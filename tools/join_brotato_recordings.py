"""Join verified recording segments from one paused game, retaining raw sources.

Timestamps use the game recorder's unchanged native clock. Gaps remain in the
timeline; this never invents frames or discards the pause to improve a score.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics


COUNTERS = ("requests", "model_requests", "forced_decisions", "applied_actions",
            "input_tokens", "output_tokens", "unknown_usage_requests", "unknown_price_requests",
            "api_errors", "expired_decisions", "rejected_actions", "frames")
MATCH_FIELDS = ("model", "prompt_versions", "instruction_sha256", "timing", "max_combat_hz",
                "price_usd_per_million_input_tokens", "price_usd_per_million_output_tokens")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def join(parts, output):
    if len(parts) != 2:
        raise ValueError("Exactly two verified segments are supported")
    parts = [Path(p).resolve() for p in parts]
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Output already exists; raw recordings are never overwritten")
    metadata = [json.loads((p / "metadata.json").read_text()) for p in parts]
    summaries = [json.loads((p / "summary.json").read_text()) for p in parts]
    provenance = metadata[1].get("continuation") or {}
    if (provenance.get("session") != parts[0].name
            or provenance.get("summary_sha256") != digest(parts[0] / "summary.json")
            or provenance.get("decisions_sha256") != digest(parts[0] / "decisions.jsonl")):
        raise ValueError("Continuation provenance does not match its raw source")
    for key in MATCH_FIELDS:
        if metadata[0].get(key) != metadata[1].get(key):
            raise ValueError(f"Segments differ: {key}")
    previous_clock = provenance["previous_recording_clock"]
    if previous_clock.get("active") or Path(previous_clock.get("path", "")).name != parts[0].name:
        raise ValueError("Original recorder was not verified stopped")
    if summaries[0]["final_state"].get("phase") != "paused":
        raise ValueError("Original game was not paused")
    logs = [read_rows(p / "decisions.jsonl") for p in parts]
    first_state = next(r["state"] for r in logs[1] if r.get("event") == "phase")
    for key in ("scene_id", "wave", "time_left", "player"):
        if first_state.get(key) != summaries[0]["final_state"].get(key):
            raise ValueError(f"Different paused game: {key}")
    origins = [previous_clock["started_at_ms"], metadata[1]["recording_clock"]["started_at_ms"]]
    offsets = [0, origins[1] - origins[0]]
    if offsets[1] < summaries[0]["end_ms"]:
        raise ValueError("Recording segments overlap or use different clocks")
    for rows, summary in zip(logs, summaries):
        if sum(r.get("event") == "decision" for r in rows) != summary["requests"]:
            raise ValueError("Incomplete decision log")
    output.mkdir(parents=True)
    combined = []
    sources = []
    cumulative = {key: 0 for key in COUNTERS}
    cumulative["estimated_cost_usd"] = 0
    telemetry = []
    frames = []
    for index, (part, meta, summary, rows, offset) in enumerate(zip(parts, metadata, summaries, logs, offsets)):
        sources.append({"session": part.name, "offset_ms": offset,
                        "metadata_sha256": digest(part / "metadata.json"),
                        "summary_sha256": digest(part / "summary.json"),
                        "decisions_sha256": digest(part / "decisions.jsonl"),
                        "frames_sha256": digest(part / "frames.jsonl"),
                        "recording_started_at_ms": origins[index],
                        "max_estimated_cost_usd": meta.get("max_estimated_cost_usd")})
        for row in rows:
            row["recording_segment"] = part.name
            row["segment_elapsed_ms"] = row["elapsed_ms"]
            row["elapsed_ms"] = round(row["elapsed_ms"] + offset, 3)
            if "decision_started_ms" in row:
                row["decision_started_ms"] = round(row["decision_started_ms"] + offset, 3)
            if "attempt" in row:
                row["segment_attempt"] = row["attempt"]
                row["attempt"] += cumulative["requests"]
            for key, value in cumulative.items():
                if key in row.get("metrics", {}):
                    row["metrics"][key] += value
            combined.append(row)
        for frame in read_rows(part / "frames.jsonl"):
            source = part / frame["path"]
            target = output / "segments" / part.name / frame["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
            frame["segment_frame"] = frame["frame"]
            frame["frame"] = len(frames) + 1
            frame["segment_elapsed_ms"] = frame["elapsed_ms"]
            frame["elapsed_ms"] += offset
            frame["path"] = str(target.relative_to(output))
            frame["recording_segment"] = part.name
            frames.append(frame)
        for row in read_rows(part / "telemetry.jsonl"):
            row["recording_segment"] = part.name
            telemetry.append(row)
        for key in cumulative:
            cumulative[key] += summary.get(key, 0)
    joined_meta = dict(metadata[0], session=output.name, source_segments=sources,
                       max_estimated_cost_usd=None, derived_recording=True,
                       continuation_note=f"Paused on wave {summaries[0]['final_state'].get('wave')} to lift cost cutoff. Raw segments preserved; recent-activity history reset at continuation. Native clock gaps retained.",
                       capture_gaps=[{"start_ms": summaries[0]["end_ms"], "end_ms": offsets[1], "phase": "paused"}])
    summary = dict(summaries[-1], session=output.name, **cumulative,
                   end_ms=round(offsets[-1] + summaries[-1]["end_ms"], 3),
                   wall_duration_seconds=round((offsets[-1] + summaries[-1]["end_ms"]) / 1000, 3),
                   derived_recording=True, recording_segments=len(parts))
    latencies = [r["judgment"]["latency_ms"] for r in combined if r.get("judgment")
                 and r["judgment"].get("source", "provider") == "provider"]
    if latencies:
        summary["median_latency_ms"] = round(statistics.median(latencies), 1)
        summary["p95_latency_ms"] = round(sorted(latencies)[min(len(latencies)-1, int(len(latencies)*.95))], 1)
    for name, rows in (("decisions", combined), ("frames", frames), ("telemetry", telemetry)):
        with (output / f"{name}.jsonl").open("x") as stream:
            for row in rows:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
    for name, data in (("metadata", joined_meta), ("summary", summary)):
        (output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = join([args.first, args.second], args.output)
    print(json.dumps({k: result[k] for k in ("session", "outcome", "wave_reached", "requests", "estimated_cost_usd", "frames")}))
