"""A hybrid Brotato agent: Jev moves in combat, an LLM makes the build decisions.

The shop, level-ups and crates pause the game, so a model that takes seconds to think
costs no health there; combat needs an answer every few hundred milliseconds. The LLM
runs through the Codex CLI (the same setup as the Slay the Spire 2 Astra runs: read-only
sandbox, no approvals, user config ignored, an output schema). It gets exactly the state,
legal options and instructions a decision model would get for that menu, and the schema
only allows one of the legal option ids. Its explanation (the build it is aiming for, the best option it passed on
and why it chose this one) is kept in the decision log. Codex does not pass on the model's
internal reasoning at low effort, so this written explanation is the record of its thinking.

A failed or late LLM answer is an error like any other: no fallback action is taken, and
the runner observes the menu again.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .jev import JevResponseError

PROMPT = """You make the build decisions for an AI agent playing Brotato, a roguelite arena game where \
weapons fire on their own. A separate fast model moves the character during waves; you choose in the \
shop, at level-ups and for crates. The game is paused while you decide, and you will be asked again \
after each purchase.

Instructions:
{instructions}

Current state (JSON):
{state}

Legal options, by id (JSON):
{options}

Reply with JSON:
{{"choice": one option id exactly as given,
  "plan": the build you are working toward, in one sentence,
  "runner_up": the best option you did not pick and why, in one sentence,
  "reason": why this option, in one sentence}}."""


@dataclass(frozen=True)
class MenuJudgment:
    """The fields the runner reads from any judgment, plus the LLM's reason."""
    choice: str
    confidence: None
    probabilities: None
    latency_ms: float
    model: str
    usage: dict | None
    source: str
    reason: str
    plan: str = ""
    runner_up: str = ""


class CodexMenuClient:
    def __init__(self, model="gpt-6-astra", effort="low", codex_bin="codex", timeout=25.0):
        self.model, self.effort, self.codex_bin, self.timeout = model, effort, codex_bin, timeout
        self.workdir = Path(tempfile.mkdtemp(prefix="brotato-menu-llm-"))   # empty: nothing to read

    def command(self, schema_path, answer_path):
        command = [self.codex_bin, "-s", "read-only", "-a", "never", "--model", self.model]
        if self.effort:
            command += ["-c", f'model_reasoning_effort="{self.effort}"']
        return command + ["exec", "--skip-git-repo-check", "--json", "--color", "never",
                          "--output-last-message", str(answer_path), "--output-schema", str(schema_path),
                          "--ephemeral", "--ignore-user-config", "-"]

    def choose(self, state, options, instructions):
        ids = list(options)
        schema = {"type": "object", "additionalProperties": False, "required": ["choice", "plan", "runner_up", "reason"],
                  "properties": {"choice": {"type": "string", "enum": ids}, "plan": {"type": "string"},
                                 "runner_up": {"type": "string"}, "reason": {"type": "string"}}}
        prompt = PROMPT.format(instructions=instructions, state=json.dumps(state, ensure_ascii=False),
                               options=json.dumps(options, ensure_ascii=False))
        schema_path = self.workdir / "schema.json"
        answer_path = self.workdir / "answer.json"
        schema_path.write_text(json.dumps(schema))
        answer_path.unlink(missing_ok=True)
        started = time.monotonic()
        try:
            done = subprocess.run(self.command(schema_path, answer_path), input=prompt, text=True,
                                  capture_output=True, timeout=self.timeout, cwd=self.workdir,
                                  env={**os.environ, "NO_COLOR": "1"})
        except subprocess.TimeoutExpired:
            raise self._error(f"menu LLM gave no answer within {self.timeout:g}s", started, None) from None
        usage = _usage(done.stdout)
        if done.returncode != 0:
            raise self._error(f"codex exited {done.returncode}: {(done.stderr or done.stdout)[-300:]}", started, usage)
        try:
            answer = json.loads(answer_path.read_text())
        except (OSError, ValueError):
            raise self._error("menu LLM answer was not JSON", started, usage) from None
        if answer.get("choice") not in options:
            raise self._error(f"menu LLM chose an option that is not legal: {answer.get('choice')!r}", started, usage)
        return MenuJudgment(answer["choice"], None, None, (time.monotonic() - started) * 1000, self.model,
                            usage, "llm", str(answer.get("reason", ""))[:300],
                            str(answer.get("plan", ""))[:300], str(answer.get("runner_up", ""))[:300])

    def _error(self, message, started, usage):
        error = JevResponseError(message)
        error.usage, error.latency_ms, error.request_attempted = usage, (time.monotonic() - started) * 1000, True
        error.source = "llm"   # not billed at the decision model's price
        return error

    def close(self):
        pass


def _usage(stdout):
    """Token counts from the Codex event stream (turn.completed), if it reports them."""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        usage = event.get("usage") if isinstance(event, dict) else None
        if isinstance(usage, dict) and "input_tokens" in usage:
            return {"input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0)}
    return None


class HybridClient:
    """Combat to the fast decision model, every menu to the LLM."""

    def __init__(self, combat_client, menu_client):
        self.combat, self.menu = combat_client, menu_client
        self.menu_calls = self.menu_errors = self.menu_input_tokens = self.menu_output_tokens = 0
        self.menu_latencies = []

    def choose(self, state, options, instructions):
        if state.get("phase") == "combat":
            return self.combat.choose(state, options, instructions)
        self.menu_calls += 1
        try:
            judgment = self.menu.choose(state, options, instructions)
        except JevResponseError as error:
            self.menu_errors += 1
            self._count(getattr(error, "usage", None))
            raise
        self._count(getattr(judgment, "usage", None))
        if getattr(judgment, "latency_ms", None) is not None:
            self.menu_latencies.append(judgment.latency_ms)
        return judgment

    def _count(self, usage):
        if usage:
            self.menu_input_tokens += usage["input_tokens"]
            self.menu_output_tokens += usage["output_tokens"]

    def menu_summary(self):
        lat = sorted(self.menu_latencies)
        return {"model": self.menu.model, "reasoning_effort": self.menu.effort, "calls": self.menu_calls,
                "errors": self.menu_errors, "input_tokens": self.menu_input_tokens,
                "output_tokens": self.menu_output_tokens,
                "median_latency_ms": round(lat[len(lat) // 2], 1) if lat else None}

    def close(self):
        self.combat.close()
        self.menu.close()
