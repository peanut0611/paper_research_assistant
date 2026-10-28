import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from paper_assistant.research.agent import MockPolicy, ResearchAgent
from paper_assistant.research.conversation import (ALLOWED_TOOLS, ConversationAgent, ConversationPolicy,
    ConversationStore, ConversationTools, ConversationWorkspace, run_turn)
from paper_assistant.research.workspace import ResearchWorkspace, WorkspaceError
from paper_assistant.research.state import AgentState, RunnerConfig
from tests.research.test_agent_model import FakeClient, envelope
from tests.research.test_agent_tools import tools
from tests.tools.test_paper_fetcher import candidates, FakeResponse
from tests.research.test_research_workspace import WorkspaceEmbedder


def answer_action(view):
    if not view["evidence"]:
        return {"kind": "tool", "tool": "search_documents", "arguments": {"query": "Hybrid Retrieval", "method": "bm25"}}
    if not view["completion"]["can_finish_sufficient_evidence"]:
        identifier, row = next(iter(view["evidence"].items()))
        return {"kind": "tool", "tool": "verify_citations", "arguments": {
            "answer": f"正文讨论了检索 [{identifier}]", "insufficient_evidence": False,
            "citations": [{"evidence_id": identifier, "quote": row["text"][:20]}]}}
    return {"kind": "finish", "reason": "sufficient_evidence", "summary": "Checked"}


