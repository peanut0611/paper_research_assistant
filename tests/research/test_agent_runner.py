import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from paper_assistant.research.agent import ResearchAgent, MockPolicy, ModelReply, RecoveryError
from paper_assistant.research.state import AgentState, ModelEstimate, ModelUsage, RunnerConfig, can_reserve
from paper_assistant.research.toolset import ResearchTools
from paper_assistant.tools.arxiv.search import HTTPResponse, LiteratureSearchSession
from paper_assistant.research.task import ResearchLimits
from tests.research.test_agent_tools import LIVE, acquire_and_ingest, tools
from tests.tools.test_literature_search import FakeTransport as SearchTransport, client as search_client
from tests.tools.test_paper_fetcher import FakeResponse, candidates, task


def action(tool, **arguments):
    return {"kind": "tool", "tool": tool, "arguments": arguments}


def finish(reason="insufficient_evidence"):
    return {"kind": "finish", "reason": reason, "summary": "Only mock controller validation"}


class Clock:
    seconds = 0

    def now(self):
        return datetime(2026, 10, 23, tzinfo=timezone.utc) + timedelta(seconds=self.seconds)

    def mono(self):
        return self.seconds


def runner(tmp_path, choose, *, runtime=None, config=None, clock=None, **policy_kwargs):
    runtime = runtime or tools(tmp_path)
    clock = clock or Clock()
    runtime.monotonic = clock.mono
    return ResearchAgent(runtime, MockPolicy(choose, **policy_kwargs), config=config, now=clock.now, monotonic=clock.mono)


def resumed(original, *, config=None, policy=None, clock=None):
    restored_tools = ResearchTools(original.tools.workspace, offline=original.tools.offline,
                                   resume=original.tools.journal_path.exists())
    clock = clock or Clock()
    restored_tools.monotonic = clock.mono
    return ResearchAgent(restored_tools, policy or original.policy, config=config or original.config,
                       resume=True, now=clock.now, monotonic=clock.mono)


def normal_policy(view):
    if not view["candidates"]:
        return action("search_literature", query="retrieval augmented generation", max_results=3)
    row = next(row for row in view["candidates"] if row["arxiv_id"] == "2401.00396")
    if row["source_sha256"] is None:
        result = action("fetch_paper", arxiv_id=row["arxiv_id"])
        result["updates"] = {"gaps": ["Need fulltext"], "selections": [
            {"arxiv_id": row["arxiv_id"], "selected": True, "reason": "Mock fixture selection"}]}
        return result
    if row["status"] != "fulltext_ready":
        return action("ingest_paper", arxiv_id=row["arxiv_id"])
    if not view["evidence"]:
        return action("search_documents", query="retrieval", method="bm25")
    if not any(row["tool"] == "verify_citations" for row in view["observations"]):
        identifier, evidence = next(iter(view["evidence"].items()))
        return action("verify_citations", answer=f"Mock excerpt [{identifier}]", insufficient_evidence=False,
                      citations=[{"evidence_id": identifier, "quote": evidence["text"][:10]}])
    result = finish("sufficient_evidence")
    result["updates"] = {"gaps": []}
    return result


def test_observation_driven_mock_completes_discovery_to_citation_check(tmp_path, sample_pdf):
    controller = runner(tmp_path, normal_policy, runtime=tools(tmp_path, sample_pdf))
    result = controller.run()
    assert result["stop_reason"] == "sufficient_evidence" and result["steps"] == 6
    assert result["tool_event_count"] == 5 and result["usage"]["calls"] == 6
    assert result["evidence"] and not result["gaps"] and result["selections"]["2401.00396"]["selected"]
    assert all(event["simulated"] for event in result["model_events"])
    assert controller.tools.workspace._embedder is not None
    assert AgentState.load(controller.path).to_dict() == controller.state.to_dict()


def test_empty_observation_causes_changed_query_and_retains_both_rounds(tmp_path):
    empty = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>0</o:totalResults><o:startIndex>0</o:startIndex></feed>'
    runtime = tools(tmp_path, search_responses=[HTTPResponse(200, "application/xml", empty), HTTPResponse(200, "application/xml", LIVE.read_bytes())])
    def choose(view):
        if view["candidates"]:
            return finish()
        return action("search_literature", query="first narrower query" if not view["queries"] else "retrieval augmented generation", max_results=3)
    result = runner(tmp_path, choose, runtime=runtime).run()
    assert len(result["candidates"]) == 3 and result["tool_limits"]["search_rounds"] == 2
    assert [row["query"] for row in result["queries"]] == ["first narrower query", "retrieval augmented generation"]


