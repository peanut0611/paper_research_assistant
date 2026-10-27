import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from paper_assistant.research.task import ResearchLimits, ResearchTask


def task_data() -> dict:
    return {
        "topic": "长上下文检索", "research_questions": ["证据位置如何影响结果？"],
        "start_at": "2026-09-01T00:00:00+08:00", "end_at": "2026-10-01T00:00:00+08:00",
    }


def test_task_serialization_preserves_input_offsets_and_canonical_utc_window():
    task = ResearchTask.from_dict(task_data())
    assert task.utc_window[0] == datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
    assert task.to_dict()["start_at"].endswith("+08:00")
    assert task.to_dict()["utc_window"]["boundary"] == "start_inclusive_end_exclusive"
    assert ResearchTask.from_dict(task.to_dict()) == task


@pytest.mark.parametrize("changes", [
    {"start_at": "2026-09-01"}, {"end_at": "2026-08-01T00:00:00Z"},
    {"topic": " "}, {"research_questions": "question"}, {"research_questions": []},
    {"target_papers": 4}, {"target_papers": True}, {"source": "other"},
    {"unknown": 1}, {"limits": {"max_candidates": 11}},
    {"limits": {"max_model_cost_usd": float("nan")}},
    {"limits": {"max_model_cost_usd": 0.01, "report_reserve_usd": 0.02}},
    {"utc_window": {"start_at": "changed"}},
])
def test_task_rejects_missing_scope_or_expanded_limits(changes):
    with pytest.raises(ValueError):
        ResearchTask.from_dict({**task_data(), **changes})


def test_custom_lower_limits_cannot_exceed_each_other():
    with pytest.raises(ValueError, match="first_search_candidates"):
        ResearchLimits(max_candidates=3)
    task = ResearchTask.from_dict({**task_data(), "target_papers": 1, "limits": {
        "max_candidates": 2, "first_search_candidates": 1,
        "max_fulltext_papers": 1, "max_fetch_attempts": 1,
    }})
    assert task.target_papers == 1


def test_direct_contract_requires_aware_timestamps():
    with pytest.raises(ValueError, match="timezone-aware"):
        ResearchTask("topic", ("question",), datetime(2026, 1, 1), datetime(2026, 2, 1))


def test_offline_scenarios_are_explicit_drafts_and_have_valid_task_contracts():
    root = Path(__file__).parents[2] / "eval_data" / "research"
    cases = [json.loads(line) for line in (root / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({case["id"] for case in cases}) == len(cases) >= 5
    candidates = json.loads((root / "synthetic_candidates.json").read_text(encoding="utf-8"))
    assert candidates["synthetic"] is True
    ids = {item["id"] for item in candidates["records"]}
    for case in cases:
        assert case["review_status"] == "pending_human_review"
        assert case["synthetic"] is True
        ResearchTask.from_dict(case["task"])
        assert set(case["candidate_ids"]) <= ids