@pytest.fixture
def parent(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    def choose(view):
        row = next(row for row in view["candidates"] if row["arxiv_id"] == "2401.00544")
        if row["status"] == "abstract_only":
            return {"kind": "tool", "tool": "fetch_paper", "arguments": {"arxiv_id": row["arxiv_id"]}}
        if row["status"] == "downloaded":
            return {"kind": "tool", "tool": "ingest_paper", "arguments": {"arxiv_id": row["arxiv_id"]}}
        return answer_action(view)
    agent = ResearchAgent(runtime, MockPolicy(choose))
    assert agent.run()["stop_reason"] == "sufficient_evidence"
    return runtime.workspace


def payload(question="刚才的方法具体如何工作？", request_id="request-test-00000001", budget=.05):
    return {"question": question, "request_id": request_id, "max_cost_usd": budget}


def policy_factory(captured):
    def choose(projected):
        captured.append(projected)
        # The real provider projection is a list of evidence records.
        view = {**projected, "evidence": {row["evidence_id"]: row for row in projected["evidence"]}}
        return envelope(answer_action(view))
    return lambda history: ConversationPolicy(history, client=FakeClient(choose=choose), token_counter=lambda request: 1000)


def test_two_followups_retrieve_again_keep_history_and_preserve_original_artifacts(parent):
    directory = parent.directory
    originals = {name: (directory / name).read_bytes() for name in ("runner_state.json", "tool_trace.json", "workspace.json")}
    store, captured = ConversationStore(directory), []
    assert not store.path.exists()
    assert store.snapshot()["initial"]["answer"]["citations"]
    assert not store.path.exists()  # GET/read does not create a conversation.
    for number, question in enumerate(("刚才的方法具体如何工作？", "它的局限是什么？")):
        turn = store.begin(payload(question, f"request-test-{number:08d}"))
        result = run_turn(directory, turn["id"], workspace_factory=lambda *a, **kw: parent,
                          policy_factory=policy_factory(captured))
        assert result["stop_reason"] == "sufficient_evidence"
        store.update(turn["id"], status="finished")
        trace = json.loads((store.turn_directory(turn["id"]) / "tool_trace.json").read_bytes())
        assert [row["tool"] for row in trace["events"]] == ["search_documents", "verify_citations"]
    assert "刚才的方法具体如何工作" in json.dumps(captured[-1]["conversation_history"], ensure_ascii=False)
    assert captured[-1]["task"]["research_questions"] == ["它的局限是什么？"]
    snapshot = store.snapshot()
    assert len(snapshot["turns"]) == 2 and snapshot["usage"]["tokens"] > 0
    assert snapshot["remaining_usd"] < .50
    for name, content in originals.items():
        assert (directory / name).read_bytes() == content
    with pytest.raises(ValueError, match="already attempted"):
        run_turn(directory, turn["id"], workspace_factory=lambda *a, **kw: parent, policy_factory=policy_factory(captured))


def test_discussion_tools_cannot_discover_ingest_or_cite_previous_turn_without_retrieval(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    workspace = ConversationWorkspace(parent, store.turn_directory(turn["id"]), "追问", .05)
    runtime = ConversationTools(workspace, offline=True)
    assert {spec["name"] for spec in runtime.tool_specs()} == ALLOWED_TOOLS
    for name in ("search_literature", "fetch_paper", "ingest_paper"):
        result = runtime.call(name, {"arxiv_id": "2401.00544"})
        assert not result["success"] and result["error"]["code"] == "unknown_tool"
    assert not runtime.call("verify_citations", {"answer": "旧答案 [E1]", "insufficient_evidence": False,
        "citations": [{"evidence_id": "E1", "quote": "Hybrid"}]})["success"]
    assert not runtime.call("read_pages", {"document_id": "another-task-paper", "start_page": 1})["success"]
    assert not runtime.call("search_documents", {"query": "Hybrid", "document_ids": ["another-task-paper"]})["success"]


def test_insufficient_answer_must_be_validated_and_stop_can_prevent_model_request(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    workspace = ConversationWorkspace(parent, store.turn_directory(turn["id"]), "企业维护成本？", .05)
    runtime = ConversationTools(workspace, offline=True)
    def choose(view):
        if not view["decisions"] or view["observations"]:
            return {"kind": "finish", "reason": "insufficient_evidence", "summary": "缺少成本依据"}
        return {"kind": "tool", "tool": "verify_citations", "arguments": {
            "answer": "当前取得的论文材料不足以回答企业维护成本。", "insufficient_evidence": True, "citations": []}}
    agent = ConversationAgent(runtime, MockPolicy(choose))
    result = agent.run()
    assert result["stop_reason"] == "insufficient_evidence"
    assert any(row.get("error") == "unverified_completion" for row in result["decisions"])
    assert store.snapshot()["turns"][0]["answer"]["insufficient_evidence"]


def test_interrupted_turns_reserve_budget_and_request_ids_detect_duplicate_payloads(parent):
    store = ConversationStore(parent.directory)
    first = payload(budget=.10)
    turn = store.begin(first)
    assert store.begin(first)["id"] == turn["id"]
    assert store.existing(first)["id"] == turn["id"]
    with pytest.raises(ValueError, match="another question"):
        store.existing({**first, "question": "different"})
    assert store.snapshot()["turns"][0]["status"] == "interrupted"
    assert store.snapshot()["remaining_usd"] == .40
    for index in range(2, 6):
        store.begin(payload(request_id=f"request-test-{index:08d}", budget=.10))
    assert store.snapshot()["remaining_usd"] == 0
    with pytest.raises(ValueError, match="预算"):
        store.begin(payload(request_id="request-over-budget"))


@pytest.mark.parametrize("changes", [{"question": ""}, {"question": "a" * 1001}, {"request_id": "../escape"},
    {"max_cost_usd": True}, {"max_cost_usd": float("nan")}, {"max_cost_usd": .11}, {"max_cost_usd": .01}])
def test_invalid_questions_budgets_and_ids_rejected(changes):
    with pytest.raises(ValueError):
        ConversationStore.validate({**payload(), **changes})


def test_long_history_is_bounded_by_production_preflight_and_never_becomes_evidence(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    workspace = ConversationWorkspace(parent, store.turn_directory(turn["id"]), "它的局限？", .05)
    history = [{"role": "assistant" if n % 2 else "user", "text": "历史文字不能当作正文证据" * 900} for n in range(30)]
    policy = ConversationPolicy(history, client=FakeClient())
    agent = ConversationAgent(ConversationTools(workspace, offline=True), policy,
                              config=RunnerConfig(max_model_tokens=60000, report_reserve_tokens=1))
    reservation = policy.estimate(agent.view())
    assert reservation.max_tokens <= 17200
    request = policy._prepared["request"]
    content = json.loads(request["input"][0]["content"])
    assert content["omitted_conversation_messages"] == 24
    assert all(item["truncated"] for item in content["conversation_history"])
    assert not content["evidence"]


def test_unknown_model_outcome_is_charged_and_never_replayed(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    client = FakeClient(replies=[RuntimeError("no response")])
    result = run_turn(parent.directory, turn["id"], workspace_factory=lambda *a, **kw: parent,
        policy_factory=lambda history: ConversationPolicy(history, client=client, token_counter=lambda request: 1000))
    assert result["usage"]["unknown_usage_calls"] == 1 and result["usage"]["cost_usd"] > 0
    store.update(turn["id"], status="failed")
    assert store.snapshot()["remaining_usd"] < .50
    with pytest.raises(ValueError, match="already attempted"):
        run_turn(parent.directory, turn["id"])
    assert len(client.calls) == 1


def test_real_chat_cli_stops_before_any_model_request(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    directory = store.turn_directory(turn["id"])
    (directory / "stop.request").write_text("stop", encoding="utf-8")
    root = Path(__file__).parents[2]
    result = subprocess.run([sys.executable, "-m", "paper_assistant", "chat", "--task-id", parent.task_id,
        "--turn-id", turn["id"], "--root", str(parent.directory.parent)], cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src"), "OPENAI_API_KEY": "sk-fixture-no-api-call"},
        capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    state = AgentState.load(directory / "runner_state.json")
    assert state.stop_reason == "user_stopped" and state.usage["calls"] == 0


def legacy_manifest(parent):
    path = parent.directory / "workspace.json"
    manifest = json.loads(path.read_bytes())
    manifest["processing"]["code_sha256"] = {"agent/workspace.py": "historical-code-before-relocation"}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path.read_bytes()


def reopen(parent, **kwargs):
    return ResearchWorkspace(parent.task_id, kwargs.pop("task", parent.task), parent.registered_candidates,
        root=parent.directory.parent, config=parent.config, fetcher=parent.fetcher,
        embedder_factory=WorkspaceEmbedder, **kwargs)


def test_old_processing_fingerprint_is_readable_without_mutation_but_not_writable(parent):
    original = legacy_manifest(parent)
    with pytest.raises(ValueError, match="processing version"):
        reopen(parent)
    reader = reopen(parent, read_only=True)
    assert reader.search_documents("Hybrid Retrieval", method="hybrid")["results"]
    document = reader.list_documents()[0]
    ready = next(row for row in reader.list_documents() if row["status"] == "fulltext_ready")
    assert reader.read_pages(ready["document_id"], 1)["pages"]
    for action in (lambda: reader.ingest_paper(ready["arxiv_id"]),
                   lambda: reader.register_candidates(reader.registered_candidates),
                   lambda: reader.record_fetch_result(None)):
        with pytest.raises(WorkspaceError) as error:
            action()
        assert error.value.code == "read_only_workspace"
    assert (parent.directory / "workspace.json").read_bytes() == original
    with pytest.raises(ValueError, match="Task/config"):
        reopen(parent, read_only=True, task=replace(parent.task, topic="changed scope"))


def test_readonly_legacy_snapshot_still_checks_pdf_integrity(parent):
    legacy_manifest(parent)
    source = next(row for row in parent.list_documents() if row["status"] == "fulltext_ready")["source"]
    path = parent.directory / source
    path.write_bytes(path.read_bytes() + b"\nchanged source")
    with pytest.raises(WorkspaceError) as error:
        reopen(parent, read_only=True)
    assert error.value.code == "invalid_workspace"


def test_legacy_task_followup_uses_readonly_reader_and_preserves_old_manifest(parent):
    original = legacy_manifest(parent)
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    def factory(*args, **kwargs):
        assert kwargs["read_only"] is True
        return reopen(parent, read_only=True)
    result = run_turn(parent.directory, turn["id"], workspace_factory=factory, policy_factory=policy_factory([]))
    assert result["stop_reason"] == "sufficient_evidence"
    assert (parent.directory / "workspace.json").read_bytes() == original


def test_cli_preflight_error_is_visible_and_does_not_reserve_unused_budget(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    manifest_path = parent.directory / "workspace.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["active_index"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    root = Path(__file__).parents[2]
    result = subprocess.run([sys.executable, "-m", "paper_assistant", "chat", "--task-id", parent.task_id,
        "--turn-id", turn["id"], "--root", str(parent.directory.parent)], cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src"), "OPENAI_API_KEY": ""}, capture_output=True, timeout=15)
    assert result.returncode == 1
    assert "No indexed papers" in result.stderr.decode("utf-8")
    failure = json.loads((store.turn_directory(turn["id"]) / "failure.json").read_bytes())
    assert failure["request_submitted"] is False
    assert not (store.turn_directory(turn["id"]) / "runner_state.json").exists()
    store.update(turn["id"], status="failed")
    snapshot = store.snapshot()
    assert snapshot["remaining_usd"] == .50
    assert snapshot["turns"][0]["failure_stage"] == "preflight"
    assert "No indexed papers" in snapshot["turns"][0]["error"]


def manage_payload(snapshot, request_id="manage-test-00000001"):
    return {"request_id": request_id, "thread_id": snapshot["thread_id"], "generation": snapshot["generation"]}


def finish_discussion(store, parent, question, request_id):
    turn = store.begin(payload(question, request_id))
    run_turn(parent.directory, turn["id"], workspace_factory=lambda *a, **kw: parent, policy_factory=policy_factory([]))
    store.update(turn["id"], status="finished")
    return turn


def test_new_dialogue_has_isolated_history_and_can_switch_back_without_resetting_costs(parent):
    store = ConversationStore(parent.directory)
    original = finish_discussion(store, parent, "第一段对话的独有问题", "request-thread-000001")
    before = store.snapshot()
    operation = manage_payload(before)
    store.manage("new", operation)
    fresh = store.snapshot()
    assert fresh["turns"] == [] and fresh["all_turn_count"] == 1
    assert fresh["thread_id"] != before["thread_id"] and len(fresh["threads"]) == 2
    assert fresh["usage"] == before["usage"] and fresh["remaining_usd"] == before["remaining_usd"]
    store.manage("new", operation)  # Lost HTTP response cannot create a third thread.
    assert store.snapshot()["thread_id"] == fresh["thread_id"] and len(store.load()["threads"]) == 2
    second = finish_discussion(store, parent, "新对话的问题", "request-thread-000002")
    request = json.loads((store.turn_directory(second["id"]) / "request.json").read_bytes())
    assert not any("第一段对话的独有问题" in row["text"] for row in request["history"])
    store.manage("select", {"thread_id": before["thread_id"]})
    restored = store.snapshot()
    assert [row["id"] for row in restored["turns"]] == [original["id"]]
    assert restored["all_turn_count"] == 2
    assert restored["usage"]["cost_usd"] > before["usage"]["cost_usd"]


def test_reset_archives_context_preserves_logs_and_rejects_stale_questions(parent):
    store = ConversationStore(parent.directory)
    turn = finish_discussion(store, parent, "重置前的独有问题", "request-reset-000001")
    before = store.snapshot()
    journal = (store.turn_directory(turn["id"]) / "tool_trace.json").read_bytes()
    operation = manage_payload(before)
    store.manage("reset", operation)
    store.manage("reset", operation)  # Idempotent even though the generation advanced.
    reset = store.snapshot()
    assert reset["thread_id"] == before["thread_id"] and reset["generation"] == 1
    assert reset["turns"] == [] and reset["threads"][0]["archived_turn_count"] == 1
    assert reset["usage"] == before["usage"] and reset["remaining_usd"] == before["remaining_usd"]
    assert (store.turn_directory(turn["id"]) / "tool_trace.json").read_bytes() == journal
    with pytest.raises(ValueError, match="已切换或重置"):
        store.begin({**payload(request_id="request-stale-000001"), "thread_id": before["thread_id"], "generation": 0})
    new = store.begin({**payload("重置后的问题", "request-after-reset1"), "thread_id": reset["thread_id"], "generation": 1})
    history = json.loads((store.turn_directory(new["id"]) / "request.json").read_bytes())["history"]
    assert not any("重置前的独有问题" in row["text"] for row in history)
    assert store.existing(payload("重置前的独有问题", "request-reset-000001"))["id"] == turn["id"]


def test_legacy_thread_migration_is_readonly_until_an_explicit_action(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload())
    data = json.loads(store.path.read_bytes())
    for key in ("threads", "active_thread_id", "actions"):
        data.pop(key)
    for key in ("thread_id", "generation"):
        data["turns"][0].pop(key)
    store.path.write_text(json.dumps(data), encoding="utf-8")
    before = store.path.read_bytes()
    snapshot = store.snapshot()
    assert snapshot["thread_id"] == "initial" and snapshot["turns"][0]["id"] == turn["id"]
    assert store.path.read_bytes() == before
    store.manage("new", manage_payload(snapshot))
    assert len(store.load()["turns"]) == 1 and len(store.load()["threads"]) == 2


@pytest.mark.parametrize("action", ["new", "reset"])
def test_changing_context_never_releases_unknown_call_reservations(parent, action):
    store = ConversationStore(parent.directory)
    for index in range(5):
        store.begin(payload(request_id=f"reserved-request-{index:05d}", budget=.10))
    before = store.snapshot()
    assert before["remaining_usd"] == 0
    store.manage(action, manage_payload(before))
    assert store.snapshot()["remaining_usd"] == 0
    assert store.snapshot()["all_turn_count"] == 5 and store.snapshot()["turns"] == []
    with pytest.raises(ValueError, match="预算"):
        store.begin(payload(request_id="request-bypass-budget"))


@pytest.fixture
def two_papers(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, seed=candidates(), fetch_responses=[FakeResponse(sample_pdf.read_bytes()) for _ in range(2)])
    def choose(view):
        for identifier in ("2401.00544", "2401.00396"):
            row = next(row for row in view["candidates"] if row["arxiv_id"] == identifier)
            if row["status"] != "fulltext_ready":
                return {"kind": "tool", "tool": "ingest_paper" if row["status"] == "downloaded" else "fetch_paper",
                        "arguments": {"arxiv_id": identifier}}
        return answer_action(view)
    agent = ResearchAgent(runtime, MockPolicy(choose))
    assert agent.run()["stop_reason"] == "sufficient_evidence"
    return runtime.workspace


def test_reference_question_finishes_with_metadata_without_body_retrieval_or_quotes(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload("列出刚刚参考的论文列表"))
    original = store.initial()["answer"]
    expected = original["citations"][0]["source"]["document_id"]
    calls = []
    def choose(view):
        calls.append(view)
        if view["completion"]["can_finish_sufficient_evidence"]:
            return envelope({"kind": "finish", "reason": "sufficient_evidence", "summary": "untrusted invented claim"})
        return envelope({"kind": "tool", "tool": "list_references", "arguments": {"scope": "latest_answer"}})
    result = run_turn(parent.directory, turn["id"], workspace_factory=lambda *a, **kw: parent,
        policy_factory=lambda history: ConversationPolicy(history, client=FakeClient(choose=choose), token_counter=lambda request: 1000))
    assert result["stop_reason"] == "sufficient_evidence" and len(calls) == 2
    assert not any(view["evidence"] for view in calls)
    store.update(turn["id"], status="finished")
    answer = store.snapshot()["turns"][0]["answer"]
    assert answer["answer_kind"] == "bibliography" and answer["citations"] == []
    assert [row["document_id"] for row in answer["references"]] == [expected]
    assert "untrusted invented claim" not in answer["answer"]
    trace = json.loads((store.turn_directory(turn["id"]) / "tool_trace.json").read_bytes())
    assert [event["tool"] for event in trace["events"]] == ["list_references"]
    assert "metadata" in answer["validation_scope"]


def test_reference_scopes_distinguish_last_answer_conversation_and_all_indexed(two_papers, tmp_path):
    source = two_papers
    identifiers = [row["document_id"] for row in source.list_documents() if row["status"] == "fulltext_ready"]
    assert len(identifiers) == 2
    history = [{"role": "assistant", "text": "older", "reference_document_ids": [identifiers[0]]},
               {"role": "assistant", "text": "newer", "reference_document_ids": [identifiers[1]]}]
    directory = tmp_path / "reference-tools"
    directory.mkdir()
    runtime = ConversationTools(ConversationWorkspace(source, directory, "列出参考论文", .05, history=history), offline=True)
    last = runtime.call("list_references", {})
    assert last["success"] and [row["document_id"] for row in last["data"]["references"]] == [identifiers[1]]
    both = runtime.call("list_references", {"scope": "conversation"})
    assert {row["document_id"] for row in both["data"]["references"]} == set(identifiers)
    indexed = runtime.call("list_references", {"scope": "indexed"})
    assert len(indexed["data"]["references"]) == 2
    assert all(row["arxiv_id"] in {"2401.00544", "2401.00396"} for row in indexed["data"]["references"])
    assert not runtime.call("list_references", {"scope": "latest_answer", "answer": "arbitrary content claim"})["success"]


def test_missing_or_out_of_scope_reference_records_are_not_replaced_with_candidates(parent, tmp_path):
    directory = tmp_path / "missing-references"
    directory.mkdir()
    workspace = ConversationWorkspace(parent, directory, "列出刚刚引用的论文", .05,
        history=[{"role": "assistant", "text": "no references", "reference_document_ids": []}])
    runtime = ConversationTools(workspace, offline=True)
    result = runtime.call("list_references", {})
    assert result["success"] and result["data"]["insufficient_evidence"] and not result["data"]["references"]
    workspace.reference_history = [["another-task-document"]]
    rejected = runtime.call("list_references", {})
    assert not rejected["success"] and rejected["error"]["code"] == "reference_out_of_scope"
    assert not runtime.call("verify_citations", {"answer": "a scientific claim", "insufficient_evidence": False, "citations": []})["success"]


def test_metadata_validation_counts_as_progress_after_listing_documents(parent):
    store = ConversationStore(parent.directory)
    turn = store.begin(payload("列出刚刚参考的论文列表"))
    request = json.loads((store.turn_directory(turn["id"]) / "request.json").read_bytes())
    runtime = ConversationTools(ConversationWorkspace(parent, store.turn_directory(turn["id"]), request["question"], .05,
                                history=request["history"]), offline=True)
    def choose(view):
        if len(view["observations"]) < 2:
            return {"kind": "tool", "tool": "list_documents", "arguments": {}}
        if not view["completion"]["can_finish_sufficient_evidence"]:
            return {"kind": "tool", "tool": "list_references", "arguments": {}}
        return {"kind": "finish", "reason": "sufficient_evidence", "summary": "Bibliography complete"}
    agent = ConversationAgent(runtime, MockPolicy(choose))
    assert agent.run()["stop_reason"] == "sufficient_evidence"
    assert agent.state.no_progress == 0