def test_unavailable_pdf_leads_to_registered_alternative_with_attempts_preserved(tmp_path, sample_pdf):
    runtime = tools(tmp_path, seed=candidates(), fetch_responses=[FakeResponse(status=404), FakeResponse(sample_pdf.read_bytes())])
    def choose(view):
        if view["tool_limits"]["downloaded_papers"]:
            return finish()
        return action("fetch_paper", arxiv_id="2401.00544" if not view["observations"] else "2401.00396")
    result = runner(tmp_path, choose, runtime=runtime).run()
    assert result["observations"][0]["result"]["error"]["code"] == "unavailable"
    assert result["tool_limits"]["fetch_attempts"] == 2 and result["tool_limits"]["downloaded_papers"] == 1


def test_exact_repeats_stop_without_third_side_effect(tmp_path):
    controller = runner(tmp_path, lambda view: action("list_documents"))
    result = controller.run()
    assert result["stop_reason"] == "repeated_action" and result["tool_event_count"] == 2


def test_different_empty_retrievals_stop_for_no_progress(tmp_path):
    result = runner(tmp_path, lambda view: action("search_documents", query=f"missing {view['steps']}", method="bm25")).run()
    assert result["stop_reason"] == "no_progress" and result["tool_limits"]["local_searches"] == 3


@pytest.mark.parametrize("limit,expected", [("step", "step_budget_exhausted"), ("tool", "tool_budget_exhausted")])
def test_independent_global_step_and_tool_limits_stop_before_model_call(tmp_path, limit, expected):
    runtime = tools(tmp_path, research_task=task(limits=ResearchLimits(max_tool_calls=1 if limit == "tool" else 24)))
    controller = runner(tmp_path, lambda view: action("list_documents"), runtime=runtime,
                        config=RunnerConfig(max_steps=1 if limit == "step" else 32))
    result = controller.run()
    assert result["stop_reason"] == expected and result["usage"]["calls"] == 1


@pytest.mark.parametrize("estimate,config", [(ModelEstimate(10, 0.081), RunnerConfig()),
                                           (ModelEstimate(81, 0), RunnerConfig(max_model_tokens=100, report_reserve_tokens=20))])
def test_report_reserve_prevents_decision_before_it_is_called(tmp_path, estimate, config):
    calls = []
    result = runner(tmp_path, lambda view: calls.append(True), estimate=estimate, config=config).run()
    assert result["stop_reason"] == "model_budget_exhausted" and not calls and result["steps"] == 0


def test_model_ledger_keeps_estimates_separate_and_accumulates_actual_usage(tmp_path):
    controller = runner(tmp_path, lambda view: action("list_documents"), estimate=ModelEstimate(60, 0.001),
                        usage=ModelUsage(40, 10, 0.0005), config=RunnerConfig(max_model_tokens=100, report_reserve_tokens=20))
    result = controller.run()
    assert result["stop_reason"] == "model_budget_exhausted" and result["usage"]["tokens"] == 50
    assert result["model_events"][0]["reservation"]["max_tokens"] == 60
    assert result["model_events"][0]["usage"]["input_tokens"] == 40
    assert can_reserve(controller.tools.task, controller.config, result["usage"], ModelEstimate(50, 0.001), phase="report")


def test_decimal_budget_boundary_does_not_overspend_research_reserve():
    request = task(limits=ResearchLimits(max_model_cost_usd=0.1, report_reserve_usd=0.02))
    assert can_reserve(request, RunnerConfig(), {"cost_usd": 0.07, "tokens": 0}, ModelEstimate(1, 0.01))
    assert not can_reserve(request, RunnerConfig(), {"cost_usd": 0.07, "tokens": 0}, ModelEstimate(1, 0.010001))


def test_usage_over_reservation_stops_and_records_actual_without_tool_action(tmp_path):
    result = runner(tmp_path, lambda view: action("list_documents"), estimate=ModelEstimate(20, 0.001), usage=ModelUsage(21, 0, 0.002)).run()
    assert result["stop_reason"] == "model_usage_exceeded_reservation" and result["usage"]["tokens"] == 21
    assert result["tool_event_count"] == 0


