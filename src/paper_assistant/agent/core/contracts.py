"""Provider-neutral responses; no research, PDF, OpenAI or storage imports."""
import math
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelEstimate:
    max_tokens: int = 1000
    max_cost_usd: float = 0.001

    def __post_init__(self):
        if type(self.max_tokens) is not int or self.max_tokens < 0:
            raise ValueError("Invalid token reservation")
        if isinstance(self.max_cost_usd, bool) or not isinstance(self.max_cost_usd, (int, float)) or not math.isfinite(self.max_cost_usd) or self.max_cost_usd < 0:
            raise ValueError("Invalid cost reservation")


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in (self.input_tokens, self.output_tokens)):
            raise ValueError("Invalid model token usage")
        if isinstance(self.cost_usd, bool) or not isinstance(self.cost_usd, (int, float)) or not math.isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("Invalid model cost")

    @property
    def tokens(self):
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ModelReply:
    decision: dict
    usage: ModelUsage = ModelUsage()
    metadata: dict = field(default_factory=dict)
    message: dict | None = None


class DecisionError(Exception):
    def __init__(self, code, message, *, usage=None, metadata=None):
        super().__init__(message)
        self.code, self.usage, self.metadata = code, usage, metadata or {}


class ModelProvider(Protocol):
    policy_id: str
    simulated: bool

    def estimate(self, view: dict) -> ModelEstimate: ...
    def decide(self, view: dict) -> ModelReply: ...


DecisionPolicy = ModelProvider  # Existing public import remains supported.
