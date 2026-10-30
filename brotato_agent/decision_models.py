"""Optional DecisionMetrics adapter; the standalone library imports no game code."""
from dataclasses import dataclass, replace
import json
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from .jev import JevResponseError


def attest_strands_baseline(backend):
    """Verify the published v19 server before calling it the Brotato baseline."""
    if backend.info.requested_model != "strands-decider-2B-hobson-v19":
        return None
    if not isinstance(backend.info.endpoint, str):
        raise ValueError("Strands baseline requires an HTTP endpoint")
    url = urlsplit(backend.info.endpoint)
    health_url = urlunsplit((url.scheme, url.netloc, "/health", "", ""))
    try:
        with urlopen(health_url, timeout=5) as response:
            health = json.load(response)
    except (OSError, ValueError):
        raise ValueError("Could not verify the Strands baseline server health") from None
    if (not isinstance(health, dict)
            or health.get("model") != backend.info.requested_model
            or health.get("checkpoint_revision") != "bb282d786bc251fd4e3068de3ada9ddbb38127cd"
            or health.get("strict_window") is not True
            or health.get("max_length") != 4096
            or health.get("head_type") != "pointer"):
        raise ValueError("Strands baseline requires the published v19 checkpoint and strict full inputs")
    backend.info = replace(backend.info, details={**backend.info.details, "server_attestation": health})
    return health


@dataclass(frozen=True)
class BenchmarkJudgment:
    choice: str
    confidence: float | None
    probabilities: dict[str, float] | None
    latency_ms: float
    model: str | None
    usage: dict[str, int] | None
    decision_id: str
    source: str


class RecordingGameClient:
    def __init__(self, harness, sink):
        self.harness = harness
        self.sink = sink
        self._ticket = None
        self._observation = None

    def begin(self, state, observed_at):
        self._ticket = None
        self._observation = (state.get("phase", "menu"), state.get("seq"), observed_at)

    def choose(self, state, candidates, instructions):
        from dataclasses import asdict
        from decision_metrics import ChoiceRequest, DecisionError
        phase, seq, observed_at = self._observation
        try:
            ticket = self.harness.choose(ChoiceRequest(state, candidates, instructions),
                phase=phase, observation_id=seq, observed_at=observed_at,
                deadline_ms=850 if phase == "combat" else 30_000)
        except DecisionError as error:
            converted = JevResponseError(f"Decision backend: {error.code}")
            converted.usage = asdict(error.usage) if error.usage is not None else None
            converted.model = error.model
            converted.answer = error.diagnostics
            converted.request_attempted = error.request_attempted
            raise converted from None
        self._ticket = ticket
        result = ticket.result
        return BenchmarkJudgment(result.choice, result.confidence, result.probabilities,
                                 ticket.latency_ms, result.model,
                                 asdict(result.usage) if result.usage is not None else None,
                                 ticket.decision_id, ticket.source)

    def complete(self, status, reason=None):
        if self._ticket is not None:
            self.harness.record_action(self._ticket, status, reason=reason)
            self._ticket = None

    def finish(self, summary):
        self.harness.outcome(summary["outcome"], metrics={k: summary[k] for k in (
            "wave_reached", "wall_duration_seconds", "applied_actions", "api_errors",
            "expired_decisions", "rejected_actions")})

    def close(self):
        self.harness.backend.close()
        self.sink.close()


def build_client(args, directory):
    try:
        from decision_metrics import Harness, JsonlSink
        from decision_metrics.providers import load_backend
    except ImportError:
        raise ValueError("Install the standalone decision-metrics directory before choosing --backend") from None
    backend = load_backend(args.backend_config, args.backend)
    try:
        attest_strands_baseline(backend)
        if backend.info.details.get("strands_context_preflight"):
            from .strands_context import BudgetedStrandsBackend, NativeStrandsBudget
            budget = NativeStrandsBudget.cached(backend.info.details.get("max_input_tokens", 4096))
            backend = BudgetedStrandsBackend(backend, budget)
    except Exception:
        backend.close()
        raise
    sink = JsonlSink(directory / "telemetry.jsonl")
    timing = args.timing or "native"
    try:
        harness = Harness(backend, sink, run_id=args.session or directory.name,
                          metadata={"game": "Brotato", "mode": "live_full_run",
                                    "episode_id": args.session or directory.name,
                                    "timing": timing,
                                    "max_combat_hz": args.max_rate if timing == "native" else 5.0,
                                    "movement_lease_ms": 500, "prediction_horizon_ms": 500,
                                    "combat_deadline_ms": 850, "setup": "manual_unpaired",
                                    "shop_profile": getattr(args, "shop_profile", "baseline"),
                                    "combat_profile": getattr(args, "combat_profile", "baseline")})
    except Exception:
        backend.close()
        sink.close()
        raise
    return RecordingGameClient(harness, sink)
