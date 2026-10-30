import json
from pathlib import Path
import tempfile
import unittest

from tools.join_brotato_recordings import digest, join, read_rows


class JoinRecordingsTests(unittest.TestCase):
    def fixture(self, root):
        first, second = root / "first", root / "second"
        state = {"phase": "paused", "scene_id": 12, "wave": 15, "time_left": 31,
                 "player": {"health": 40, "position": {"x": 2, "y": 3}}}
        for directory, outcome, cost in ((first, "no_available_actions", .4), (second, "gameover", .1)):
            (directory / "frames").mkdir(parents=True)
            (directory / "frames/a.png").write_bytes(b"fixture frame")
            meta = {"session": directory.name, "model": "jev", "prompt_versions": {"combat": "v2"},
                    "instruction_sha256": {"combat": "abc"}, "timing": "native", "max_combat_hz": 20}
            summary = {"session": directory.name, "outcome": outcome, "wave_reached": 15,
                       "end_ms": 1000, "requests": 1, "model_requests": 1, "applied_actions": 1,
                       "input_tokens": 100, "output_tokens": 5, "estimated_cost_usd": cost,
                       "frames": 1, "final_state": state}
            rows = [{"event": "phase", "elapsed_ms": 0, "phase": "paused", "state": state},
                    {"event": "decision", "elapsed_ms": 500, "decision_started_ms": 400, "attempt": 1,
                     "judgment": {"choice": "east", "source": "provider", "latency_ms": 100},
                     "metrics": {"requests": 1, "estimated_cost_usd": cost}}]
            (directory / "metadata.json").write_text(json.dumps(meta))
            (directory / "summary.json").write_text(json.dumps(summary))
            (directory / "decisions.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
            (directory / "frames.jsonl").write_text(json.dumps({"frame": 1, "path": "frames/a.png", "elapsed_ms": 100})+"\n")
            (directory / "telemetry.jsonl").write_text(json.dumps({"event": "decision", "decision_id": directory.name})+"\n")
        meta = json.loads((second / "metadata.json").read_text())
        meta.update(recording_clock={"started_at_ms": 2000}, continuation={"session": "first",
                    "summary_sha256": digest(first / "summary.json"),
                    "decisions_sha256": digest(first / "decisions.jsonl"),
                    "previous_recording_clock": {"active": False, "path": str(first), "started_at_ms": 100}})
        (second / "metadata.json").write_text(json.dumps(meta))
        return first, second

    def test_keeps_clock_gap_counters_and_original_files(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            parts = self.fixture(root)
            originals = {p: p.read_bytes() for folder in parts for p in folder.rglob("*") if p.is_file()}
            result = join(parts, root / "joined")
            self.assertEqual(result["requests"], 2)
            self.assertEqual(result["end_ms"], 2900)
            self.assertEqual(result["estimated_cost_usd"], .5)
            rows = read_rows(root / "joined/decisions.jsonl")
            self.assertEqual(rows[-1]["elapsed_ms"], 2400)
            self.assertEqual(rows[-1]["decision_started_ms"], 2300)
            self.assertEqual(rows[-1]["attempt"], 2)
            self.assertEqual(rows[-1]["segment_attempt"], 1)
            self.assertEqual(rows[-1]["metrics"]["requests"], 2)
            self.assertEqual(rows[-1]["metrics"]["estimated_cost_usd"], .5)
            frames = read_rows(root / "joined/frames.jsonl")
            self.assertEqual([f["elapsed_ms"] for f in frames], [100, 2000])
            self.assertTrue(all((root / "joined" / f["path"]).is_file() for f in frames))
            for p, value in originals.items():
                self.assertEqual(p.read_bytes(), value)

    def test_rejects_altered_source_before_writing_output(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            parts = self.fixture(root)
            (parts[0] / "decisions.jsonl").write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "provenance"):
                join(parts, root / "joined")
            self.assertFalse((root / "joined").exists())

    def test_rejects_native_clock_overlap(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            parts = self.fixture(root)
            meta = json.loads((parts[1] / "metadata.json").read_text())
            meta["recording_clock"]["started_at_ms"] = 200
            (parts[1] / "metadata.json").write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "overlap"):
                join(parts, root / "joined")
            self.assertFalse((root / "joined").exists())