def test_policy_exception_uses_conservative_unknown_charge_without_logging_raw_error(tmp_path):
    def fail(view):
        raise RuntimeError("sk-private-secret must not appear")
    controller = runner(tmp_path, fail, estimate=ModelEstimate(20, 0.001))
    result = controller.run()
    assert result["stop_reason"] == "model_error_unknown_usage"
    assert result["usage"]["unknown_usage_calls"] == 1 and result["usage"]["tokens"] == 20
    assert "sk-private-secret" not in controller.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("decision", [{"kind": "shell", "command": "x"}, {"kind": "tool", "tool": "list_documents", "arguments": {}, "task": {}},
    {"kind": "finish", "reason": "sufficient_evidence", "summary": "x", "updates": {"selections": [
        {"arxiv_id": "not-searched", "selected": True, "reason": "invented"}]}}])
def test_invalid_decisions_have_bounded_corrections_and_still_cost_steps(tmp_path, decision):
    result = runner(tmp_path, lambda view: decision).run()
    assert result["stop_reason"] == "invalid_decision_limit" and result["usage"]["calls"] == 2
    assert result["tool_event_count"] == 0


def test_tool_parameters_cannot_expand_task_or_read_arbitrary_path(tmp_path):
    result = runner(tmp_path, lambda view: action("list_documents", path="C:/secret")).run()
    assert result["stop_reason"] == "repeated_action"
    assert all(row["result"]["error"]["code"] == "invalid_arguments" for row in result["observations"])


def test_finish_requires_checked_actual_evidence_for_sufficient_flag(tmp_path):
    assert runner(tmp_path / "a", lambda view: finish("sufficient_evidence")).run()["stop_reason"] == "unverified_completion"
    assert runner(tmp_path / "b", lambda view: finish()).run()["stop_reason"] == "insufficient_evidence"


def test_premature_finish_can_be_corrected_by_real_citation_validation(tmp_path, sample_pdf):
    def choose(view):
        checked = any(row["tool"] == "verify_citations" for row in view["observations"])
        if view["evidence"] and not checked:
            if not view["last_error"]:
                return finish("sufficient_evidence")
            assert view["last_error"]["required_tool"] == "verify_citations"
            assert not view["completion"]["can_finish_sufficient_evidence"]
        return normal_policy(view)
    controller = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf))
    result = controller.run()
    assert result["stop_reason"] == "sufficient_evidence"
    assert result["steps"] == result["usage"]["calls"] == 7
    assert result["tool_event_count"] == 5 and result["last_error"] is None
    assert sum(row.get("error") == "unverified_completion" for row in result["decisions"]) == 1
    assert controller.view()["completion"]["can_finish_sufficient_evidence"]


def test_premature_finish_correction_limit_survives_checkpoint_and_resume(tmp_path):
    controller = runner(tmp_path, lambda view: finish("sufficient_evidence"))
    first = controller.run(max_steps_per_run=1)
    assert first["status"] == "ready" and first["pending"] is None
    assert first["last_error"]["code"] == "unverified_completion"
    result = resumed(controller).run()
    assert result["stop_reason"] == "unverified_completion"
    assert result["steps"] == result["usage"]["calls"] == 2
    assert result["tool_event_count"] == 0


def test_intervening_tool_does_not_reset_premature_finish_correction_limit(tmp_path):
    def choose(view):
        return action("list_documents") if view["steps"] == 2 else finish("sufficient_evidence")
    result = runner(tmp_path, choose).run()
    assert result["stop_reason"] == "unverified_completion"
    assert result["usage"]["calls"] == 3 and result["tool_event_count"] == 1


@pytest.mark.parametrize("boundary", ["steps", "money", "time"])
def test_completion_correction_cannot_bypass_existing_budgets(tmp_path, boundary):
    runtime = tools(tmp_path, research_task=task(limits=ResearchLimits(max_model_cost_usd=0.021))) if boundary == "money" else tools(tmp_path)
    clock = Clock()
    config = RunnerConfig(max_steps=1) if boundary == "steps" else RunnerConfig()
    controller = runner(tmp_path, lambda view: finish("sufficient_evidence"), runtime=runtime, config=config, clock=clock)
    controller.run(max_steps_per_run=1)
    if boundary == "time":
        clock.seconds = 301
    result = controller.run()
    assert result["stop_reason"] == {"steps": "step_budget_exhausted", "money": "model_budget_exhausted", "time": "runtime_budget_exhausted"}[boundary]
    assert result["usage"]["calls"] == 1 and result["tool_event_count"] == 0


