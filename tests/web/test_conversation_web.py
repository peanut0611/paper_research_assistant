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
    assert first["turns"][0]["max_cost_usd"] == .10
    assert started.wait(3)
    second = service.ask(JOB, {**payload(), "max_cost_usd": .10})
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
    assert service.ask(JOB, {**payload(), "max_cost_usd": .10})["turns"] == result["turns"]
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


def test_legacy_threads_remain_readable_but_new_and_reset_are_retired(discussion, monkeypatch):
    service = discussion[0]
    store = ConversationStore(service._directory(JOB))
    first = store.snapshot()
    store.manage("new", manage_payload(first))  # Existing shared-paper history.
    second = store.snapshot()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    route = f"/api/research/jobs/{JOB}/conversation"
    for action in ("new", "reset"):
        status, _, body = send_request(server, route + "/" + action, payload=manage_payload(second))
        assert status == 410 and "新建研究对话" in json.loads(body)["error"]
    assert send_request(server, route + "/select", payload={"thread_id": first["thread_id"]})[0] == 200
    restored = service.conversation(JOB)
    assert restored["legacy_shared_threads"] and len(restored["threads"]) == 2
    assert restored["research_dialogue_id"] == JOB
    assert not discussion[3] and not service.work_lock.locked()


def test_active_turn_blocks_all_context_changes_and_stale_question_does_not_run(discussion):
    service, started, release, calls, views = discussion
    original = service.conversation(JOB)
    active = service.ask(JOB, payload())
    assert started.wait(3)
    for action, operation in (("new", manage_payload(original)), ("reset", manage_payload(original)),
                              ("select", {"thread_id": original["thread_id"]})):
        with pytest.raises(WebResearchError) as error:
            service.manage_conversation(JOB, action, operation)
        assert error.value.status == (409 if action == "select" else 410)
        assert service.work_lock.locked()
    release.set()
    service._chat_workers[active["active_turn_id"]].join(timeout=5)
    legacy = ConversationStore(service._directory(JOB))
    data = legacy.load()
    data["threads"][0]["generation"] += 1  # Persisted legacy reset, no longer callable.
    legacy.save(data)
    with pytest.raises(WebResearchError) as stale:
        service.ask(JOB, {**payload(request_id="stale-question-000001"), "thread_id": original["thread_id"], "generation": 0})
    assert stale.value.status == 400 and len(calls) == 1
    snapshot = service.conversation(JOB)
    assert snapshot["turns"] == [] and snapshot["all_turn_count"] == 1
    assert not service.work_lock.locked()


def test_two_web_research_dialogues_keep_papers_history_permissions_and_usage_after_restart(tmp_path, sample_pdf, monkeypatch):
    from paper_assistant.research.agent import MockPolicy, ResearchAgent
    from paper_assistant.research.task import ResearchTask
    from paper_assistant.research.toolset import ResearchTools
    from paper_assistant.research.workspace import ResearchWorkspace, ExtractedPDF
    from paper_assistant.tools.knowledge.pdf_parser import PageDocument
    from tests.research.test_conversation import answer_action
    from tests.research.test_research_workspace import warmed, WorkspaceEmbedder
    from tests.tools.test_paper_fetcher import candidates

    monkeypatch.setenv("OPENAI_API_KEY", "sk-isolated-no-network")
    fetcher = warmed(tmp_path, sample_pdf, count=2)
    scopes, projected = {}, {}
    def workspace_factory(*args, **kwargs):
        marker = "ALPHA_ONLY_MARKER" if args[2][0].arxiv_id == candidates()[0].arxiv_id else "BETA_ONLY_MARKER"
        def controlled_parser(path, config):
            return ExtractedPDF((PageDocument("fixture", path.name, 1, f"Hybrid Retrieval describes {marker}."),))
        return ResearchWorkspace(*args, **kwargs, fetcher=fetcher,
                                 embedder_factory=WorkspaceEmbedder, parser=controlled_parser)
    def execute(command, directory):
        if "chat" in command:
            run_turn(directory.parent.parent, directory.name, workspace_factory=workspace_factory,
                     policy_factory=policy_factory(projected[directory.parent.parent.name]))
            return 0
        topic = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--topic="))
        number = 0 if topic == "研究 A" else 1
        task = ResearchTask(topic, (f"问题 {number}",), candidates()[0].submitted_at.replace(month=1, day=1),
                            candidates()[0].submitted_at.replace(year=2024, month=1, day=1), target_papers=1)
        source = workspace_factory(directory.name, task, (candidates()[number],), root=directory.parent)
        scopes[directory.name] = source
        projected[directory.name] = []
        def choose(view):
            row = view["candidates"][0]
            if row["status"] in {"abstract_only", "downloaded"}:
                return {"kind": "tool", "tool": "fetch_paper" if row["status"] == "abstract_only" else "ingest_paper",
                        "arguments": {"arxiv_id": row["arxiv_id"]}}
            return answer_action(view)
        assert ResearchAgent(ResearchTools(source), MockPolicy(choose)).run()["stop_reason"] == "sufficient_evidence"
        return 0
    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    ids, snapshots = [], []
    try:
        for number, topic in enumerate(("研究 A", "研究 B")):
            code, _, body = send_request(server, "/api/research/jobs", payload=research_payload(topic=topic, questions=[f"问题 {number}"]))
            assert code == 202
            identifier = json.loads(body)["job"]["task_id"]
            ids.append(identifier)
            service._workers[identifier].join(5)
            assert service.snapshot(identifier)["job"]["status"] == "finished"
            assert service.conversation(identifier)["usage"]["cost_usd"] == 0
            route = f"/api/research/jobs/{identifier}/conversation"
            code, _, body = send_request(server, route, payload=payload(f"只讨论 {topic}", request_id=f"isolated-question-{number:04d}"))
            assert code == 202
            active = json.loads(body)["active_turn_id"]
            service._chat_workers[active].join(5)
            chat = service.conversation(identifier)
            assert len(chat["turns"]) == 1 and chat["usage"]["cost_usd"] > 0
            cited = chat["turns"][0]["answer"]["citations"]
            assert {row["source"]["arxiv_id"] for row in cited} == {candidates()[number].arxiv_id}
            snapshots.append(chat)
        assert scopes[ids[0]].index_directory != scopes[ids[1]].index_directory
        assert service.conversation(ids[0]) == snapshots[0]
        foreign_id = scopes[ids[0]].list_documents()[0]["document_id"]
        from paper_assistant.research.conversation import ConversationTools, ConversationWorkspace
        restricted = ConversationTools(ConversationWorkspace(scopes[ids[1]], tmp_path / "permission-probe", "范围", .05), offline=True)
        absent = restricted.call("search_documents", {"query": "ALPHA_ONLY_MARKER", "method": "bm25"})
        assert absent["success"] and absent["data"]["results"] == []
        for tool, args in (("read_pages", {"document_id": foreign_id, "start_page": 1}),
                           ("search_documents", {"query": "Hybrid", "document_ids": [foreign_id]}),
                           ("verify_citations", {"answer": "历史证据 [E1]", "insufficient_evidence": False,
                                                 "citations": [{"evidence_id": "E1", "quote": "Hybrid"}]})):
            assert not restricted.call(tool, args)["success"]
        assert all("只讨论 研究 A" not in json.dumps(view.get("conversation_history", []), ensure_ascii=False)
                   for view in projected[ids[1]])
        restarted = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *a: pytest.fail("read executed model"))
        assert len(restarted.dialogues()["dialogues"]) == 2
        for identifier, previous in zip(ids, snapshots):
            assert restarted.conversation(identifier) == previous
            assert restarted.snapshot(identifier)["state"]["candidates"][0]["arxiv_id"] in {row.arxiv_id for row in candidates()[:2]}
    finally:
        service.close()


