import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_assistant.agent.runner import AgentRunner, MockPolicy
from paper_assistant.agent.state import AgentState
from paper_assistant.web.research import ResearchJobService, WebResearchError
from test_agent_tools import tools
from test_web_app import send_request


def payload(**changes):
    return {"topic": "retrieval augmented generation", "start_date": "2023-01-01", "end_date": "2024-01-01",
            "questions": ["What is RAGTruth?"], "target_papers": 1, "max_cost_usd": 0.10, "source_mode": "online", **changes}


def test_start_accepts_key_from_project_env_without_environment_variable(jobs, monkeypatch):
    service, started, release, calls = jobs
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (service.project_root / ".env").write_text("OPENAI_API_KEY=sk-local-dotenv-test", encoding="utf-8")
    result = service.start(payload())
    assert started.wait(5)
    assert result["job"]["task_id"].startswith("web-")
    assert len(calls) == 1
    assert "sk-local-dotenv-test" not in str(result)
    release.set()


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-web-test-not-a-real-key")
    started, release = threading.Event(), threading.Event()
    calls = []
    def execute(command, directory):
        calls.append(command)
        started.set()
        assert release.wait(10)
        state = AgentState({"task_id": directory.name}, "2026-10-24T00:00:00+00:00", "2026-10-24T00:00:00+00:00",
                           status="stopped", stop_reason="insufficient_evidence")
        state.observations = [{"tool": "verify_citations", "result": {"success": True, "data": {
            "answer": "<script>literal answer</script>", "insufficient_evidence": True, "citations": []}}}]
        state.save(directory / "runner_state.json")
        return 0
    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    yield service, started, release, calls
    release.set()
    service.close()
    for worker in service._workers.values():
        worker.join(timeout=3)


def wait_finished(service, task_id):
    service._workers[task_id].join(timeout=3)
    return service.snapshot(task_id)


def test_status_load_and_snapshot_do_not_execute_or_expose_key(jobs):
    service, _, _, calls = jobs
    status = service.status()
    assert status["key_ready"] and not status["busy"] and not status["cached_example_ready"]
    assert not calls and "sk-local" not in json.dumps(status)


@pytest.mark.parametrize("changes", [{"topic": ""}, {"target_papers": True}, {"target_papers": 4},
    {"max_cost_usd": .02}, {"max_cost_usd": .11}, {"max_cost_usd": float("nan")},
    {"start_date": "2024-01-01"}, {"end_date": "2023-02-30"}, {"start_date": "2023-01-01T00:00:00Z"},
    {"questions": "question"}, {"questions": []}, {"questions": ["q"*1001]}, {"source_mode": "file"},
    {"source_mode": "cached_example"}, {"api_key": "not-accepted"}, {"path": "arbitrary"}])
def test_invalid_request_never_launches_a_process(jobs, changes):
    service, _, _, calls = jobs
    with pytest.raises(WebResearchError):
        service.start(payload(**changes))
    assert not calls and not service.work_lock.locked()


def test_background_job_shares_lock_and_polling_never_restarts_generation(jobs):
    service, started, release, calls = jobs
    first = service.start(payload())
    task_id = first["job"]["task_id"]
    assert started.wait(2)
    assert service.work_lock.locked()
    assert service.status()["active_job_id"] == task_id
    with pytest.raises(WebResearchError) as error:
        service.start(payload())
    assert error.value.status == 409
    for _ in range(3):
        assert service.snapshot(task_id)["job"]["status"] == "running"
    assert len(calls) == 1
    release.set()
    result = wait_finished(service, task_id)
    assert result["job"]["status"] == "finished" and not service.work_lock.locked()
    assert result["state"]["evidence_answer"]["answer"].startswith("<script>")
    assert "pdf_path" not in json.dumps(result) and "sk-local-web" not in json.dumps(result)


def test_stop_only_creates_cooperative_marker_and_completed_jobs_are_not_restarted(jobs):
    service, started, release, calls = jobs
    task_id = service.start(payload())["job"]["task_id"]
    assert started.wait(2)
    snapshot = service.stop(task_id)
    assert snapshot["job"]["stop_requested"] and snapshot["job"]["status"] == "stopping"
    assert (service.root / task_id / "stop.request").is_file() and len(calls) == 1
    release.set()
    wait_finished(service, task_id)
    service.stop(task_id)
    assert len(calls) == 1


