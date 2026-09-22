"""A recorded run: Jev chooses combat and menu actions until a terminal state."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

from .bridge import BridgeClient, BridgeError, BridgeRejected
from .jev import JevClient, JevError
from .policy import DIRECTIONS, INSTRUCTIONS, prepare_decision

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
    def __init__(self):
        self.requests = 0
        self.applied_actions = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.unknown_usage_requests = 0
        self.latencies = []

    def add_usage(self, usage, latency=None):
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
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.input_tokens / 1_000_000 * PRICE_PER_MILLION,
            "unknown_usage_requests": self.unknown_usage_requests,
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


def run_session(args):
    slug = args.session or datetime.now().strftime("%Y%m%d-%H%M%S")
    if not slug or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in slug):
        raise ValueError("session must contain only letters, numbers, dash and underscore")
    directory = ROOT / "recordings" / slug
    directory.mkdir(parents=True, exist_ok=True)
    if (directory/"decisions.jsonl").exists():
        raise ValueError("This session already contains a run; choose a new name")
    metadata = {"session": slug, "started_at": datetime.now(timezone.utc).isoformat(),
                "difficulty": None, "character": None, "model": "jev-latest",
                "capture_interval_ms": 250, "price_usd_per_million_input_tokens": PRICE_PER_MILLION,
                "output_tokens_free": True, "price_source": "https://docs.typesafe.ai/models",
                "price_checked_at": "2026-09-22", "max_seconds": args.seconds,
                "max_calls": args.max_calls, "max_estimated_cost_usd": args.max_cost,
                "note": "Gameplay frames only. Setup selected manually; Jev chooses combat and offered menu actions."}
    (directory/"metadata.json").write_text(json.dumps(metadata, indent=2))
    client = JevClient(timeout=2.0)
    accounting = Accounting()
    outcome = "stopped"
    state = {}
    wave_reached = 0
    failed = expired = rejected = 0
    consecutive_errors = 0
    previous_phase = None
    previous_choice = None
    menu_count = 0
    start = time.monotonic()
    last_progress = start
    last_status_check = start
    recording = {}
    clock_origin = start
    print(f"Recording session {slug}; max {args.seconds:g}s, {args.max_calls} Jev calls, ${args.max_cost:.2f} estimated input cost", flush=True)
    try:
        with (directory/"decisions.jsonl").open("w") as log, BridgeClient(port=args.port) as bridge:
            recording = bridge.request("record_start", session=slug, interval_ms=250)
            clock_origin = time.monotonic() - (recording["now_ms"]-recording["started_at_ms"])/1000
            start = clock_origin

            def write(record):
                record.update(elapsed_ms=round((time.monotonic()-clock_origin)*1000, 1),
                              metrics=accounting.snapshot())
                log.write(json.dumps(record, allow_nan=False) + "\n")
                log.flush()

            try:
                while True:
                    now = time.monotonic()
                    if now-start >= args.seconds:
                        outcome = "time_limit"
                        break
                    if accounting.requests >= args.max_calls:
                        outcome = "request_limit"
                        break
                    if accounting.snapshot()["estimated_cost_usd"] >= args.max_cost:
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
                        context, candidates = prepare_decision(state)
                        context["previous_action"] = previous_choice
                        instructions = INSTRUCTIONS
                    else:
                        bridge.release()
                        context, candidates = menu_decision(state, menu_count)
                        instructions = MENU_INSTRUCTIONS
                    if not candidates:
                        if now-last_progress > 120:
                            outcome = "no_available_actions"
                            break
                        time.sleep(.25)
                        continue
                    accounting.requests += 1
                    record = {"event": "decision", "attempt": accounting.requests,
                              "decision_started_ms": round((iteration-clock_origin)*1000, 1),
                              "phase": phase, "state": state, "decision_state": context,
                              "candidates": candidates, "applied": False}
                    try:
                        judgment = client.choose(context, candidates, instructions)
                        accounting.add_usage(judgment.usage, judgment.latency_ms)
                        record.update(judgment=asdict(judgment), choice=judgment.choice)
                        metadata["model"] = judgment.model
                        if phase == "combat" and time.monotonic()-iteration > .85:
                            expired += 1
                            record["reason"] = "decision expired"
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
                            last_progress = time.monotonic()
                            if phase != "combat" or accounting.requests % 10 == 0:
                                hp = (state.get("player") or {}).get("health", "—")
                                print(f"{accounting.requests}: {phase} {judgment.choice} | HP {hp} | {judgment.latency_ms:.0f}ms | ${accounting.snapshot()['estimated_cost_usd']:.5f}", flush=True)
                        consecutive_errors = 0
                    except JevError as error:
                        accounting.add_usage(getattr(error, "usage", None), getattr(error, "latency_ms", None))
                        failed += 1
                        consecutive_errors += 1
                        record["error"] = str(error)
                        if getattr(error, "answer", None) is not None:
                            record["invalid_answer"] = error.answer
                        bridge.release()
                        print(f"Jev error: {error}", flush=True)
                    except BridgeRejected as error:
                        rejected += 1
                        record["error"] = str(error)
                        # End-of-wave and menu state changes are expected races.
                        bridge.release()
                        print(f"Action rejected: {error}", flush=True)
                    write(record)
                    if consecutive_errors >= 5:
                        outcome = "api_failures"
                        break
                    delay = .2 if phase == "combat" else .3
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
        client.close()
        if recording.get("active"):
            try:
                with BridgeClient(port=args.port) as recovery:
                    recording = recovery.request("record_stop")
            except (BridgeError, OSError):
                pass
        summary = {"session": slug, "outcome": outcome, "wave_reached": wave_reached,
                   "wall_duration_seconds": round(time.monotonic()-start, 3),
                   "end_ms": round((time.monotonic()-clock_origin)*1000, 1),
                   **accounting.snapshot(), "api_errors": failed, "expired_decisions": expired,
                   "rejected_actions": rejected, "frames": recording.get("frames", 0),
                   "final_state": state}
        if accounting.latencies:
            summary["median_latency_ms"] = round(statistics.median(accounting.latencies), 1)
            summary["p95_latency_ms"] = round(sorted(accounting.latencies)[min(len(accounting.latencies)-1, int(len(accounting.latencies)*.95))], 1)
        (directory/"summary.json").write_text(json.dumps(summary, indent=2))
        (directory/"metadata.json").write_text(json.dumps(metadata, indent=2))
        print(json.dumps({k:v for k,v in summary.items() if k != "final_state"}, indent=2), flush=True)
        print(f"Session saved: {directory}", flush=True)
    return summary