def test_later_insufficient_answer_cannot_finish_using_an_older_positive_check(tmp_path, sample_pdf):
    def choose(view):
        checks = [row for row in view["observations"] if row["tool"] == "verify_citations"]
        if len(checks) == 1:
            return action("verify_citations", answer="证据不足。", insufficient_evidence=True, citations=[])
        return finish("sufficient_evidence") if len(checks) == 2 else normal_policy(view)
    controller = runner(tmp_path, choose, runtime=tools(tmp_path, sample_pdf), config=RunnerConfig(max_invalid_decisions=1))
    result = controller.run()
    assert result["stop_reason"] == "unverified_completion"
    assert not controller.view()["completion"]["can_finish_sufficient_evidence"]


def test_mutating_policy_view_cannot_change_task_limits_or_private_evidence(tmp_path):
    def choose(view):
        view["binding"]["task"]["limits"]["max_tool_calls"] = 999
        view["evidence"]["E999"] = {"text": "invented"}
        return finish("sufficient_evidence")
    controller = runner(tmp_path, choose)
    result = controller.run()
    assert controller.tools.task.limits.max_tool_calls == 24 and "E999" not in result["evidence"]


def test_deadline_after_decision_charges_usage_but_does_not_dispatch_tool(tmp_path):
    clock = Clock()
    def choose(view):
        clock.seconds = 301
        return action("list_documents")
    result = runner(tmp_path, choose, clock=clock).run()
    assert result["stop_reason"] == "runtime_budget_exhausted" and result["tool_event_count"] == 0
    assert result["usage"]["calls"] == 1


def test_deadline_after_tool_keeps_observation_and_prevents_next_decision(tmp_path, monkeypatch):
    clock = Clock()
    controller = runner(tmp_path, lambda view: action("list_documents"), clock=clock)
    dispatch = controller.tools._dispatch
    def slow(*args):
        clock.seconds = 301
        return dispatch(*args)
    monkeypatch.setattr(controller.tools, "_dispatch", slow)
    result = controller.run()
    assert result["stop_reason"] == "runtime_budget_exhausted" and result["tool_event_count"] == 1
    assert result["usage"]["calls"] == 1


def test_clean_checkpoint_resume_preserves_usage_evidence_and_selection(tmp_path, sample_pdf):
    controller = runner(tmp_path, normal_policy, runtime=tools(tmp_path, sample_pdf))
    before = controller.run(max_steps_per_run=4)
    assert before["status"] == "ready" and before["evidence"]
    restored = resumed(controller)
    assert restored.state.usage == before["usage"] and restored.state.evidence == before["evidence"]
    result = restored.run()
    assert result["stop_reason"] == "sufficient_evidence" and result["steps"] == 6
    assert result["tool_limits"]["search_rounds"] == 1 and result["usage"]["calls"] == 6
    assert result["selections"] == before["selections"]


def test_completed_tool_before_runner_checkpoint_is_reconciled_without_reexecution(tmp_path, monkeypatch):
    controller = runner(tmp_path, lambda view: action("list_documents"))
    save = controller._save
    saves = []
    def fail_after_action():
        saves.append(True)
        if len(saves) == 3:
            raise OSError("Synthetic controller checkpoint failure")
        save()
    monkeypatch.setattr(controller, "_save", fail_after_action)
    with pytest.raises(OSError):
        controller.step()
    assert AgentState.load(controller.path).pending["phase"] == "tool"
    restored = resumed(controller)
    assert restored.state.tool_event_count == 1 and restored.state.pending is None
    from paper_assistant.agent.messages import completed_exchanges
    assert len(completed_exchanges(restored.state.messages)) == 1
    assert restored.state.usage["calls"] == 1 and len(restored.tools.export_trace()["events"]) == 1


def test_interrupted_decision_never_retried_and_unknown_usage_charged_on_resume(tmp_path):
    calls = []
    def crash(view):
        calls.append(True)
        raise KeyboardInterrupt()
    controller = runner(tmp_path, crash, estimate=ModelEstimate(50, 0.001))
    with pytest.raises(KeyboardInterrupt):
        controller.step()
    restored = resumed(controller)
    result = restored.run()
    assert result["stop_reason"] == "interrupted_unknown_model_usage" and calls == [True]
    assert result["usage"]["tokens"] == 50 and result["usage"]["unknown_usage_calls"] == 1


