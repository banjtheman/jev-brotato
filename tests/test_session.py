from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from brotato_agent.jev import JevResponseError, Judgment
from brotato_agent.session import Accounting, menu_decision, record_observed_setup, run_session, validate_continuation


class SessionTests(unittest.TestCase):
    def test_continuation_requires_identical_paused_game(self):
        state = {"phase": "paused", "scene_id": 123, "wave": 15,
                 "time_left": 31.5, "player": {"health": 40, "position": {"x": 10, "y": 20}}}
        validate_continuation({"final_state": state}, dict(state))
        for key, value in (("scene_id", 456), ("wave", 1), ("phase", "combat"),
                           ("time_left", 30), ("player", {"health": 55})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_continuation({"final_state": state}, {**state, key: value})

    def test_disabled_cost_limit_keeps_accounting_and_other_limits(self):
        for cost_limit, count, outcome in ((.01, 1, "cost_limit"), (None, 2, "request_limit")):
            with self.subTest(cost_limit=cost_limit), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                bridge = MagicMock()
                bridge.observe.return_value = {"seq": 1, "phase": "upgrade", "wave": 1, "menu": {"options": {"upgrade_0": {}}}}
                bridge.request.side_effect = lambda command, **kw: {"now_ms": 0, "started_at_ms": 0, "active": command == "record_start", "frames": 0}
                client = MagicMock()
                client.choose.return_value = Judgment("upgrade_0", 1, {"upgrade_0": 1}, 10, "jev", {"input_tokens": 500000, "output_tokens": 5})
                stack.enter_context(patch("brotato_agent.session.ROOT", Path(directory)))
                factory = stack.enter_context(patch("brotato_agent.session.BridgeClient"))
                factory.return_value.__enter__.return_value = bridge
                stack.enter_context(patch("brotato_agent.session.JevClient", return_value=client))
                stack.enter_context(patch("brotato_agent.session.time.sleep"))
                stack.enter_context(patch("builtins.print"))
                args = SimpleNamespace(session="cost-test", seconds=60, max_calls=2, max_cost=cost_limit, port=4243)
                result = run_session(args)
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["requests"], count)
                self.assertAlmostEqual(result["estimated_cost_usd"], .021 * count)

    def test_interrupted_request_retains_prepared_input(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            bridge = MagicMock()
            bridge.observe.return_value = {"seq": 1, "phase": "upgrade", "wave": 1, "menu": {"options": {"upgrade_0": {}}}}
            bridge.request.side_effect = lambda command, **kw: {"now_ms": 0, "started_at_ms": 0, "active": command == "record_start", "frames": 0}
            client = MagicMock()
            client.choose.side_effect = KeyboardInterrupt
            stack.enter_context(patch("brotato_agent.session.ROOT", Path(directory)))
            factory = stack.enter_context(patch("brotato_agent.session.BridgeClient"))
            factory.return_value.__enter__.return_value = bridge
            stack.enter_context(patch("brotato_agent.session.JevClient", return_value=client))
            stack.enter_context(patch("builtins.print"))
            args = SimpleNamespace(session="interrupt-test", seconds=60, max_calls=1, max_cost=None, port=4243)
            result = run_session(args)
            rows = [json.loads(line) for line in (Path(directory) / "recordings" / args.session / "decisions.jsonl").read_text().splitlines()]
            decision = next(r for r in rows if r["event"] == "decision")
            self.assertEqual(result["outcome"], "interrupted")
            self.assertEqual(result["requests"], 1)
            self.assertEqual(result["unknown_usage_requests"], 1)
            self.assertEqual(decision["error"], "interrupted")
            self.assertEqual(decision["decision_state"]["phase"], "upgrade")
            self.assertEqual(set(decision["model_options"]), {"upgrade_0"})
            self.assertFalse(decision["applied"])
            bridge.move.assert_not_called()

    def test_usage_tracks_invalid_responses_and_unknown_requests_separately(self):
        accounting = Accounting()
        accounting.add_usage({"input_tokens": 1_000_000, "output_tokens": 1000}, 120)
        accounting.add_usage(None)
        result = accounting.snapshot()
        self.assertEqual(result["estimated_cost_usd"], .042)
        self.assertEqual(result["unknown_usage_requests"], 1)
        self.assertEqual(result["output_tokens"], 1000)

    def test_menu_spending_loop_cap_still_permits_advancing(self):
        state = {"phase": "shop", "menu": {"options": {
            "buy_0": {"kind": "buy_item"}, "reroll": {"kind": "reroll"},
            "next_wave": {"kind": "next_wave"}}}}
        _, options = menu_decision(state, 24)
        self.assertEqual(set(options), {"next_wave"})
        self.assertEqual(len(state["menu"]["options"]), 3)

    def test_upgrades_are_not_removed_by_shop_cap(self):
        state = {"phase": "upgrade", "menu": {"options": {"upgrade_0": {"kind": "upgrade"}}}}
        _, options = menu_decision(state, 25)
        self.assertEqual(set(options), {"upgrade_0"})

    def test_rejected_answer_diagnostics_are_logged_without_applying_action(self):
        error = JevResponseError("Jev choice does not match its highest probability.")
        error.answer = {"type": "choice", "choice": "upgrade_0", "confidence": 0.7,
                        "probabilities": {"upgrade_0": 0.1, "upgrade_1": 0.9}}
        error.usage = {"input_tokens": 125, "output_tokens": 20}
        error.latency_ms = 50
        bridge = MagicMock()
        bridge.observe.return_value = {"phase": "upgrade", "wave": 1,
                                       "build": {"character": {"name": "Ranger"}},
                                       "menu": {"options": {"upgrade_0": {}, "upgrade_1": {}}}}
        bridge.request.side_effect = [
            {"now_ms": 0, "started_at_ms": 0, "active": True},
            {"active": False, "frames": 0},
        ]
        client = MagicMock()
        client.choose.side_effect = error
        args = SimpleNamespace(session="diagnostics-test", seconds=60, max_calls=1, max_cost=1, port=4243)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch("brotato_agent.session.ROOT", Path(directory)))
            bridge_factory = stack.enter_context(patch("brotato_agent.session.BridgeClient"))
            bridge_factory.return_value.__enter__.return_value = bridge
            stack.enter_context(patch("brotato_agent.session.JevClient", return_value=client))
            stack.enter_context(patch("brotato_agent.session.time.sleep"))
            stack.enter_context(patch("builtins.print"))
            summary = run_session(args)
            records = [json.loads(line) for line in (
                Path(directory) / "recordings" / args.session / "decisions.jsonl"
            ).read_text().splitlines()]
            metadata = json.loads((Path(directory) / "recordings" / args.session / "metadata.json").read_text())
        decision = next(record for record in records if record["event"] == "decision")
        self.assertEqual(decision["invalid_answer"], error.answer)
        self.assertFalse(decision["applied"])
        self.assertNotIn("judgment", decision)
        self.assertEqual(summary["input_tokens"], 125)
        self.assertEqual(summary["api_errors"], 1)
        self.assertEqual(metadata["character"], "Ranger")
        self.assertIsNone(metadata["difficulty"])
        bridge.move.assert_not_called()
        self.assertFalse(any(call.args[0] == "act" for call in bridge.request.call_args_list))

    def test_setup_metadata_stays_unknown_until_observed(self):
        metadata = {"character": None, "difficulty": None}
        record_observed_setup(metadata, {})
        self.assertEqual(metadata, {"character": None, "difficulty": None})
        record_observed_setup(metadata, {"build": {"character": {"name": "Mage"}}})
        self.assertEqual(metadata, {"character": "Mage", "difficulty": None})
        record_observed_setup(metadata, {"difficulty": 3})
        self.assertEqual(metadata, {"character": "Mage", "difficulty": 3})


if __name__ == "__main__":
    unittest.main()