def test_discussion_workspace_enforces_two_minute_deadline_through_web(discussion):
    service, started, release, calls, views = discussion
    first = service.ask(JOB, payload())
    release.set()
    service._chat_workers[first["active_turn_id"]].join(timeout=5)
    assert views
    assert views[0]["task"]["limits"]["max_runtime_seconds"] == 120
    assert service.conversation(JOB)["max_turn_cost_usd"] == .10
    assert service.conversation(JOB)["max_runtime_seconds"] == 120


def test_followup_timeout_during_model_request_preserves_charge_and_stops_tools(discussion, parent):
    from tests.research.test_agent_runner import Clock
    from paper_assistant.research.agent import MockPolicy
    service, started, release, calls, views = discussion
    clock = Clock()
    decisions = []
    def choose(view):
        decisions.append(view)
        clock.seconds = 121
        return {"kind": "tool", "tool": "list_documents", "arguments": {}}
    def execute(command, directory):
        run_turn(directory.parent.parent, directory.name, workspace_factory=lambda *a, **kw: parent,
                 policy_factory=lambda history: MockPolicy(choose), now=clock.now, monotonic=clock.mono)
        return 0
    service._execute = execute
    result = service.ask(JOB, payload())
    service._chat_workers[result["turns"][0]["id"]].join(timeout=5)
    turn = service.conversation(JOB)["turns"][0]
    assert turn["stop_reason"] == "runtime_budget_exhausted"
    assert turn["usage"]["calls"] == 1 and turn["usage"]["cost_usd"] > 0
    assert len(decisions) == 1
    assert turn["answer"] is None


def test_delete_waits_for_stopped_followup_and_stale_followup_cannot_recreate(discussion):
    service, started, release, calls, views = discussion
    server = SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    route = f"/api/research/jobs/{JOB}"
    turn = service.ask(JOB, payload())
    assert started.wait(3)
    assert send_request(server, route + "/delete", payload={"confirmed": True})[0] == 409
    assert send_request(server, route + "/conversation/stop", payload={})[0] == 200
    assert send_request(server, route + "/delete", payload={"confirmed": True})[0] == 409
    release.set()
    service._chat_workers[turn["active_turn_id"]].join(timeout=5)
    assert send_request(server, route + "/delete", payload={"confirmed": True})[0] == 200
    assert send_request(server, route + "/conversation", payload=payload())[0] == 410
    assert not (service.root / JOB).exists() and len(calls) == 1


def test_restart_does_not_allow_deleting_a_still_running_followup(discussion):
    service, started, release, calls, _ = discussion
    turn = service.ask(JOB, payload())
    assert started.wait(3)
    restarted = ResearchJobService(service.project_root, threading.Lock(), execute=lambda *a: 99)
    server = SimpleNamespace(research=restarted, server_address=("127.0.0.1", 8501))
    route = f"/api/research/jobs/{JOB}/delete"
    assert send_request(server, route, payload={"confirmed": True})[0] == 409
    release.set()
    service._chat_workers[turn["active_turn_id"]].join(timeout=5)
    assert send_request(server, route, payload={"confirmed": True})[0] == 200
    assert len(calls) == 1 and not (service.root / JOB).exists()
