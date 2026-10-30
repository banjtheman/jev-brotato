"""Exercise the optional library against the recorded Brotato loop, without a game."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from brotato_agent.session import Accounting, run_session


@unittest.skipIf(importlib.util.find_spec("decision_metrics") is None, "Optional decision-metrics package is not on PYTHONPATH")
class BenchmarkSessionTests(unittest.TestCase):
    def run_fixture(self, options, *, choice="upgrade_0", rejected=False, interrupted=False, timing="native", backend=None):
        from decision_metrics import BackendInfo, DecisionResult, Pricing, Usage
        if backend is None:
            backend = MagicMock()
            backend.info = BackendInfo("test-provider", "test-v1", "offline-test", pricing=Pricing(.24, .1))
            backend.choose.return_value = DecisionResult(choice, model="test-v1", usage=Usage(1000, 50))
        if interrupted:
            backend.choose.side_effect = KeyboardInterrupt
        bridge = MagicMock()
        bridge.observe.return_value = {"seq": 1, "phase": "upgrade", "wave": 1,
                                       "build": {"character": {"name": "Ranger"}},
                                       "menu": {"options": options}}
        def bridge_request(command, **kwargs):
            if command == "record_start":
                return {"now_ms": 0, "started_at_ms": 0, "active": True}
            if command == "act" and rejected:
                from brotato_agent.bridge import BridgeRejected
                raise BridgeRejected("menu_changed")
            return {"active": False, "frames": 0, "ok": True}
        bridge.request.side_effect = bridge_request
        args = SimpleNamespace(session="library-test", seconds=60, max_calls=1, max_cost=1,
                               port=4243, backend="test", backend_config=None,
                               timing=timing, max_rate=20)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            recording = root / "recordings" / args.session
            stack.enter_context(patch("brotato_agent.session.ROOT", root))
            bridge_factory = stack.enter_context(patch("brotato_agent.session.BridgeClient"))
            bridge_factory.return_value.__enter__.return_value = bridge
            stack.enter_context(patch("decision_metrics.providers.load_backend", return_value=backend))
            stack.enter_context(patch("brotato_agent.session.time.sleep"))
            stack.enter_context(patch("builtins.print"))
            result = run_session(args)
            metadata = json.loads((recording / "metadata.json").read_text())
            events = [json.loads(line) for line in (recording / "telemetry.jsonl").read_text().splitlines()]
            log = [json.loads(line) for line in (recording / "decisions.jsonl").read_text().splitlines()]
        return result, metadata, events, log, backend, bridge

    def test_provider_usage_pricing_action_and_instructions_reach_both_logs(self):
        summary, metadata, events, log, backend, bridge = self.run_fixture({"upgrade_0": {}, "upgrade_1": {}})
        self.assertAlmostEqual(summary["estimated_cost_usd"], .000245)
        self.assertEqual(summary["model_requests"], 1)
        self.assertEqual(metadata["backend"]["name"], "test-provider")
        self.assertEqual(metadata["timing"], "native")
        self.assertEqual([e["status"] for e in events if e["event"] == "action"], ["applied"])
        self.assertEqual(events[-1]["outcome"], "request_limit")
        decision = next(e for e in log if e["event"] == "decision")
        self.assertIn("instructions", decision)
        self.assertEqual(decision["judgment"]["decision_id"], next(e for e in events if e["event"] == "decision")["decision_id"])
        bridge.request.assert_any_call("act", observation_seq=1, action="upgrade_0")
        backend.close.assert_called_once()

    def test_forced_menu_action_uses_no_model_and_no_unknown_usage_charge(self):
        summary, _, events, _, backend, bridge = self.run_fixture({"upgrade_0": {}})
        backend.choose.assert_not_called()
        self.assertEqual(summary["forced_decisions"], 1)
        self.assertEqual(summary["model_requests"], 0)
        self.assertEqual(summary["unknown_usage_requests"], 0)
        self.assertEqual(summary["estimated_cost_usd"], 0)
        self.assertEqual(next(e for e in events if e["event"] == "decision")["source"], "forced")
        bridge.request.assert_any_call("act", observation_seq=1, action="upgrade_0")

    def test_accounting_uses_output_pricing_and_marks_unknown_price(self):
        accounting = Accounting(.24, .1)
        accounting.add_usage({"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        self.assertAlmostEqual(accounting.snapshot()["estimated_cost_usd"], .34)
        unknown = Accounting(None, None)
        unknown.model_requests = 1
        unknown.add_usage({"input_tokens": 100, "output_tokens": 0})
        self.assertEqual(unknown.snapshot()["unknown_price_requests"], 1)

    def test_invalid_provider_choice_is_billed_but_never_executed(self):
        summary, _, events, _, _, bridge = self.run_fixture({"upgrade_0": {}, "upgrade_1": {}}, choice="invented")
        self.assertEqual(summary["api_errors"], 1)
        self.assertEqual(summary["input_tokens"], 1000)
        self.assertFalse(any(call.args[0] == "act" for call in bridge.request.call_args_list))
        event = next(e for e in events if e["event"] == "decision")
        self.assertEqual(event["error"]["code"], "unknown_choice")
        self.assertFalse(any(e["event"] == "action" for e in events))

    def test_bridge_rejection_is_distinct_from_model_failure(self):
        summary, _, events, _, _, _ = self.run_fixture({"upgrade_0": {}, "upgrade_1": {}}, rejected=True)
        self.assertEqual(summary["api_errors"], 0)
        self.assertEqual(summary["rejected_actions"], 1)
        self.assertEqual(next(e for e in events if e["event"] == "decision")["status"], "ok")
        self.assertEqual(next(e for e in events if e["event"] == "action")["status"], "rejected")

    def test_interrupted_request_stops_with_unknown_usage_in_both_records(self):
        summary, _, events, _, _, _ = self.run_fixture({"upgrade_0": {}, "upgrade_1": {}}, interrupted=True)
        self.assertEqual(summary["outcome"], "interrupted")
        self.assertEqual(summary["unknown_usage_requests"], 1)
        self.assertEqual(next(e for e in events if e["event"] == "decision")["error"]["code"], "interrupted")
        self.assertEqual(events[-1]["outcome"], "interrupted")

    def test_shared_cadence_records_the_same_effective_ceiling_in_both_metadata_files(self):
        _, metadata, events, _, _, _ = self.run_fixture({"upgrade_0": {}, "upgrade_1": {}}, timing="shared")
        start = next(e for e in events if e["event"] == "run_start")
        self.assertEqual(start["metadata"]["timing"], "shared")
        self.assertEqual(start["metadata"]["max_combat_hz"], metadata["max_combat_hz"])
        self.assertEqual(metadata["max_combat_hz"], 5.0)

    def test_openai_native_answer_reaches_game_and_both_recordings(self):
        from decision_metrics.providers import make_backend
        backend = make_backend({"provider": "openai-decisions"},
                               environ={"OPENAI_API_KEY": "TEST_ONLY_SECRET"})
        transport = MagicMock()
        transport.post.return_value = {"model": "gpt-6-luna",
            "usage": {"input_tokens": 50, "output_tokens": 0},
            "answers": [{"type": "choice", "name": "action", "choice": "upgrade_1",
                "confidence": .6, "probabilities": [
                    {"value": "upgrade_0", "probability": .2},
                    {"value": "upgrade_1", "probability": .8}]}]}
        backend._transport = transport
        summary, metadata, events, log, _, bridge = self.run_fixture(
            {"upgrade_0": {"stat": "range"}, "upgrade_1": {"stat": "max_hp"}}, backend=backend)
        self.assertEqual(summary["api_errors"], 0)
        self.assertAlmostEqual(summary["estimated_cost_usd"], .000005)
        self.assertEqual(metadata["requested_model"], "gpt-6-luna")
        self.assertEqual(metadata["backend"]["details"]["api"], "openai-decisions")
        bridge.request.assert_any_call("act", observation_seq=1, action="upgrade_1")
        self.assertEqual(next(e for e in events if e["event"] == "action")["status"], "applied")
        decision = next(e for e in log if e["event"] == "decision")
        self.assertEqual(decision["judgment"]["probabilities"], {"upgrade_0": .2, "upgrade_1": .8})
        self.assertEqual(json.loads(transport.post.call_args.args[0]["input"]), decision["decision_state"])
        self.assertNotIn("TEST_ONLY_SECRET", json.dumps([metadata, events, log]))
        transport.close.assert_called_once()

    def test_openrouter_microsoft_choice_reaches_game_and_both_recordings(self):
        from decision_metrics.providers import make_backend
        backend = make_backend({"provider": "openrouter-decisions", "model": "microsoft/microsoft-decision-1",
            "api_key_env": "OPEN_ROUTER_KEY", "input_per_million_usd": .042},
            environ={"OPEN_ROUTER_KEY": "TEST_ONLY_SECRET"})
        transport = MagicMock()
        transport.post.return_value = {"model": "microsoft/microsoft-decision-1", "provider": "Azure",
            "usage": {"input_tokens": 50, "output_tokens": 11, "cost": .0000021},
            "answers": {"action": {"type": "choice", "choice": "upgrade_1", "confidence": .6,
                                    "probabilities": {"upgrade_0": .2, "upgrade_1": .8}}}}
        backend._transport = transport
        summary, metadata, events, log, _, bridge = self.run_fixture(
            {"upgrade_0": {"stat": "range"}, "upgrade_1": {"stat": "max_hp"}}, backend=backend)
        self.assertEqual(summary["api_errors"], 0)
        self.assertAlmostEqual(summary["estimated_cost_usd"], .0000021)
        self.assertEqual(metadata["requested_model"], "microsoft/microsoft-decision-1")
        self.assertEqual(metadata["backend"]["details"]["api"], "openrouter-decisions")
        bridge.request.assert_any_call("act", observation_seq=1, action="upgrade_1")
        self.assertEqual(next(e for e in events if e["event"] == "action")["status"], "applied")
        decision = next(e for e in log if e["event"] == "decision")
        self.assertEqual(decision["judgment"]["probabilities"], {"upgrade_0": .2, "upgrade_1": .8})
        self.assertEqual(transport.post.call_args.args[0]["state"], decision["decision_state"])
        self.assertEqual(transport.post.call_args.args[0]["provider"], {"allow_fallbacks": False})
        self.assertNotIn("TEST_ONLY_SECRET", json.dumps([metadata, events, log]))
        transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
