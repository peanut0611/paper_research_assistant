"""Validated research request contract; it does not execute an Agent or spend money."""

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ResearchLimits:
    """MVP ceilings. A future runner must account for use before each action."""

    max_candidates: int = 10
    first_search_candidates: int = 5
    max_search_rounds: int = 2
    max_fulltext_papers: int = 3
    max_fetch_attempts: int = 5
    max_local_searches: int = 4
    max_tool_calls: int = 24
    max_pages_per_read: int = 5
    max_read_chars: int = 12000
    max_runtime_seconds: int = 300
    max_pdf_bytes: int = 20_000_000
    max_model_cost_usd: float = 0.10
    report_reserve_usd: float = 0.02

    def __post_init__(self) -> None:
        ceilings = {
            "max_candidates": 10, "first_search_candidates": 5,
            "max_search_rounds": 2, "max_fulltext_papers": 3,
            "max_fetch_attempts": 5, "max_local_searches": 4,
            "max_tool_calls": 24, "max_pages_per_read": 5,
            "max_read_chars": 12000, "max_runtime_seconds": 300,
            "max_pdf_bytes": 20_000_000,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be an integer in 1..{ceiling}")
        if self.first_search_candidates > self.max_candidates:
            raise ValueError("first_search_candidates exceeds max_candidates")
        if self.max_fulltext_papers > min(self.max_candidates, self.max_fetch_attempts):
            raise ValueError("Fulltext limit exceeds candidate or fetch limit")
        for name in ("max_model_cost_usd", "report_reserve_usd"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.report_reserve_usd >= self.max_model_cost_usd:
            raise ValueError("Report reserve must leave budget for research")


@dataclass(frozen=True)
class ResearchTask:
    """Explicit, timezone-aware [start_at, end_at) window on first submission.

    Offsets in the original timestamps are retained; utc_window is the canonical
    search interval. Relative-date parsing belongs to the future input adapter.
    """

    topic: str
    research_questions: tuple[str, ...]
    start_at: datetime
    end_at: datetime
    target_papers: int = 3
    source: str = "arxiv"
    limits: ResearchLimits = field(default_factory=ResearchLimits)

    def __post_init__(self) -> None:
        if not isinstance(self.topic, str) or not self.topic.strip() or len(self.topic) > 500:
            raise ValueError("topic must contain 1..500 characters")
        if not isinstance(self.research_questions, tuple) or not 1 <= len(self.research_questions) <= 8:
            raise ValueError("Provide 1..8 research_questions as a tuple")
        if any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in self.research_questions):
            raise ValueError("Each research question must contain 1..1000 characters")
        for value in (self.start_at, self.end_at):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("Date window requires timezone-aware timestamps")
        if self.start_at >= self.end_at:
            raise ValueError("start_at must precede end_at")
        if self.source != "arxiv":
            raise ValueError("Only arxiv is supported in the MVP")
        if not isinstance(self.limits, ResearchLimits):
            raise ValueError("limits must be ResearchLimits")
        if type(self.target_papers) is not int or not 1 <= self.target_papers <= self.limits.max_fulltext_papers:
            raise ValueError("target_papers exceeds the fulltext limit")

    @property
    def utc_window(self) -> tuple[datetime, datetime]:
        return (self.start_at.astimezone(timezone.utc), self.end_at.astimezone(timezone.utc))

    def to_dict(self) -> dict:
        start, end = self.utc_window
        return {
            "topic": self.topic, "research_questions": list(self.research_questions),
            "start_at": self.start_at.isoformat(), "end_at": self.end_at.isoformat(),
            "target_papers": self.target_papers, "source": self.source,
            "limits": asdict(self.limits),
            "utc_window": {"start_at": start.isoformat(), "end_at": end.isoformat(),
                           "boundary": "start_inclusive_end_exclusive", "date_field": "submitted"},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchTask":
        allowed = {"topic", "research_questions", "start_at", "end_at", "target_papers", "source", "limits", "utc_window"}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("Invalid research task fields")
        required = {"topic", "research_questions", "start_at", "end_at"}
        if not required <= data.keys() or not isinstance(data["research_questions"], list):
            raise ValueError("Missing research task fields or invalid research_questions")
        try:
            result = cls(
                topic=data["topic"], research_questions=tuple(data["research_questions"]),
                start_at=datetime.fromisoformat(data["start_at"]),
                end_at=datetime.fromisoformat(data["end_at"]),
                target_papers=data.get("target_papers", 3), source=data.get("source", "arxiv"),
                limits=ResearchLimits(**data.get("limits", {})),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid research task: {error}") from error
        if "utc_window" in data and data["utc_window"] != result.to_dict()["utc_window"]:
            raise ValueError("utc_window does not match the input timestamps")
        return result
