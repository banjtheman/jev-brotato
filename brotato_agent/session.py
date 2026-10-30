"""A recorded run: a configured model chooses combat and supported menu actions."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import statistics
import time

from .bridge import BridgeClient, BridgeError, BridgeRejected
from .jev import JevClient, JevError, MODEL as JEV_REQUESTED_MODEL
from .policy import DIRECTIONS, INSTRUCTIONS, prepare_decision
from .combat import CombatHistory, combat_metadata, prepare_combat
from .shop import prepare_shop, profile_metadata

ROOT = Path(__file__).resolve().parent.parent
PRICE_PER_MILLION = 0.042  # https://docs.typesafe.ai/models, checked 2026-09-22
MENU_INSTRUCTIONS = (
    "Choose the next legal Brotato menu action from the criteria to maximize the "
    "chance of surviving all 20 waves. The observed `build` and offered items are "
    "authoritative. Build around the character and existing weapon damage types. "
    "Early in a run prioritize filling weapon slots with compatible weapons, then "
    "damage, attack speed, some harvesting, and enough HP/armor/healing to survive. "
    "Avoid spending on unrelated stats. Evaluate item tradeoffs using their supplied "
    "descriptions. Prefer buying a useful affordable offer before leaving the shop. "
    "Reroll only if it is worth the remaining gold; next_wave is valid when none of "
    "the affordable offers improve this build. For upgrades pick the strongest "
    "relevant stat; for crates take useful items and recycle harmful ones."
)


class Accounting:
    def __init__(self, input_price=PRICE_PER_MILLION, output_price=0.0):
        self.requests = 0
        self.model_requests = 0
        self.forced_decisions = 0
        self.applied_actions = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.unknown_usage_requests = 0
        self.latencies = []
        self.input_price = input_price
        self.output_price = output_price

    def add_usage(self, usage, latency=None, *, billable=True):
        if not billable:
            return
        if usage is None:
            self.unknown_usage_requests += 1
        else:
            self.input_tokens += usage["input_tokens"]
            self.output_tokens += usage["output_tokens"]
        if latency is not None:
            self.latencies.append(latency)

    def snapshot(self):
        return {
            "requests": self.requests, "applied_actions": self.applied_actions,
            "model_requests": self.model_requests, "forced_decisions": self.forced_decisions,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "estimated_cost_usd": (self.input_tokens * (self.input_price or 0.0)
                                   + self.output_tokens * (self.output_price or 0.0)) / 1_000_000,
            "unknown_usage_requests": self.unknown_usage_requests,
            "unknown_price_requests": self.model_requests if self.input_price is None else 0,
        }


def menu_decision(state, menu_action_count):
    candidates = dict((state.get("menu") or {}).get("options") or {})
    # A hard cap prevents an accidental infinite spending/reroll loop. Option
    # legality and exact prices remain the game's responsibility.
    if menu_action_count >= 24:
        advance = {k: v for k, v in candidates.items()
                   if v.get("kind") in ("next_wave", "continue") or k == "next_wave"}
        if advance:
            candidates = advance
    context = {"game": "Brotato", "phase": state["phase"], "wave": state.get("wave"),
               "build": state.get("build"), "menu_actions_this_wave": menu_action_count,
               "goal": "Survive all 20 waves; maximize useful upgrades with the current gold."}
    menu = state.get("menu") or {}
    for field in ("reroll_budget", "shop"):
        if field in menu:
            context[field] = menu[field]
    return context, candidates


def record_observed_setup(metadata, state):
    """Fill setup metadata only from fields actually exposed by the bridge."""
    build = state.get("build")
    if not isinstance(build, dict):
        build = {}
    character = build.get("character")
    name = character.get("name") if isinstance(character, dict) else character
    if metadata.get("character") is None and isinstance(name, str) and name.strip():
        metadata["character"] = name
    difficulty = state.get("difficulty", build.get("difficulty"))
    if metadata.get("difficulty") is None and type(difficulty) in (int, float) and 0 <= difficulty <= 5 and int(difficulty) == difficulty:
        metadata["difficulty"] = int(difficulty)


def validate_continuation(source, state):
    """Refuse to join a different game or an episode whose state changed."""
    previous = source["final_state"]
    if previous.get("phase") != "paused" or state.get("phase") != "paused":
        raise ValueError("Continuation requires the same game to remain paused")
    for key in ("scene_id", "wave", "time_left", "player"):
        if previous.get(key) is None or state.get(key) != previous[key]:
            raise ValueError(f"Paused game changed: {key}; refusing continuation")


def continuation_source(slug):
    if not slug or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in slug):
        raise ValueError("Invalid continuation recording name")
    directory = ROOT / "recordings" / slug
    summary = json.loads((directory / "summary.json").read_text())
    if summary.get("outcome") in ("gameover", "victory"):
        raise ValueError("Cannot continue a completed episode")
    metadata = json.loads((directory / "metadata.json").read_text())
    provenance = {"session": slug, "summary_sha256": hashlib.sha256((directory / "summary.json").read_bytes()).hexdigest(),
                  "decisions_sha256": hashlib.sha256((directory / "decisions.jsonl").read_bytes()).hexdigest(),
                  "note": "Same game paused for cost-limit change; separate raw recording segments. Recent-activity history restarts."}
    return summary, metadata, provenance


def run_session(args):
    capture_interval_ms = getattr(args, "capture_interval_ms", 250)
    shop_profile = getattr(args, "shop_profile", "baseline")
    combat_profile = getattr(args, "combat_profile", "baseline")
    combat_setup = combat_metadata(combat_profile)
    setup = profile_metadata(shop_profile, MENU_INSTRUCTIONS, INSTRUCTIONS)
    setup.update(combat_setup)
    setup["prompt_versions"]["combat"] = combat_setup["combat_prompt_version"]
    setup["instruction_sha256"]["combat"] = combat_setup["combat_instruction_sha256"]
    combat_history = CombatHistory()
    slug = args.session or datetime.now().strftime("%Y%m%d-%H%M%S")
    if not slug or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in slug):
        raise ValueError("session must contain only letters, numbers, dash and underscore")
    directory = ROOT / "recordings" / slug
    directory.mkdir(parents=True, exist_ok=True)
    if (directory/"decisions.jsonl").exists():
        raise ValueError("This session already contains a run; choose a new name")
    continued = continuation_source(args.continue_from) if getattr(args, "continue_from", None) else None
    if continued and (continued[1].get("prompt_versions") != setup["prompt_versions"]
                      or continued[1].get("instruction_sha256") != setup["instruction_sha256"]):
        raise ValueError("Continuation must retain the recorded prompt profiles")
    telemetry = None
    timing = getattr(args, "timing", None) or ("native" if getattr(args, "backend", None) else "shared")
    max_rate = getattr(args, "max_rate", 20.0)
    if getattr(args, "backend", None):
        from .decision_models import build_client
        telemetry = build_client(args, directory)
        client = telemetry
    else:
        client = JevClient(timeout=2.0)
    menu_llm = getattr(args, "menu_llm", None)
    menu_llm = menu_llm if isinstance(menu_llm, str) and menu_llm else None
    if menu_llm:
        if telemetry is not None:
            client.close()
            raise ValueError("--menu-llm runs with the original Jev client; omit --backend")
        from .llm_menu import CodexMenuClient, HybridClient
        client = HybridClient(client, CodexMenuClient(model=menu_llm, effort=getattr(args, "menu_effort", "low"),
                                                      codex_bin=getattr(args, "codex_bin", "codex")))
    metadata = {"session": slug, "started_at": datetime.now(timezone.utc).isoformat(), **setup,
                "difficulty": None, "character": None, "model": "jev-latest",
                "requested_model": JEV_REQUESTED_MODEL if telemetry is None else None,
                "capture_interval_ms": capture_interval_ms, "price_usd_per_million_input_tokens": PRICE_PER_MILLION,
                "output_tokens_free": True, "price_source": "https://docs.typesafe.ai/models",
                "price_checked_at": "2026-09-22", "max_seconds": args.seconds,
                "max_calls": args.max_calls, "max_estimated_cost_usd": args.max_cost,
                "timing": timing, "max_combat_hz": max_rate if timing == "native" else 5.0,
                "movement_lease_ms": 500, "combat_deadline_ms": 850,
                "note": "Gameplay frames only. Setup selected manually; model chooses combat and offered menu actions. Trials are unpaired."}
    if menu_llm:
        metadata.update(menu_model=menu_llm, menu_reasoning_effort=getattr(args, "menu_effort", "low"),
                        menu_transport="codex-cli",
                        note="Hybrid: Jev chooses combat movement; the menu LLM chooses shop, level-up and crate actions "
                             "from the same legal options. Setup selected manually. Trials are unpaired.")
    if telemetry is not None:
        info = telemetry.harness.backend.info
        price = info.pricing
        metadata.update(backend=info.to_dict(), model=info.requested_model, requested_model=info.requested_model,
                        price_usd_per_million_input_tokens=price.input_per_million_usd if price else None,
                        price_usd_per_million_output_tokens=price.output_per_million_usd if price else None,
                        output_tokens_free=price.output_per_million_usd == 0 if price else None,
                        price_source=price.source if price else None,
                        price_checked_at=price.checked_at if price else None)
        accounting = Accounting(price.input_per_million_usd if price else None,
                                price.output_per_million_usd if price else None)
    else:
        accounting = Accounting()
    if continued:
        for key in ("model", "timing", "max_combat_hz"):
            if continued[1].get(key) != metadata.get(key):
                client.close()
                raise ValueError(f"Continuation must retain {key}")
        metadata["continuation"] = continued[2]
    (directory/"metadata.json").write_text(json.dumps(metadata, indent=2))
    outcome = "stopped"
    state = {}
    wave_reached = 0
    failed = expired = rejected = 0
    consecutive_errors = 0
    previous_phase = None
    previous_choice = None
    if continued:
        with (ROOT / "recordings" / args.continue_from / "decisions.jsonl").open() as source_log:
            for line in source_log:
                row = json.loads(line)
                if row.get("phase") == "combat" and row.get("applied"):
                    previous_choice = row.get("choice")
    menu_count = 0
    start = time.monotonic()
    last_progress = start
    last_status_check = start
    recording = {}
    clock_origin = start
    cost_label = f"${args.max_cost:.2f} known API cost" if args.max_cost is not None else "no cost cutoff (cost still recorded)"
    print(f"Recording session {slug}; model {metadata['model']}; {timing} timing; max {args.seconds:g}s, {args.max_calls} decisions, {cost_label}", flush=True)
    try:
        with (directory/"decisions.jsonl").open("w") as log, BridgeClient(port=args.port) as bridge:
            if continued:
                validate_continuation(continued[0], bridge.observe())
                previous_recording = bridge.request("record_status")
                if previous_recording.get("active") or Path(previous_recording.get("path", "")).name != args.continue_from:
                    raise ValueError("Continuation requires the previous recorder to be stopped in this game instance")
                metadata["continuation"]["previous_recording_clock"] = previous_recording
            recording = bridge.request("record_start", session=slug, interval_ms=capture_interval_ms)
            metadata["recording_clock"] = {key: recording[key] for key in ("started_at_ms", "now_ms")}
            clock_origin = time.monotonic() - (recording["now_ms"]-recording["started_at_ms"])/1000
            start = clock_origin

            def write(record):
                record.update(elapsed_ms=round((time.monotonic()-clock_origin)*1000, 1),
                              metrics=accounting.snapshot())
                log.write(json.dumps(record, allow_nan=False) + "\n")
                log.flush()

            if continued:
                write({"event": "continuation", "phase": "paused", "source": continued[2], "applied": False})

            try:
                while True:
                    now = time.monotonic()
                    if now-start >= args.seconds:
                        outcome = "time_limit"
                        break
                    if accounting.requests >= args.max_calls:
                        outcome = "request_limit"
                        break
                    if args.max_cost is not None and accounting.snapshot()["estimated_cost_usd"] >= args.max_cost:
                        outcome = "cost_limit"
                        break
                    if now-last_status_check > 5:
                        recording = bridge.request("record_status")
                        last_status_check = now
                        if not recording.get("active") or recording.get("recording_error"):
                            outcome = "recording_failed"
                            break
                    iteration = time.monotonic()
                    state = bridge.observe()
                    record_observed_setup(metadata, state)
                    phase = state.get("phase", "menu")
                    wave_reached = max(wave_reached, int(state.get("wave") or 0))
                    if phase != previous_phase:
                        write({"event": "phase", "phase": phase, "state": state, "applied": False})
                        print(f"Phase {phase} | wave {state.get('wave')} | {accounting.requests} calls", flush=True)
                        if phase == "combat":
                            menu_count = 0
                        last_progress = now
                        previous_phase = phase
                    if phase in ("gameover", "victory") or state.get("terminal"):
                        outcome = phase
                        bridge.release()
                        time.sleep(6 if phase == "victory" else 3)
                        break
                    if phase == "combat":
                        if combat_profile == "combat-v2" and state.get("combat_observation_version") != "combat-obs-v2":
                            raise ValueError("combat-v2 requires the updated bridge; restart Brotato with tools/launch_brotato.py")
                        context, candidates, instructions = prepare_combat(state, combat_profile, combat_history)
                        context["previous_action"] = previous_choice
                    else:
                        combat_history.reset()
                        bridge.release()
                        context, candidates = menu_decision(state, menu_count)
                        instructions = MENU_INSTRUCTIONS
                    context, model_options, instructions = prepare_shop(context, candidates, instructions, shop_profile)
                    if not candidates:
                        if phase == "paused":
                            last_progress = now
                        if now-last_progress > 120:
                            outcome = "no_available_actions"
                            break
                        time.sleep(.25)
                        continue
                    accounting.requests += 1
                    if telemetry is not None and len(candidates) == 1:
                        accounting.forced_decisions += 1
                    else:
                        accounting.model_requests += 1
                    record = {"event": "decision", "attempt": accounting.requests,
                              "decision_started_ms": round((iteration-clock_origin)*1000, 1),
                              "phase": phase, "state": state, "decision_state": context,
                              "candidates": candidates, "instructions": instructions,
                              "model_options": model_options,
                              "prompt_version": setup["prompt_versions"]["combat" if phase == "combat" else "shop" if phase == "shop" else "menu"],
                              "candidate_order": list(candidates), "applied": False}
                    usage_accounted = False
                    try:
                        if telemetry is not None:
                            telemetry.begin(state, iteration)
                        judgment = client.choose(context, model_options, instructions)
                        accounting.add_usage(judgment.usage, judgment.latency_ms,
                                             billable=getattr(judgment, "source", "provider") == "provider")
                        usage_accounted = True
                        record.update(judgment=asdict(judgment), choice=judgment.choice)
                        if judgment.model is not None:
                            metadata["model"] = judgment.model
                        if phase == "combat" and time.monotonic()-iteration > .85:
                            expired += 1
                            record["reason"] = "decision expired"
                            if telemetry is not None:
                                telemetry.complete("expired", "combat_deadline")
                            bridge.release()
                        else:
                            if phase == "combat":
                                bridge.move(DIRECTIONS[judgment.choice], state["seq"], ttl_ms=500)
                                previous_choice = judgment.choice
                            else:
                                bridge.request("act", observation_seq=state["seq"], action=judgment.choice)
                                menu_count += 1
                            accounting.applied_actions += 1
                            record["applied"] = True
                            if telemetry is not None:
                                telemetry.complete("applied")
                            last_progress = time.monotonic()
                            if phase != "combat" or accounting.requests % 10 == 0:
                                hp = (state.get("player") or {}).get("health", "—")
                                print(f"{accounting.requests}: {phase} {judgment.choice} | HP {hp} | {judgment.latency_ms:.0f}ms | ${accounting.snapshot()['estimated_cost_usd']:.5f}", flush=True)
                                if getattr(judgment, "reason", None):
                                    print(f"    why: {judgment.reason}", flush=True)
                                if getattr(judgment, "runner_up", None):
                                    print(f"    passed on: {judgment.runner_up}", flush=True)
                                if getattr(judgment, "plan", None) and judgment.plan != getattr(client, "last_plan", None):
                                    print(f"    plan: {judgment.plan}", flush=True)
                                    client.last_plan = judgment.plan
                        consecutive_errors = 0
                    except JevError as error:
                        attempted = getattr(error, "request_attempted", None) is not False
                        if not attempted:
                            accounting.model_requests -= 1
                        accounting.add_usage(getattr(error, "usage", None), getattr(error, "latency_ms", None),
                                             billable=attempted and getattr(error, "source", "provider") == "provider")
                        failed += 1
                        consecutive_errors += 1
                        record["error"] = str(error)
                        if getattr(error, "answer", None) is not None:
                            record["invalid_answer"] = error.answer
                        bridge.release()
                        print(f"Decision error: {error}", flush=True)
                    except BridgeRejected as error:
                        rejected += 1
                        record["error"] = str(error)
                        if telemetry is not None:
                            telemetry.complete("rejected", "game_bridge_rejected")
                        # End-of-wave and menu state changes are expected races.
                        bridge.release()
                        print(f"Action rejected: {error}", flush=True)
                    except KeyboardInterrupt:
                        if not usage_accounted:
                            accounting.add_usage(None, billable=not (telemetry is not None and len(candidates) == 1))
                        record["error"] = "interrupted"
                        write(record)
                        raise
                    write(record)
                    if consecutive_errors >= 5:
                        outcome = "api_failures"
                        break
                    delay = (1 / max_rate if timing == "native" else .2) if phase == "combat" else .3
                    time.sleep(max(0, delay-(time.monotonic()-iteration)))
            finally:
                try:
                    bridge.release()
                    recording = bridge.request("record_stop")
                except (BridgeError, OSError):
                    pass
    except KeyboardInterrupt:
        outcome = "interrupted"
    except (BridgeError, OSError, ValueError) as error:
        outcome = "runner_error"
        print(f"Run stopped: {error}", flush=True)
    finally:
        if telemetry is None:
            client.close()
        if recording.get("active"):
            try:
                with BridgeClient(port=args.port) as recovery:
                    recording = recovery.request("record_stop")
            except (BridgeError, OSError):
                pass
        summary = {"session": slug, "outcome": outcome, "wave_reached": wave_reached,
                   "shop_profile": shop_profile,
                   "combat_profile": combat_profile,
                   "wall_duration_seconds": round(time.monotonic()-start, 3),
                   "end_ms": round((time.monotonic()-clock_origin)*1000, 1),
                   **accounting.snapshot(), "api_errors": failed, "expired_decisions": expired,
                   "rejected_actions": rejected, "frames": recording.get("frames", 0),
                   "final_state": state}
        if menu_llm:
            summary["menu_llm"] = client.menu_summary()
        if accounting.latencies:
            summary["median_latency_ms"] = round(statistics.median(accounting.latencies), 1)
            summary["p95_latency_ms"] = round(sorted(accounting.latencies)[min(len(accounting.latencies)-1, int(len(accounting.latencies)*.95))], 1)
        if telemetry is not None:
            try:
                telemetry.complete("unknown", "run_stopped_before_action_result")
                telemetry.finish(summary)
            finally:
                telemetry.close()
        (directory/"summary.json").write_text(json.dumps(summary, indent=2))
        (directory/"metadata.json").write_text(json.dumps(metadata, indent=2))
        print(json.dumps({k:v for k,v in summary.items() if k != "final_state"}, indent=2), flush=True)
        print(f"Session saved: {directory}", flush=True)
    return summary
