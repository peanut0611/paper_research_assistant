import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_assistant.research.conversation import ConversationStore, run_turn
from paper_assistant.research.state import AgentState
from paper_assistant.web.jobs import ResearchJobService, WebResearchError
from tests.research.test_conversation import parent, payload, policy_factory, manage_payload
from tests.web.test_research_web import payload as research_payload
from tests.web.test_web_app import send_request


JOB = "web-20261027T000000-aaaaaaaaaaaa"


@pytest.fixture
def discussion(parent, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-chat-fixture")
    started, release = threading.Event(), threading.Event()
    calls, views = [], []
    def execute(command, directory):
        calls.append(command)
        started.set()
        assert release.wait(10)
        run_turn(directory.parent.parent, directory.name, workspace_factory=lambda *a, **kw: parent,
                 policy_factory=policy_factory(views))
        return 0
    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    directory = service._directory(JOB)
    directory.mkdir(parents=True)
    for name in ("runner_state.json", "tool_trace.json", "workspace.json"):
        (directory / name).write_bytes((parent.directory / name).read_bytes())
    job = {"task_id": JOB, "status": "finished", "created_at": "2026-10-27T00:00:00Z",
           "stop_requested": False, "request": research_payload()}
    service._save_job(job)
    yield service, started, release, calls, views
    release.set()
    for worker in service._chat_workers.values():
        worker.join(timeout=10)
    service.close()


def test_get_and_duplicate_post_are_read_only_or_idempotent_and_lock_out_other_tasks(discussion):
    service, started, release, calls, views = discussion
    before = service.conversation(JOB)
    assert before["available"] and before["turns"] == [] and not calls
    assert not (service._directory(JOB) / "conversation.json").exists()
    request = payload()
    request.pop("max_cost_usd")
    first = service.ask(JOB, request)
    assert first["turns"][0]["max_cost_usd"] == .05
    assert started.wait(3)
    second = service.ask(JOB, payload())
    assert first["active_turn_id"] == second["active_turn_id"]
    assert len(calls) == 1
    assert service.status()["active_job_id"] == JOB and service.status()["busy"]
    with pytest.raises(WebResearchError) as duplicate:
        service.ask(JOB, payload("different", request_id="request-test-00000001"))
    assert duplicate.value.status == 400
    with pytest.raises(WebResearchError) as busy:
        service.ask(JOB, payload(request_id="request-test-00000002"))
    assert busy.value.status == 409
    with pytest.raises(WebResearchError) as research_busy:
        service.start(research_payload())
    assert research_busy.value.status == 409
    release.set()
    service._chat_workers[first["active_turn_id"]].join(timeout=5)
    result = service.conversation(JOB)
    assert result["turns"][0]["answer"]["citations"]
    assert not result["active_turn_id"] and not service.work_lock.locked()
    assert result["usage"]["cost_usd"] > 0
    # A new server instance reads all messages and costs without starting work.
    restarted = ResearchJobService(service.project_root, threading.Lock(), execute=lambda *args: pytest.fail("GET replayed model"))
    assert restarted.conversation(JOB)["turns"] == result["turns"]
    assert restarted.conversation(JOB)["remaining_usd"] == result["remaining_usd"]
    assert service.ask(JOB, payload())["turns"] == result["turns"]
    assert len(calls) == 1


def test_stop_marker_prevents_model_calls_and_survives_refresh(discussion):
    service, started, release, calls, views = discussion
    first = service.ask(JOB, payload())
    assert started.wait(3)
    stopped = service.stop_conversation(JOB)
    assert stopped["turns"][0]["status"] == "stopping"
    release.set()
    service._chat_workers[first["active_turn_id"]].join(timeout=5)
    result = service.conversation(JOB)
    assert result["turns"][0]["stop_reason"] == "user_stopped"
    assert not views and result["usage"]["cost_usd"] == 0


def test_http_chat_routes_keep_origin_path_and_payload_checks(discussion):
    service, _, release, calls, _ = discussion
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    route = f"/api/research/jobs/{JOB}/conversation"
    assert send_request(server, route, method="GET")[0] == 200
    assert send_request(server, route, payload=payload(), origin="https://other.invalid")[0] == 403
    assert send_request(server, route, payload={**payload(), "document_ids": ["other-task"]})[0] == 400
    assert send_request(server, "/api/research/jobs/../conversation", method="GET")[0] == 404
    assert not calls
    status, _, content = send_request(server, route, payload=payload())
    assert status == 202
    assert "sk-local-chat-fixture" not in content.decode()
    assert send_request(server, route + "/stop", payload={})[0] == 200
    release.set()


def test_unindexed_task_cannot_start_discussion(discussion):
    service = discussion[0]
    path = service._directory(JOB) / "workspace.json"
    manifest = json.loads(path.read_bytes())
    manifest["active_index"] = None
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(WebResearchError) as error:
        service.ask(JOB, payload())
    assert error.value.status == 409
    assert not (service._directory(JOB) / "conversation.json").exists()


def test_restart_does_not_reexecute_unknown_turn_and_reserves_whole_budget(discussion):
    service = discussion[0]
    store = ConversationStore(service._directory(JOB))
    turn = store.begin(payload())
    store.update(turn["id"], status="running")
    result = service.conversation(JOB)
    assert result["turns"][0]["status"] == "interrupted"
    assert result["remaining_usd"] == .45 and not discussion[3]


def test_manage_routes_create_switch_and_reset_without_model_requests(discussion, monkeypatch):
    service = discussion[0]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # Context management is local.
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    route = f"/api/research/jobs/{JOB}/conversation"
    first = service.conversation(JOB)
    operation = manage_payload(first)
    assert send_request(server, route + "/new", payload=operation, origin="https://other.invalid")[0] == 403
    assert len(service.conversation(JOB)["threads"]) == 1
    status, _, body = send_request(server, route + "/new", payload=operation)
    assert status == 200
    new = json.loads(body)
    assert new["thread_id"] != first["thread_id"] and new["turns"] == []
    assert send_request(server, route + "/new", payload=operation)[0] == 200
    assert len(service.conversation(JOB)["threads"]) == 2
    assert send_request(server, route + "/select", payload={"thread_id": first["thread_id"]})[0] == 200
    status, _, body = send_request(server, route + "/reset", payload=manage_payload(first, "reset-http-00000001"))
    assert status == 200 and json.loads(body)["generation"] == 1
    assert send_request(server, route + "/select", payload={"thread_id": ["invalid"]})[0] == 400
    assert send_request(server, route + "/select", payload={"thread_id": "nonexistent"})[0] == 400
    assert not discussion[3] and not service.work_lock.locked()
    restarted = ResearchJobService(service.project_root, threading.Lock(), execute=lambda *a: pytest.fail("management called model"))
    assert restarted.conversation(JOB)["generation"] == 1
    assert len(restarted.conversation(JOB)["threads"]) == 2


def test_active_turn_blocks_all_context_changes_and_stale_question_does_not_run(discussion):
    service, started, release, calls, views = discussion
    original = service.conversation(JOB)
    active = service.ask(JOB, payload())
    assert started.wait(3)
    for action, operation in (("new", manage_payload(original)), ("reset", manage_payload(original)),
                              ("select", {"thread_id": original["thread_id"]})):
        with pytest.raises(WebResearchError) as error:
            service.manage_conversation(JOB, action, operation)
        assert error.value.status == 409
        assert service.work_lock.locked()
    release.set()
    service._chat_workers[active["active_turn_id"]].join(timeout=5)
    service.manage_conversation(JOB, "reset", manage_payload(original))
    with pytest.raises(WebResearchError) as stale:
        service.ask(JOB, {**payload(request_id="stale-question-000001"), "thread_id": original["thread_id"], "generation": 0})
    assert stale.value.status == 400 and len(calls) == 1
    snapshot = service.conversation(JOB)
    assert snapshot["turns"] == [] and snapshot["all_turn_count"] == 1
    assert not service.work_lock.locked()
