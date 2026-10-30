"""Optional native Strands token preflight; no model weights or truncation."""
from __future__ import annotations

from dataclasses import replace
import importlib.metadata

CHECKPOINT = "StrandsAgents/strands-decider-2B-hobson-v19"
REVISION = "bb282d786bc251fd4e3068de3ada9ddbb38127cd"


class NativeStrandsBudget:
    def __init__(self, tokenizer, max_length=4096):
        from strands_decider.prompting import render_question, render_state
        from strands_decider.schema import SystemOneRequest
        self.tokenizer = tokenizer
        self.max_length = max_length
        self._render_question = render_question
        self._render_state = render_state
        self._request_type = SystemOneRequest

    @classmethod
    def cached(cls, max_length=4096):
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer
        if importlib.metadata.version("strands-decider") != "0.1.0":
            raise ValueError("Strands preflight requires the validated strands-decider 0.1.0 renderer")
        path = snapshot_download(CHECKPOINT, revision=REVISION, local_files_only=True)
        return cls(AutoTokenizer.from_pretrained(path, local_files_only=True), max_length)

    def inspect(self, request):
        from decision_metrics.rendering import render_options
        native = self._request_type.model_validate({"state": request.state, "questions": {
            "action": {"type": "choice", "instructions": request.instructions,
                       "criteria": render_options(request.options)}}})
        state_text = self._render_state(native.state)
        question_text = self._render_question(native.questions["action"]).text
        state_count = len(self.tokenizer(state_text, add_special_tokens=True)["input_ids"])
        question_count = len(self.tokenizer(question_text, add_special_tokens=False)["input_ids"])
        return {"state_tokens": state_count, "question_tokens": question_count,
                "input_tokens": state_count + question_count, "max_length": self.max_length}


class BudgetedStrandsBackend:
    """Reject oversize packets before HTTP, retaining safe token diagnostics."""
    def __init__(self, backend, budget):
        self.backend = backend
        self.budget = budget
        self.info = replace(backend.info, details={**backend.info.details,
            "input_preflight": {"renderer": "strands-decider-0.1.0", "max_length": budget.max_length,
                                "tokenizer_revision": REVISION, "truncation": False}})

    def choose(self, request):
        from decision_metrics import DecisionError
        size = self.budget.inspect(request)
        if size["input_tokens"] > size["max_length"]:
            raise DecisionError("context_window_exceeded", kind="capability",
                                diagnostics=size, request_attempted=False)
        return self.backend.choose(request)

    def close(self):
        self.backend.close()
