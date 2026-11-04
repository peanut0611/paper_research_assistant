"""Baseline collection crosses the existing HTTP interface, never a paid model."""

import json
import threading
from types import SimpleNamespace

import pytest

from paper_assistant.evaluation import capture_case
from paper_assistant.web.jobs import ResearchJobService
from paper_assistant.research.state import AgentState
from tests.web.test_web_app import send_request
from tests.research.test_conversation import parent, policy_factory
from paper_assistant.research.conversation import run_turn


def case():
    return {
        "id": "tool-use-comparison",
        "web_payload": {"topic": "language model tool use", "questions": ["比较两篇论文的方法。"],
                        "start_date": "2020-01-01", "end_date": "2024-01-01",
                        "target_papers": 2, "max_cost_usd": .10},
        "followups": [],
        "expected": {"review_only": "评分依据不能传给模型"},
    }


def http_client(service):
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    def request(method, path, payload=None):
        status, _, body = send_request(server, path, method=method, payload=payload)
        return status, json.loads(body)
    return request


def test_missing_key_is_not_an_executed_or_failed_model_case(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    service = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *a: pytest.fail("paid call"))
    try:
        result = capture_case(case(), http_client(service), mode="online", execute=True,
                              evidence_kind="controlled_web_test")
    finally:
        service.close()
    assert result["execution_status"] == "not_executed"
    assert "api_key_not_ready" in result["blockers"]
    assert result["evidence_kind"] == "controlled_web_test"
    assert result["review"]["verdict"] == "not_reviewed"
    assert result["research"] is None
    assert not (tmp_path / "data").exists()


def test_target_budget_is_recorded_as_rejected_without_silently_changing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-only")
    service = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *a: pytest.fail("paid call"))
    example = case()
    example["web_payload"]["max_cost_usd"] = .50
    try:
        result = capture_case(example, http_client(service), mode="online", execute=True,
                              evidence_kind="controlled_web_test")
    finally:
        service.close()
    assert result["execution_status"] == "http_rejected"
    assert result["input"]["max_cost_usd"] == .50
    assert result["http_log"][-1]["status"] == 400
    assert not (tmp_path / "data").exists()


def test_collects_real_http_results_without_sending_rubric_or_awarding_a_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-only")
    def execute(command, directory):
        state = AgentState({"task_id": directory.name}, "2026-11-04T00:00:00Z", "2026-11-04T00:00:00Z",
                           status="stopped", stop_reason="insufficient_evidence")
        state.usage.update(tokens=120, cost_usd=.004, calls=1)
        state.observations = [{"tool": "verify_citations", "result": {"success": True, "data": {
            "answer": "现有正文证据不足。", "insufficient_evidence": True, "citations": []}}}]
        state.save(directory / "runner_state.json")
        return 0
    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    try:
        result = capture_case(case(), http_client(service), mode="online", execute=True,
                              evidence_kind="controlled_web_test", poll_interval=.001)
    finally:
        service.close()
    assert result["execution_status"] == "executed"
    assert result["research"]["state"]["stop_reason"] == "insufficient_evidence"
    assert result["research"]["state"]["usage"]["tokens"] == 120
    assert result["research"]["state"]["evidence_answer"]["answer"] == "现有正文证据不足。"
    assert "expected" not in result["research"]["job"]["request"]
    assert result["review"]["verdict"] == "not_reviewed"


def test_lost_submission_response_is_checkpointed_and_never_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-only")
    service = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *a: pytest.fail("paid call"))
    request = http_client(service)
    submissions, checkpoints = [], []
    def unreliable(method, path, payload=None):
        if method == "POST":
            submissions.append(path)
            raise OSError("connection lost after possible submission")
        return request(method, path, payload)
    try:
        result = capture_case(case(), unreliable, mode="online", execute=True,
                              evidence_kind="controlled_web_test",
                              checkpoint=lambda data: checkpoints.append(json.loads(json.dumps(data))))
    finally:
        service.close()
    assert result["execution_status"] == "submission_unknown"
    assert submissions == ["/api/research/jobs"]
    assert any(row["execution_status"] == "submitting_research" for row in checkpoints)
    assert result["review"]["verdict"] == "not_reviewed"


def test_collects_continuous_followups_using_the_actual_conversation_routes(parent, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-only")
    def execute(command, directory):
        if "chat" in command:
            run_turn(directory.parent.parent, directory.name, workspace_factory=lambda *a, **kw: parent,
                     policy_factory=policy_factory([]))
        else:
            for name in ("runner_state.json", "tool_trace.json", "workspace.json"):
                (directory / name).write_bytes((parent.directory / name).read_bytes())
        return 0
    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    example = case()
    example["followups"] = ["刚才的方法如何工作？", "它有哪些局限？"]
    try:
        result = capture_case(example, http_client(service), mode="online", execute=True,
                              evidence_kind="controlled_web_test", poll_interval=.001)
    finally:
        service.close()
    assert result["execution_status"] == "executed"
    assert len(result["followups"]) == 2
    final = result["followups"][-1]["snapshot"]
    assert [turn["question"] for turn in final["turns"]] == example["followups"]
    assert all(turn["answer"]["citations"] for turn in final["turns"])
    assert final["usage"]["cost_usd"] > 0


def test_fixed_material_gaps_block_submission_without_falling_back_to_online(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-only")
    service = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *a: pytest.fail("paid call"))
    try:
        result = capture_case(case(), http_client(service), mode="fixed", execute=True,
                              evidence_kind="controlled_web_test", material_errors=["react-v1:pdf_missing"])
    finally:
        service.close()
    assert result["execution_status"] == "not_executed"
    assert "react-v1:pdf_missing" in result["blockers"]
    assert result["input"]["source_mode"] == "cached_example"
    assert all(row["method"] == "GET" for row in result["http_log"])


def test_lost_poll_preserves_task_identity_and_last_successful_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-only")
    release = threading.Event()
    service = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *a: release.wait(10) and 1)
    transport = http_client(service)
    def unreliable(method, path, payload=None):
        if method == "GET" and path.startswith("/api/research/jobs/"):
            raise OSError("lost polling response")
        return transport(method, path, payload)
    try:
        result = capture_case(case(), unreliable, mode="online", execute=True,
                              evidence_kind="controlled_web_test", poll_interval=.001)
    finally:
        release.set()
        service.close()
    assert result["execution_status"] == "collection_failed"
    assert result["research"]["job"]["task_id"] == result["job_id"]
    assert result["collection_error"]["error"] == "transport_failure_outcome_unknown_do_not_replay"
