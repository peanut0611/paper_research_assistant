"""Bounded evidence-to-answer handoff; no model or network requests."""
import json

import pytest

from paper_assistant.research.agent import MockPolicy, ResearchAgent
from paper_assistant.research.model import OpenAIDecisionPolicy
from paper_assistant.research.state import AgentState, ModelEstimate, RunnerConfig
from paper_assistant.research.task import ResearchLimits
from tests.research.test_agent_model import FakeClient
from tests.research.test_agent_runner import Clock, action, normal_policy, resumed, runner
from tests.research.test_agent_tools import tools
from tests.tools.test_paper_fetcher import task


def checked_action(view, *, insufficient=False, bad_quote=False):
    identifier, row = next(iter(view["evidence"].items()))
    return action("verify_citations",
        answer="论文未提供该问题所需的依据。" if insufficient else f"Supported excerpt [{identifier}]",
        insufficient_evidence=insufficient,
        citations=[] if insufficient else [{"evidence_id": identifier,
            "quote": "This is not present in the PDF." if bad_quote else row["text"][:20]}])


def gathering(view):
    return action("list_documents") if view["evidence"] else normal_policy(view)


@pytest.mark.parametrize("insufficient", [False, True])
def test_no_progress_hands_off_to_checked_answer_and_finishes_without_extra_model_call(tmp_path, sample_pdf, insufficient):
    def choose(view):
        if view["completion"].get("answer_required"):
            assert view["last_error"]["required_tool"] == "verify_citations"
            return checked_action(view, insufficient=insufficient)
        return gathering(view)
    agent = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf))
    result = agent.run()
    assert result["answer_phase"] == "no_progress"
    assert result["stop_reason"] == ("insufficient_evidence" if insufficient else "sufficient_evidence")
    assert result["steps"] == result["usage"]["calls"] == 7
    assert result["observations"][-1]["result"]["data"]["insufficient_evidence"] == insufficient
    assert not any(row.get("decision", {}).get("kind") == "finish" for row in result["decisions"])
    assert AgentState.load(agent.path).to_dict() == agent.state.to_dict()


def test_answer_phase_survives_resume_and_persistent_gathering_is_bounded(tmp_path, sample_pdf):
    agent = runner(tmp_path, gathering, runtime=tools(tmp_path, sample_pdf))
    paused = agent.run(max_steps_per_run=7)
    assert paused["answer_phase"] == "no_progress" and paused["last_error"]["code"] == "answer_required"
    assert paused["tool_event_count"] == 6  # The seventh action was blocked.
    result = resumed(agent).run()
    assert result["stop_reason"] == "no_progress" and result["usage"]["calls"] == 8
    assert result["tool_event_count"] == 6
    assert sum(row.get("error") == "answer_required" for row in result["decisions"]) == 2


def test_repeat_guard_hands_off_before_no_progress_threshold(tmp_path, sample_pdf):
    def choose(view):
        return checked_action(view) if view["completion"].get("answer_required") else gathering(view)
    result = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf),
                    config=RunnerConfig(max_no_progress=10)).run()
    assert result["stop_reason"] == "sufficient_evidence" and result["answer_phase"] == "no_progress"
    assert result["steps"] == 8 and result["tool_event_count"] == 7
    assert sum(row.get("error") == "answer_required" for row in result["decisions"]) == 1


def test_invalid_quote_can_be_corrected_in_answer_phase(tmp_path, sample_pdf):
    def choose(view):
        if not view["completion"].get("answer_required"):
            return gathering(view)
        checked = [row for row in view["observations"] if row["tool"] == "verify_citations"]
        return checked_action(view, bad_quote=not checked)
    result = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf)).run()
    checks = [row["result"] for row in result["observations"] if row["tool"] == "verify_citations"]
    assert len(checks) == 2 and not checks[0]["success"] and checks[1]["success"]
    assert result["stop_reason"] == "sufficient_evidence" and result["usage"]["calls"] == 8


def test_persistent_invalid_quotes_stop_without_publishing_unchecked_answer(tmp_path, sample_pdf):
    def choose(view):
        return checked_action(view, bad_quote=True) if view["completion"].get("answer_required") else gathering(view)
    result = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf)).run()
    assert result["stop_reason"] == "invalid_decision_limit" and result["usage"]["calls"] == 8
    assert sum(row.get("error") == "answer_validation_failed" for row in result["decisions"]) == 2
    assert not any(row["tool"] == "verify_citations" and row["result"]["success"] for row in result["observations"])


@pytest.mark.parametrize("boundary", ["step", "tool", "money"])
def test_answer_handoff_preserves_room_inside_existing_limits(tmp_path, sample_pdf, boundary):
    limits = ResearchLimits(max_tool_calls=5) if boundary == "tool" else (
        ResearchLimits(max_model_cost_usd=.025) if boundary == "money" else ResearchLimits())
    config = RunnerConfig(max_steps=6 if boundary == "step" else 32)
    def choose(view):
        return checked_action(view) if view["completion"].get("answer_required") else gathering(view)
    agent = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf, research_task=task(limits=limits)),
                   config=config, estimate=ModelEstimate(1000, .003 if boundary == "money" else .001))
    result = agent.run()
    assert result["stop_reason"] == "sufficient_evidence" and result["steps"] == 5
    assert result["answer_phase"] == {"step": "step_budget", "tool": "tool_budget", "money": "model_budget"}[boundary]
    assert result["tool_event_count"] <= limits.max_tool_calls
    assert result["usage"]["cost_usd"] + limits.report_reserve_usd <= limits.max_model_cost_usd


def test_answer_phase_cannot_bypass_runtime_deadline(tmp_path, sample_pdf):
    clock = Clock()
    agent = runner(tmp_path, gathering, runtime=tools(tmp_path, sample_pdf), clock=clock)
    agent.run(max_steps_per_run=6)
    assert agent.state.answer_phase == "no_progress"
    clock.seconds = 301
    result = agent.run()
    assert result["stop_reason"] == "runtime_budget_exhausted" and result["usage"]["calls"] == 6


def test_final_request_schema_only_exposes_answer_validation_and_keeps_evidence(tmp_path, sample_pdf):
    agent = runner(tmp_path, gathering, runtime=tools(tmp_path, sample_pdf))
    agent.run(max_steps_per_run=6)
    client = FakeClient()
    policy = OpenAIDecisionPolicy(client=client)
    policy.estimate(agent.view())
    request = policy._prepared["request"]
    context = json.loads(request["input"][0]["content"])
    branches = request["tools"][0]["parameters"]["properties"]["action"]["anyOf"]
    assert context["completion"]["answer_required"] and context["evidence"]
    assert len(branches) == 1 and branches[0]["properties"]["tool"]["enum"] == ["verify_citations"]
    assert policy._count(request) <= 16000 and not client.calls


def test_final_stage_does_not_send_request_when_even_answer_reservation_cannot_fit(tmp_path, sample_pdf):
    class LimitedPolicy(MockPolicy):
        def estimate(self, view):
            return ModelEstimate(1000, .004 if view["evidence"] else .0001)
    runtime = tools(tmp_path, sample_pdf, research_task=task(limits=ResearchLimits(max_model_cost_usd=.023)))
    agent = ResearchAgent(runtime, LimitedPolicy(gathering))
    result = agent.run()
    assert result["answer_phase"] == "model_budget" and result["stop_reason"] == "model_budget_exhausted"
    assert result["usage"]["calls"] == result["tool_event_count"] == 4
