import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from brotato_agent.jev import JevResponseError
from brotato_agent.llm_menu import CodexMenuClient, HybridClient

FAKE_CODEX = r'''#!/usr/bin/env python3
"""Stands in for the Codex CLI: answers with FAKE_CHOICE, or sleeps, or fails."""
import json, os, sys, time
args = sys.argv[1:]
answer = args[args.index("--output-last-message") + 1]
schema = json.load(open(args[args.index("--output-schema") + 1]))
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "slow":
    time.sleep(5)
if mode == "fail":
    print("boom", file=sys.stderr)
    sys.exit(2)
choice = os.environ.get("FAKE_CHOICE") or schema["properties"]["choice"]["enum"][0]
json.dump({"choice": choice, "plan": "a gun build", "runner_up": "reroll: too expensive",
           "reason": "fits the build"}, open(answer, "w"))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 40}}))
'''


class Recorder:
    def __init__(self, name):
        self.name, self.calls, self.closed = name, [], False

    def choose(self, state, options, instructions):
        self.calls.append(state["phase"])
        return self.name

    def close(self):
        self.closed = True


class LlmMenuTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.codex = self.dir / "codex"
        self.codex.write_text(FAKE_CODEX)
        self.codex.chmod(self.codex.stat().st_mode | stat.S_IEXEC)
        self.options = {"buy_0": "Buy SMG", "next_wave": "Start the next wave"}
        for key in ("FAKE_MODE", "FAKE_CHOICE"):
            os.environ.pop(key, None)

    def client(self, timeout=10):
        return CodexMenuClient(model="test-model", effort="low", codex_bin=str(self.codex), timeout=timeout)

    def test_answer_is_a_legal_option_with_reason_and_usage(self):
        os.environ["FAKE_CHOICE"] = "next_wave"
        judgment = self.client().choose({"phase": "shop"}, self.options, "Pick one.")
        self.assertEqual(judgment.choice, "next_wave")
        self.assertEqual(judgment.reason, "fits the build")
        self.assertEqual(judgment.plan, "a gun build")
        self.assertEqual(judgment.runner_up, "reroll: too expensive")
        self.assertEqual(judgment.usage, {"input_tokens": 1200, "output_tokens": 40})
        self.assertEqual(judgment.source, "llm")

    def test_schema_only_allows_the_legal_ids(self):
        client = self.client()
        client.choose({"phase": "shop"}, self.options, "Pick one.")
        schema = json.loads((client.workdir / "schema.json").read_text())
        self.assertEqual(schema["properties"]["choice"]["enum"], ["buy_0", "next_wave"])
        self.assertEqual(schema["required"], ["choice", "plan", "runner_up", "reason"])

    def test_illegal_choice_is_an_error_not_an_action(self):
        os.environ["FAKE_CHOICE"] = "buy_9"
        with self.assertRaises(JevResponseError) as caught:
            self.client().choose({"phase": "shop"}, self.options, "Pick one.")
        self.assertEqual(caught.exception.source, "llm")

    def test_slow_or_failed_codex_is_an_error(self):
        os.environ["FAKE_MODE"] = "slow"
        with self.assertRaises(JevResponseError):
            self.client(timeout=1).choose({"phase": "shop"}, self.options, "Pick one.")
        os.environ["FAKE_MODE"] = "fail"
        with self.assertRaises(JevResponseError):
            self.client().choose({"phase": "shop"}, self.options, "Pick one.")

    def test_hybrid_sends_combat_to_jev_and_menus_to_the_llm(self):
        combat, menu = Recorder("jev"), Recorder("llm")
        menu.model, menu.effort = "test-model", "low"
        hybrid = HybridClient(combat, menu)
        self.assertEqual(hybrid.choose({"phase": "combat"}, {}, ""), "jev")
        for phase in ("shop", "upgrade", "crate"):
            hybrid.choose({"phase": phase}, {}, "")
        self.assertEqual(combat.calls, ["combat"])
        self.assertEqual(menu.calls, ["shop", "upgrade", "crate"])
        hybrid.close()
        self.assertTrue(combat.closed and menu.closed)


if __name__ == "__main__":
    unittest.main()
