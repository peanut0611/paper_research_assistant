"""Persisted controller state and conservative model accounting; no API calls."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from paper_assistant.agent.core.contracts import ModelEstimate, ModelUsage
from paper_assistant.agent.task import ResearchTask
from paper_assistant.storage import atomic_write_bytes


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def detached(value):
    return json.loads(json_bytes(value))


def digest(value: object) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def cost(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Model cost must be a finite nonnegative number")
    return Decimal(str(value))


@dataclass(frozen=True)
class RunnerConfig:
    max_steps: int = 32
    max_model_tokens: int = 16000
    report_reserve_tokens: int = 2000
    max_no_progress: int = 3
    max_repeat_calls: int = 2
    max_invalid_decisions: int = 2

    def __post_init__(self):
        for name, ceiling in (("max_steps", 64), ("max_model_tokens", 1000000), ("report_reserve_tokens", 1000000),
                              ("max_no_progress", 10), ("max_repeat_calls", 5), ("max_invalid_decisions", 3)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"Invalid {name}")
        if self.report_reserve_tokens >= self.max_model_tokens:
            raise ValueError("Token reserve must leave research capacity")


def can_reserve(task: ResearchTask, config: RunnerConfig, usage: dict, estimate: ModelEstimate, *, phase="decision") -> bool:
    if phase not in ("decision", "report"):
        raise ValueError("Unknown model phase")
    money_reserve = cost(task.limits.report_reserve_usd) if phase == "decision" else Decimal(0)
    token_reserve = config.report_reserve_tokens if phase == "decision" else 0
    return (cost(usage["cost_usd"]) + cost(estimate.max_cost_usd) + money_reserve <= cost(task.limits.max_model_cost_usd)
            and usage["tokens"] + estimate.max_tokens + token_reserve <= config.max_model_tokens)


@dataclass
class AgentState:
    binding: dict
    created_at: str
    updated_at: str
    status: str = "ready"
    stop_reason: str | None = None
    summary: str = ""
    steps: int = 0
    elapsed_seconds: float = 0.0
    tool_event_count: int = 0
    tool_last_sha256: str | None = None
    pending: dict | None = None
    no_progress: int = 0
    invalid_decisions: int = 0
    usage: dict = field(default_factory=lambda: {"tokens": 0, "cost_usd": 0.0, "calls": 0,
                                                "unknown_usage_calls": 0})
    messages: list = field(default_factory=list)
    agent_events: list = field(default_factory=list)
    model_events: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    queries: list = field(default_factory=list)
    selections: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)
    tool_limits: dict = field(default_factory=dict)
    last_error: dict | None = None

    def to_dict(self) -> dict:
        return detached(asdict(self))

    def save(self, path: Path) -> None:
        state = self.to_dict()
        atomic_write_bytes(path, json_bytes({"schema_version": 1, "state": state, "sha256": digest(state)}))

    @classmethod
    def load(cls, path: Path) -> "AgentState":
        if path.stat().st_size > 16_000_000:
            raise ValueError("Runner state is too large")
        value = json.loads(path.read_bytes())
        if value.get("schema_version") != 1 or value.get("sha256") != digest(value.get("state")):
            raise ValueError("Incomplete or changed runner state")
        state = cls(**value["state"])
        if state.status not in ("ready", "running", "stopped"):
            raise ValueError("Invalid runner status")
        for count in (state.steps, state.tool_event_count, state.no_progress, state.invalid_decisions, state.usage["tokens"], state.usage["calls"]):
            if type(count) is not int or count < 0:
                raise ValueError("Invalid runner counters")
        cost(state.usage["cost_usd"])
        if not math.isfinite(state.elapsed_seconds) or state.elapsed_seconds < 0:
            raise ValueError("Invalid runner elapsed time")
        return state
