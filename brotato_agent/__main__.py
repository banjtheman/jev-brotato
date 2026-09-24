import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import time

from .bridge import BridgeClient, BridgeError
from .jev import JevClient, JevError, load_env
from .policy import DIRECTIONS, INSTRUCTIONS, heuristic_choice, prepare_decision

ROOT = Path(__file__).resolve().parent.parent


def demo_state():
    return {
        "seq": 1, "phase": "combat", "wave": 1, "time_left": 12,
        "player": {"position": {"x": 600, "y": 400}, "velocity": {"x": 0, "y": 0},
                   "health": 6, "max_health": 20, "speed": 200},
        "arena": {"min": {"x": 0, "y": 0}, "max": {"x": 1200, "y": 800}},
        "enemies": [{"position": {"x": 660, "y": 400}, "velocity": {"x": -80, "y": 0}}],
        "consumables": [{"position": {"x": 460, "y": 400}}], "items": [],
    }


def play(args):
    client = JevClient(timeout=2.0) if args.policy == "jev" else None
    log_dir = ROOT / "runs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".jsonl")
    attempts = 0
    applied = 0
    latencies = []
    errors = 0
    tokens = 0
    deadline = time.monotonic() + args.seconds
    print(f"Policy: {args.policy}; limit: {args.seconds:g}s / {args.max_calls} decisions; log: {log_path}", flush=True)
    previous_phase = None
    with log_path.open("w") as log, BridgeClient(port=args.port) as bridge:
        while time.monotonic() < deadline and attempts < args.max_calls:
            iteration = time.monotonic()
            state = bridge.observe()
            phase = state.get("phase")
            if phase != previous_phase:
                print(f"Game phase: {phase}", flush=True)
                previous_phase = phase
            if phase != "combat":
                bridge.release()
                time.sleep(min(.25, max(0, deadline-time.monotonic())))
                continue
            summary, candidates = prepare_decision(state)
            attempts += 1
            record = {"timestamp": datetime.now(timezone.utc).isoformat(), "policy": args.policy,
                      "state": state, "decision_state": summary, "attempt": attempts}
            try:
                if client:
                    judgment = client.choose(summary, candidates, INSTRUCTIONS)
                    record["judgment"] = asdict(judgment)
                    choice = judgment.choice
                    latencies.append(judgment.latency_ms)
                    tokens += judgment.usage["input_tokens"]
                else:
                    choice = heuristic_choice(candidates)
                record["choice"] = choice
                # The game independently verifies sequence, age and scene.
                if time.monotonic() - iteration > .85 or time.monotonic() >= deadline:
                    record["applied"] = False
                    record["reason"] = "decision expired"
                    bridge.release()
                else:
                    bridge.move(DIRECTIONS[choice], state["seq"], ttl_ms=650)
                    applied += 1
                    errors = 0
                    record["applied"] = True
                    print(f"{attempts}: {choice}; HP {state['player']['health']}/{state['player']['max_health']}" +
                          (f"; Jev {latencies[-1]:.0f}ms" if client else ""), flush=True)
            except JevError as exc:
                errors += 1
                record.update(applied=False, error=str(exc))
                bridge.release()
                print(f"Jev request failed: {exc}", file=sys.stderr, flush=True)
            except BridgeError as exc:
                errors += 1
                record.update(applied=False, error=str(exc))
                bridge.release()
                print(f"Movement rejected: {exc}", file=sys.stderr, flush=True)
            log.write(json.dumps(record, allow_nan=False) + "\n")
            log.flush()
            if errors >= 3:
                print("Stopped after three consecutive failures.", file=sys.stderr)
                break
            time.sleep(max(0, min(.5-(time.monotonic()-iteration), deadline-time.monotonic())))
    result = {"attempts": attempts, "applied": applied, "input_tokens": tokens, "log": str(log_path)}
    if latencies:
        result["median_latency_ms"] = round(statistics.median(latencies), 1)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Experimental Jev combat agent for Brotato")
    parser.add_argument("--env", type=Path, default=ROOT/".env")
    parser.add_argument("--port", type=int, default=4243)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("observe", help="Print one live game snapshot; no API call")
    commands.add_parser("release", help="Release movement control")
    demo = commands.add_parser("demo", help="Choose a move for a synthetic state")
    demo.add_argument("--live", action="store_true", help="Make one real Jev request using TYPESAFE_API_KEY or JEV_KEY")
    run = commands.add_parser("play", help="Play combat; start a run manually in Brotato")
    run.add_argument("--seconds", type=float, default=60)
    run.add_argument("--max-calls", type=int, default=120)
    run.add_argument("--policy", choices=["jev", "heuristic"], default="jev")
    session = commands.add_parser("run", help="Record a full Jev run, including supported menus, until win/death")
    session.add_argument("--session", help="Recording folder name (letters, digits, dash, underscore)")
    session.add_argument("--seconds", type=float, default=2400)
    session.add_argument("--max-calls", type=int, default=6000)
    session.add_argument("--max-cost", type=float, default=.50, help="Stop at this estimated known input-token cost in USD")
    args = parser.parse_args()
    if args.command in ("play", "run") and (not math.isfinite(args.seconds) or args.seconds <= 0 or args.max_calls <= 0):
        parser.error("seconds and max-calls must be positive, finite limits")
    if args.command == "run" and (not math.isfinite(args.max_cost) or args.max_cost <= 0):
        parser.error("max-cost must be positive and finite")
    try:
        if args.command in ("demo", "play", "run"):
            load_env(args.env)
        if args.command == "demo":
            state, candidates = prepare_decision(demo_state())
            if args.live:
                result = asdict(JevClient().choose(state, candidates, INSTRUCTIONS))
                result["synthetic_state"] = True
            else:
                result = {"synthetic_state": True, "policy": "heuristic", "choice": heuristic_choice(candidates),
                          "note": "Add --live to test Jev credentials with one API call", "state": state}
            print(json.dumps(result, indent=2))
        elif args.command == "play":
            play(args)
        elif args.command == "run":
            from .session import run_session
            result = run_session(args)
            if result["outcome"] == "interrupted":
                return 130
            if result["outcome"] in ("runner_error", "recording_failed", "api_failures", "no_available_actions"):
                return 1
        else:
            with BridgeClient(port=args.port) as bridge:
                print(json.dumps(bridge.observe() if args.command == "observe" else bridge.release(), indent=2))
    except KeyboardInterrupt:
        print("Stopped; movement released.", file=sys.stderr)
        return 130
    except (JevError, BridgeError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, ConnectionRefusedError):
            print("Start Brotato with tools/launch_brotato.py, then run this command again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