def test_pending_tool_without_journal_is_stopped_without_retry(tmp_path, monkeypatch):
    controller = runner(tmp_path, lambda view: action("list_documents"))
    def crash(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(controller.tools, "call", crash)
    with pytest.raises(KeyboardInterrupt):
        controller.step()
    restored = resumed(controller)
    assert restored.run()["stop_reason"] == "interrupted_unknown_tool_outcome"
    assert not restored.tools.journal_path.exists()


def test_started_tool_record_refuses_resume_instead_of_retry(tmp_path, monkeypatch):
    controller = runner(tmp_path, lambda view: action("list_documents"))
    def crash(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(controller.tools, "_dispatch", crash)
    with pytest.raises(KeyboardInterrupt):
        controller.step()
    with pytest.raises(ValueError, match="Incomplete"):
        resumed(controller)
    assert AgentState.load(controller.path).pending["phase"] == "tool"


def test_downtime_counts_against_deadline_after_resume(tmp_path):
    controller = runner(tmp_path, lambda view: action("list_documents"))
    controller.step()
    clock = Clock()
    clock.seconds = 301
    restored = resumed(controller, clock=clock)
    result = restored.run()
    assert result["stop_reason"] == "runtime_budget_exhausted" and result["usage"]["calls"] == 1


def test_changed_policy_configuration_or_state_refuses_resume(tmp_path):
    controller = runner(tmp_path, lambda view: action("list_documents"))
    controller.step()
    with pytest.raises(RecoveryError, match="changed"):
        resumed(controller, config=RunnerConfig(max_steps=2))
    with pytest.raises(RecoveryError, match="changed"):
        resumed(controller, policy=MockPolicy(lambda view: finish(), policy_id="different-policy"))
    value = json.loads(controller.path.read_bytes())
    value["state"]["usage"]["tokens"] = 0
    value["state"]["steps"] = 0
    controller.path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        resumed(controller)


def test_terminated_checkpoint_resume_returns_without_additional_calls(tmp_path):
    controller = runner(tmp_path, lambda view: finish())
    initial = controller.run()
    restored = resumed(controller)
    result = restored.run()
    assert result["usage"] == initial["usage"] and result["stop_reason"] == initial["stop_reason"]


def test_tools_restore_failed_attempts_rounds_and_local_search_without_reset(tmp_path):
    runtime = tools(tmp_path, seed=candidates(), fetch_responses=[FakeResponse(status=404)])
    runtime.call("search_literature", {"query": "retrieval augmented generation", "max_results": 3})
    runtime.call("fetch_paper", {"arxiv_id": "2401.00544"})
    runtime.call("search_documents", {"query": "x", "method": "bm25"})
    restored = ResearchTools(runtime.workspace, resume=True)
    assert restored.limits()["search_rounds"] == 1 and restored.limits()["fetch_attempts"] == 1
    assert restored.limits()["local_searches"] == 1 and restored.limits()["tool_calls"] == 3
    assert restored.limits()["network_requests"] == 2


def test_tool_checkpoint_tamper_or_transport_mode_change_refuses_resume(tmp_path):
    runtime = tools(tmp_path)
    runtime.call("list_documents", {})
    with pytest.raises(ValueError, match="transport mode"):
        ResearchTools(runtime.workspace, offline=True, resume=True)
    value = json.loads(runtime.journal_path.read_bytes())
    value["checkpoint"]["calls"] = 0
    runtime.journal_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint"):
        ResearchTools(runtime.workspace, resume=True)


@pytest.mark.parametrize("factory", [lambda: ModelUsage(True, 0, 0), lambda: ModelEstimate(2, float("nan")),
    lambda: RunnerConfig(max_steps=True), lambda: RunnerConfig(max_model_tokens=20, report_reserve_tokens=20)])
def test_accounting_contracts_reject_ambiguous_or_nonfinite_limits(factory):
    with pytest.raises(ValueError):
        factory()


def test_estimate_failure_prevents_decision_and_usage_charge(tmp_path, monkeypatch):
    calls = []
    controller = runner(tmp_path, lambda view: calls.append(True))
    monkeypatch.setattr(controller.policy, "estimate", lambda view: None)
    result = controller.run()
    assert result["stop_reason"] == "model_estimate_error" and not calls and result["usage"]["calls"] == 0


def test_state_storage_failure_prevents_decision_and_stops_current_session(tmp_path, monkeypatch):
    calls = []
    controller = runner(tmp_path, lambda view: calls.append(True))
    def fail(*args):
        raise OSError("Synthetic state storage failure")
    monkeypatch.setattr(controller.state, "save", fail)
    with pytest.raises(OSError):
        controller.step()
    with pytest.raises(OSError, match="session stopped"):
        controller.step()
    assert not calls and not controller.tools.journal_path.exists()


def test_external_tool_action_is_detected_before_decision_and_on_resume(tmp_path):
    controller = runner(tmp_path, lambda view: finish())
    controller.tools.call("list_documents", {})
    with pytest.raises(RecoveryError, match="outside"):
        controller.step()
    with pytest.raises(RecoveryError, match="outside"):
        resumed(controller)
    assert controller.state.usage["calls"] == 0


@pytest.mark.parametrize("tool_name", ["search_literature", "fetch_paper"])
def test_network_timeout_is_capped_by_remaining_total_runtime(tmp_path, sample_pdf, tool_name):
    clock = Clock()
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    choose = (lambda view: action("search_literature", query="retrieval augmented generation", max_results=3)) if tool_name == "search_literature" else (
        lambda view: action("fetch_paper", arxiv_id="2401.00544"))
    controller = runner(tmp_path, choose, runtime=runtime, clock=clock)
    clock.seconds = 299
    controller.step()
    transport = runtime.search.client.transport if tool_name == "search_literature" else runtime.workspace.fetcher.transport
    assert 0 < transport.calls[0][1] <= 1


def test_excluded_date_candidates_survive_resume_and_still_occupy_capacity(tmp_path):
    import xml.etree.ElementTree as ET
    from paper_assistant.tools.arxiv.search import ATOM
    tree = ET.fromstring(LIVE.read_bytes())
    tree.findall(ATOM + "entry")[0].find(ATOM + "published").text = "2024-01-01T00:00:00Z"
    tree.findall(ATOM + "entry")[0].find(ATOM + "updated").text = "2024-01-02T00:00:00Z"
    runtime = tools(tmp_path, search_responses=[HTTPResponse(200, "application/xml", ET.tostring(tree))])
    runtime.call("search_literature", {"query": "retrieval augmented generation", "max_results": 3})
    restored = ResearchTools(runtime.workspace, resume=True)
    assert len(restored.workspace.registered_candidates) == 2
    assert restored.limits()["observed_candidates"] == 3 and restored.limits()["search_rounds"] == 1


def test_restored_local_search_limit_cannot_be_reset_by_new_session(tmp_path):
    runtime = tools(tmp_path, research_task=task(limits=ResearchLimits(max_local_searches=1)))
    runtime.call("search_documents", {"query": "x"})
    restored = ResearchTools(runtime.workspace, resume=True)
    result = restored.call("search_documents", {"query": "y"})
    assert result["error"]["code"] == "local_search_budget_exhausted"
    assert result["limits"]["after"]["local_searches"] == 1


def test_cli_mock_controller_can_checkpoint_resume_and_return_stopped_state(tmp_path):
    root = Path(__file__).parents[2]
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "gbk"}
    script = root / "scripts" / "dev" / "run_mock_research.py"
    request = task()
    directory = tmp_path / "runs"
    policy = MockPolicy(lambda view: action("list_documents"), policy_id="fixed-historical-smoke-v1:2401.00396")
    # Seed one safe checkpoint with the CLI's exact task/policy binding; no I/O or model.
    from paper_assistant.research.task import ResearchTask
    from paper_assistant.research.workspace import ResearchWorkspace
    cli_task = ResearchTask("intentionally uncached mock query", ("What is RAGTruth?",), request.start_at, request.end_at)
    workspace = ResearchWorkspace("cli-runner", cli_task, (), root=directory)
    controller = ResearchAgent(ResearchTools(workspace, offline=True), policy)
    controller.run(max_steps_per_run=1)
    command = [sys.executable, str(script), "--task-id", "cli-runner", "--root", str(directory),
               "--resume", "--offline", "--local-models-only"]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["mode"] == "mock_policy_not_llm_agent" and output["llm_calls"] == 0
    # The source is deliberately unavailable offline; the mock terminates rather than retrying indefinitely.
    assert output["status"] == "stopped"
    assert output["simulated_usage"]["calls"] >= 2
    state = (workspace.directory / "runner_state.json").read_bytes()
    again = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert again.returncode == 0, again.stderr
    assert (workspace.directory / "runner_state.json").read_bytes() == state
    assert json.loads(again.stdout)["simulated_usage"] == output["simulated_usage"]