def test_service_restart_reads_completed_job_but_never_resumes_an_unknown_job(jobs):
    service, started, release, calls = jobs
    task_id = service.start(payload())["job"]["task_id"]
    assert started.wait(2)
    restarted = ResearchJobService(service.project_root, threading.Lock(), execute=lambda *args: pytest.fail("No automatic resume"))
    assert restarted.snapshot(task_id)["job"]["status"] == "interrupted"
    release.set()
    wait_finished(service, task_id)
    assert restarted.snapshot(task_id)["job"]["status"] == "finished"
    assert len(calls) == 1


def test_arbitrary_paths_and_non_web_task_ids_cannot_be_read(jobs):
    service = jobs[0]
    for identifier in ("../secret", "tools-stage5-smoke", "web-20261024T000000-aaaaaaaaaaaa/../../other", "%2e%2e", ""):
        with pytest.raises(WebResearchError) as error:
            service.snapshot(identifier)
        assert error.value.status == 404


def test_missing_key_does_not_create_job(jobs, monkeypatch):
    service = jobs[0]
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(WebResearchError, match="OPENAI_API_KEY"):
        service.start(payload())
    assert not service.root.exists()


def test_failures_and_logs_do_not_expose_raw_exceptions_or_private_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-fixture")
    def failure(*args):
        raise RuntimeError("sk-private-secret C:/private/file")
    service = ResearchJobService(tmp_path, threading.Lock(), execute=failure)
    task_id = service.start(payload())["job"]["task_id"]
    result = wait_finished(service, task_id)
    assert result["job"]["status"] == "failed"
    assert "private" not in json.dumps(result) and not service.work_lock.locked()


def test_http_research_routes_retain_origin_validation_and_do_not_serve_task_files(jobs):
    service, started, release, calls = jobs
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501), question_lock=service.work_lock)
    assert send_request(server, "/api/research/status", method="GET")[0] == 200
    assert send_request(server, "/research.js", method="GET")[0] == 200
    assert send_request(server, "/api/research/jobs", payload=payload(), origin="https://other.invalid")[0] == 403
    assert not calls
    status, _, body = send_request(server, "/api/research/jobs", payload=payload(), origin="http://127.0.0.1:8501")
    assert status == 202 and started.wait(2)
    identifier = json.loads(body)["job"]["task_id"]
    assert send_request(server, "/api/research/jobs", payload=payload())[0] == 409
    assert send_request(server, "/api/research/jobs/" + identifier, method="GET")[0] == 200
    assert send_request(server, "/api/research/jobs/../secret", method="GET")[0] == 404
    assert send_request(server, "/data/research_runs/" + identifier + "/web_process.log", method="GET")[0] == 404
    # The retired local Q&A endpoint must never start a second request.
    assert send_request(server, "/api/ask")[0] == 404
    release.set()
    wait_finished(service, identifier)


def test_cooperative_runner_stop_preserves_previous_step_and_prevents_second_decision(tmp_path):
    runtime = tools(tmp_path)
    controller = AgentRunner(runtime, MockPolicy(lambda view: {"kind": "tool", "tool": "list_documents", "arguments": {}}))
    state = controller.run(stop_requested=lambda: controller.state.steps >= 1)
    assert state["stop_reason"] == "user_stopped" and state["usage"]["calls"] == 1
    assert state["tool_event_count"] == 1


def test_real_cli_child_honors_marker_before_any_api_call(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fixture-no-api-request")
    project = Path(__file__).parents[1]
    service = ResearchJobService(project, threading.Lock())
    service.root = tmp_path / "runs"
    def execute(command, directory):
        (directory / "stop.request").write_text("stop", encoding="utf-8")
        return service._run_process(command, directory)
    service._execute = execute
    task_id = service.start(payload())["job"]["task_id"]
    # Join only the explicit worker for this service; no network or model is used.
    worker = service._workers[task_id]
    worker.join(timeout=15)
    result = service.snapshot(task_id)
    assert result["job"]["status"] == "finished"
    assert result["state"]["stop_reason"] == "user_stopped" and result["state"]["usage"]["calls"] == 0
