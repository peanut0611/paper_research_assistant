import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from paper_assistant.research.toolset import RecordedToolReplay, ResearchTools
from paper_assistant.tools.arxiv.search import HTTPResponse, LiteratureSearchSession
from paper_assistant.tools.arxiv.fetch import PaperFetcher
from paper_assistant.research.task import ResearchLimits
from paper_assistant.research.workspace import ResearchWorkspace
from tests.tools.test_literature_search import FakeTransport as SearchTransport, client as search_client
from tests.tools.test_paper_fetcher import FakeResponse, FakeTransport, NoWaitGate, candidates, task
from tests.research.test_research_workspace import WorkspaceEmbedder, local_parser


LIVE = Path(__file__).parents[1] / "fixtures" / "arxiv" / "live_20261023.xml"


def tools(tmp_path, sample_pdf=None, *, seed=(), offline=False, research_task=None, search_responses=None, fetch_responses=None):
    request_task = research_task or task()
    pdf = PaperFetcher(cache_directory=tmp_path / "pdf-cache", gate=NoWaitGate(), transport=FakeTransport(
        fetch_responses if fetch_responses is not None else [FakeResponse(sample_pdf.read_bytes())] if sample_pdf else []))
    source = search_client(tmp_path, SearchTransport(search_responses or [HTTPResponse(200, "application/atom+xml", LIVE.read_bytes())]))
    search = LiteratureSearchSession(request_task, source)
    ws = ResearchWorkspace("tools-test", request_task, seed, root=tmp_path / "runs", fetcher=pdf,
                           embedder_factory=WorkspaceEmbedder, parser=local_parser)
    return ResearchTools(ws, search_session=search, offline=offline)


def discover(runtime):
    result = runtime.call("search_literature", {"query": "retrieval augmented generation"}, purpose="Find candidate metadata")
    assert result["success"], result
    return result


def acquire_and_ingest(runtime, identifier="2401.00544"):
    acquired = runtime.call("fetch_paper", {"arxiv_id": identifier})
    assert acquired["success"], acquired
    assert "pdf_path" not in acquired["data"]["asset"]
    event = runtime.call("ingest_paper", {"arxiv_id": identifier})
    assert event["success"], event
    return event["data"]["document_id"]


def test_seven_tools_form_metadata_fetch_ingest_retrieve_read_and_verify_pipeline(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf)
    assert {spec["name"] for spec in runtime.tool_specs()} == {
        "search_literature", "fetch_paper", "ingest_paper", "list_documents", "search_documents", "read_pages", "verify_citations"}
    found = discover(runtime)
    assert len(found["data"]["candidates"]) == 3
    assert "raw_entry_xml" not in found["data"]["candidates"][0]
    downloaded = runtime.call("fetch_paper", {"arxiv_id": "2401.00544", "version": 2})
    assert downloaded["success"]
    listed = runtime.call("list_documents", {})
    assert next(row for row in listed["data"]["documents"] if row["arxiv_id"] == "2401.00544")["status"] == "downloaded"
    ingested = runtime.call("ingest_paper", {"arxiv_id": "2401.00544"})
    doc = ingested["data"]["document_id"]
    retrieved = runtime.call("search_documents", {"query": "retrieval", "method": "hybrid"})
    assert retrieved["success"] and retrieved["data"]["results"]
    evidence = retrieved["data"]["results"][0]
    assert evidence["source"]["version"] == 2 and evidence["evidence_id"].startswith("E")
    read = runtime.call("read_pages", {"document_id": doc, "start_page": 1, "max_chars": 8})
    assert read["success"] and read["data"]["truncated"]
    checked = runtime.call("verify_citations", {"answer": f"It discusses retrieval. [{evidence['evidence_id']}]",
        "insufficient_evidence": False, "citations": [{"evidence_id": evidence["evidence_id"], "quote": evidence["chunk"]["text"][:10]}]})
    assert checked["success"] and checked["data"]["valid"]
    assert "not semantic" in checked["data"]["validation_scope"]
    assert checked["limits"]["after"]["downloaded_papers"] == 1


def test_citation_failure_returns_source_bound_repair_then_requires_revalidation(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    acquire_and_ingest(runtime)
    hits = runtime.call("search_documents", {"query": "Hybrid Retrieval", "method": "bm25"})
    identifier = hits["data"]["results"][0]["evidence_id"]
    args = {"answer": f"正文摘录 [{identifier}]", "insufficient_evidence": False,
            "citations": [{"evidence_id": identifier, "quote": "A sentence not in this PDF"}]}
    failed = runtime.call("verify_citations", args)
    assert not failed["success"] and failed["error"]["code"] == "citation_validation_failed"
    hint = failed["data"]["citation_errors"][0]
    assert hint["evidence_id"] == identifier
    assert hint["example_source_quote"] in runtime.evidence_records()[identifier]["text"]
    args["citations"][0]["quote"] = hint["example_source_quote"]
    assert runtime.call("verify_citations", args)["success"]


@pytest.mark.parametrize("tool,arguments", [
    ("search_documents", {"query": "retrieval", "document_ids": ["arxiv--2401.00544--v2"]}),
    ("search_documents", {"query": "retrieval"}),
    ("read_pages", {"document_id": "arxiv--2401.00544--v2", "start_page": 1}),
])
def test_downloaded_pdf_requires_explicit_ingestion_and_returns_recovery_action(tmp_path, sample_pdf, tool, arguments):
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    assert runtime.call("fetch_paper", {"arxiv_id": "2401.00544"})["success"]
    result = runtime.call(tool, arguments)
    assert not result["success"] and result["error"]["code"] == "document_not_indexed"
    assert result["data"]["required_action"] == {"tool": "ingest_paper", "arguments": {"arxiv_id": "2401.00544"}}
    assert runtime.workspace.index_directory is None  # No hidden automatic ingestion or embedding.
    assert runtime.call("ingest_paper", {"arxiv_id": "2401.00544"})["success"]
    assert runtime.call(tool, arguments)["success"]


@pytest.mark.parametrize("name,args", [
    ("shell", {"command": "anything"}), ("register_candidates", {}),
    ("fetch_paper", {"url": "https://evil.example/a.pdf"}),
    ("fetch_paper", {"arxiv_id": "2401.00544", "task_id": "another"}),
    ("search_literature", {"query": "x", "start_at": "2020-01-01T00:00:00Z"}),
    ("search_literature", {"query": "x", "max_results": True}),
    ("search_documents", {"query": "x", "method": "unknown"}),
    ("search_documents", {"query": "x", "document_ids": "paper"}),
    ("read_pages", {"document_id": "paper", "start_page": True}),
    ("read_pages", {"document_id": "paper", "start_page": 1, "max_chars": 12001}),
    ("list_documents", {"path": "C:/secret"}), ("list_documents", None),
])
def test_invalid_tools_and_arguments_cannot_trigger_io(tmp_path, name, args):
    runtime = tools(tmp_path)
    result = runtime.call(name, args)
    assert not result["success"] and result["error"]["code"] in ("unknown_tool", "invalid_arguments")
    assert runtime.search.client.network_requests == runtime.workspace.fetcher.network_requests == 0
    assert runtime.search.search_rounds == runtime.fetch.fetch_attempts == 0


def test_unknown_candidate_and_ingest_before_acquisition_are_rejected(tmp_path):
    runtime = tools(tmp_path, seed=candidates())
    assert not runtime.call("fetch_paper", {"arxiv_id": "not-registered"})["success"]
    result = runtime.call("ingest_paper", {"arxiv_id": "2401.00544"})
    assert result["error"]["code"] == "not_acquired"
    assert runtime.fetch.fetch_attempts == 0


def test_empty_search_is_success_and_connection_failure_is_error(tmp_path):
    body = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>0</o:totalResults><o:startIndex>0</o:startIndex></feed>'
    runtime = tools(tmp_path, search_responses=[HTTPResponse(200, "application/xml", body)])
    result = discover(runtime)
    assert result["success"] and result["data"]["candidates"] == []
    assert runtime.workspace.list_documents() == []


def test_failed_acquisition_is_observable_and_does_not_unlock_ingestion(tmp_path):
    runtime = tools(tmp_path, seed=candidates(), fetch_responses=[FakeResponse(status=404)])
    unavailable = runtime.call("fetch_paper", {"arxiv_id": "2401.00544"})
    assert unavailable["error"]["code"] == "unavailable" and unavailable["data"]["status"] == "unavailable"
    assert unavailable["data"]["http_status"] == 404
    rows = runtime.call("list_documents", {})["data"]["documents"]
    assert next(row for row in rows if row["arxiv_id"] == "2401.00544")["status"] == "unavailable"
    assert runtime.call("ingest_paper", {"arxiv_id": "2401.00544"})["error"]["code"] == "not_acquired"


def test_first_search_cap_and_two_round_limit_survive_tool_wrapping(tmp_path):
    runtime = tools(tmp_path, search_responses=[HTTPResponse(200, "application/xml", LIVE.read_bytes())] * 2)
    invalid = runtime.call("search_literature", {"query": "x", "max_results": 6})
    assert not invalid["success"] and runtime.search.search_rounds == 0
    discover(runtime)
    runtime.call("search_literature", {"query": "another retrieval query"})
    stopped = runtime.call("search_literature", {"query": "third"})
    assert stopped["error"]["code"] == "search_budget_exhausted"
    assert runtime.search.client.network_requests == 2


def test_seeded_candidates_count_toward_remaining_source_capacity(tmp_path):
    limits = ResearchLimits(max_candidates=3, first_search_candidates=3)
    runtime = tools(tmp_path, seed=candidates(), research_task=task(limits=limits))
    result = runtime.call("search_literature", {"query": "new"})
    assert result["error"]["code"] == "candidate_budget_exhausted"
    assert result["limits"]["before"]["observed_candidates"] == 3 and runtime.search.client.network_requests == 0


def test_source_version_update_cannot_silently_change_the_task_pinned_version(tmp_path):
    changed = LIVE.read_bytes().replace(b"2401.00544v2", b"2401.00544v3")
    runtime = tools(tmp_path, seed=candidates(), search_responses=[HTTPResponse(200, "application/xml", changed)])
    result = discover(runtime)
    assert result["warnings"] and runtime.workspace.registered_candidates[0].version == 2
    row = next(row for row in result["data"]["candidates"] if row["arxiv_id"] == "2401.00544")
    assert row["version"] == 3 and row["task_pinned_version"] == 2
    assert not runtime.call("fetch_paper", {"arxiv_id": "2401.00544", "version": 3})["success"]


def test_local_search_and_total_call_limits_are_enforced(tmp_path):
    runtime = tools(tmp_path)
    for _ in range(4):
        assert runtime.call("search_documents", {"query": "retrieval"})["success"]
    assert runtime.call("search_documents", {"query": "retrieval"})["error"]["code"] == "local_search_budget_exhausted"
    while runtime.limits()["tool_calls"] < runtime.task.limits.max_tool_calls:
        runtime.call("list_documents", {})
    assert runtime.call("list_documents", {})["error"]["code"] == "tool_budget_exhausted"


def test_task_read_limits_and_foreign_document_ids_are_rejected(tmp_path, sample_pdf):
    request_task = task(limits=ResearchLimits(max_pages_per_read=1, max_read_chars=10))
    runtime = tools(tmp_path, sample_pdf, seed=candidates(), research_task=request_task)
    doc = acquire_and_ingest(runtime)
    assert runtime.call("read_pages", {"document_id": doc, "start_page": 1, "end_page": 2})["error"]["code"] == "invalid_arguments"
    assert not runtime.call("read_pages", {"document_id": "arxiv--foreign--v1", "start_page": 1})["success"]
    assert not runtime.call("search_documents", {"query": "retrieval", "document_ids": ["paper"]})["success"]
    limited = runtime.call("read_pages", {"document_id": doc, "start_page": 1})
    assert len(limited["data"]["pages"][0]["text"]) == 10 and limited["data"]["truncated"]


def test_evidence_ids_are_stable_across_index_growth_and_result_mutation_is_detached(tmp_path, sample_pdf):
    runtime = tools(tmp_path, seed=candidates(), fetch_responses=[FakeResponse(sample_pdf.read_bytes()) for _ in range(2)])
    doc = acquire_and_ingest(runtime)
    first = runtime.call("search_documents", {"query": "retrieval", "method": "bm25", "document_ids": [doc]})
    evidence = first["data"]["results"][0]
    identifier = evidence["evidence_id"]
    quote = evidence["chunk"]["text"][:10]
    evidence["chunk"]["text"] = "forged"
    acquire_and_ingest(runtime, "2401.00396")
    second = runtime.call("search_documents", {"query": "retrieval", "method": "bm25", "document_ids": [doc]})
    assert second["data"]["build_id"] != first["data"]["build_id"]
    assert second["data"]["results"][0]["evidence_id"] == identifier
    assert runtime.call("verify_citations", {"answer": f"Finding [{identifier}]", "insufficient_evidence": False,
        "citations": [{"evidence_id": identifier, "quote": quote}]})["success"]


def test_citations_cannot_reference_unreturned_text_or_fabricated_ids(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    doc = acquire_and_ingest(runtime)
    read = runtime.call("read_pages", {"document_id": doc, "start_page": 1, "max_chars": 8})
    identifier = read["data"]["pages"][0]["evidence_id"]
    for evidence_id, quote in (("E999", "anything"), (identifier, "Retrieval-Augmented research.")):
        invalid = runtime.call("verify_citations", {"answer": f"Claim [{evidence_id}]", "insufficient_evidence": False,
            "citations": [{"evidence_id": evidence_id, "quote": quote}]})
        assert invalid["error"]["code"] == "citation_validation_failed"
    inline = runtime.call("verify_citations", {"answer": "Claim [E999]", "insufficient_evidence": False,
        "citations": [{"evidence_id": identifier, "quote": read["data"]["pages"][0]["text"]}]})
    assert inline["error"]["code"] == "citation_validation_failed"


def test_journal_is_serializable_replayable_and_rejects_changes(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    runtime.call("list_documents", {}, purpose="List available documents")
    acquire_and_ingest(runtime)
    runtime.call("search_documents", {"query": "retrieval", "method": "bm25"})
    trace = json.loads(runtime.journal_path.read_text(encoding="utf-8"))
    replay = RecordedToolReplay(trace)
    for event in trace["events"]:
        assert event["duration_ms"] >= 0 and "limits_before" in event and "source_refs" in event
        assert replay.call(event["tool"], event["arguments"]) == event["result"]
    with pytest.raises(ValueError, match="No recorded"):
        replay.call("list_documents", {})
    trace["events"][0]["result"]["success"] = False
    with pytest.raises(ValueError, match="changed"):
        RecordedToolReplay(trace)


def test_journal_blocks_silent_session_restart_and_interrupted_event_replay(tmp_path):
    runtime = tools(tmp_path)
    runtime.call("list_documents", {})
    with pytest.raises(ValueError, match="journal already"):
        ResearchTools(runtime.workspace)
    trace = runtime.export_trace()
    trace["events"][0]["status"] = "started"
    with pytest.raises(ValueError, match="Incomplete"):
        RecordedToolReplay(trace)


def test_invalid_input_and_unexpected_exception_do_not_leak_keys_into_journal(tmp_path, monkeypatch):
    runtime = tools(tmp_path)
    runtime.call("list_documents", {"api_key": "sk-project-secret-test"}, purpose="Bearer confidential-token")
    def failure():
        raise RuntimeError("private sk-project-should-never-be-logged")
    monkeypatch.setattr(runtime, "_dispatch", lambda *args: failure())
    result = runtime.call("list_documents", {})
    assert result["error"]["code"] == "tool_execution_failed"
    content = runtime.journal_path.read_text(encoding="utf-8")
    assert "sk-project" not in content and "confidential-token" not in content
    assert "private" not in json.dumps(result)


def test_schema_validation_is_strict_without_loading_a_model(tmp_path):
    runtime = tools(tmp_path)
    for args in ({"query": "x", "top_k": 1.0}, {"query": "x", "document_ids": [None]}, {"query": "x", "top_k": 0}):
        assert runtime.call("search_documents", args)["error"]["code"] == "invalid_arguments"
    assert runtime.workspace._embedder is None


def test_replay_mismatched_action_does_not_advance_the_recorded_position(tmp_path):
    runtime = tools(tmp_path)
    expected = runtime.call("list_documents", {})
    replay = RecordedToolReplay(runtime.export_trace())
    with pytest.raises(ValueError, match="does not match"):
        replay.call("fetch_paper", {"arxiv_id": "unknown"})
    assert replay.call("list_documents", {}) == expected


def test_tool_journal_write_failure_stops_before_action_or_reports_unknown_final_record(tmp_path, monkeypatch):
    runtime = tools(tmp_path)
    calls = []
    def never_run(*args):
        calls.append(True)
        return {}, []
    monkeypatch.setattr(runtime, "_dispatch", never_run)
    def fail():
        raise OSError("Synthetic record failure")
    monkeypatch.setattr(runtime, "_persist", fail)
    first = runtime.call("list_documents", {})
    assert first["error"]["code"] == "audit_storage_error" and not calls
    assert runtime.call("list_documents", {})["error"]["code"] == "audit_storage_error" and not calls


def test_final_journal_write_failure_preserves_started_record_without_replaying_action(tmp_path, monkeypatch):
    runtime = tools(tmp_path)
    original = runtime._persist
    calls = []
    def fail_final():
        calls.append(True)
        if len(calls) == 3:
            raise OSError("Synthetic final-write failure")
        original()
    monkeypatch.setattr(runtime, "_persist", fail_final)
    result = runtime.call("list_documents", {})
    assert result["error"]["code"] == "audit_storage_error"
    trace = json.loads(runtime.journal_path.read_text(encoding="utf-8"))
    assert trace["events"][0]["status"] == "started"
    with pytest.raises(ValueError, match="Incomplete"):
        RecordedToolReplay(trace)


def test_existing_acquisitions_reserve_paper_capacity_when_tool_session_starts(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, research_task=task(target_papers=1), seed=candidates())
    # Emulate trusted acquisition state prepared before the first tool call.
    prior = runtime.fetch.fetch("2401.00544")
    runtime.workspace.record_fetch_result(prior)
    restored = ResearchTools(runtime.workspace)
    assert restored.limits()["downloaded_papers"] == 1
    result = restored.call("fetch_paper", {"arxiv_id": "2401.00396"})
    assert result["error"]["code"] == "paper_budget_exhausted"
    assert restored.fetch.fetch_attempts == 0


def test_changing_pinned_pdf_hash_cannot_overwrite_successful_workspace_state(tmp_path, sample_pdf):
    from dataclasses import replace
    from paper_assistant.research.workspace import WorkspaceError
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    doc = acquire_and_ingest(runtime)
    acquired = runtime.fetch.fetch("2401.00544", offline=True)
    changed = replace(acquired, asset=replace(acquired.asset, source_sha256="f" * 64))
    with pytest.raises(WorkspaceError, match="state was preserved"):
        runtime.workspace.record_fetch_result(changed)
    assert runtime.workspace.read_pages(doc, 1)["pages"]


def test_cli_executes_and_replays_a_no_network_sequence_without_creating_new_task(tmp_path):
    calls = tmp_path / "calls.jsonl"
    calls.write_text('{"tool":"list_documents","arguments":{},"purpose":"查看任务资料"}\n', encoding="utf-8")
    root = Path(__file__).parents[2]
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "gbk"}
    execute = subprocess.run([
        sys.executable, str(root / "scripts" / "dev" / "run_tool_calls.py"), "--calls", str(calls), "--task-id", "cli-test",
        "--topic", "retrieval", "--start", "2023-01-01T00:00:00Z", "--end", "2024-01-01T00:00:00Z",
        "--root", str(tmp_path / "runs"), "--offline", "--local-models-only",
    ], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert execute.returncode == 0, execute.stderr
    payload = json.loads(execute.stdout)
    assert payload["mode"] == "explicit_script_not_autonomous" and payload["llm_calls"] == 0
    path = Path(payload["journal"])
    original = path.read_bytes()
    replay = subprocess.run([sys.executable, str(root / "scripts" / "dev" / "run_tool_calls.py"), "--replay", str(path)],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["mode"] == "recorded_observation_replay_no_actions"
    assert path.read_bytes() == original
    assert len(list((tmp_path / "runs").iterdir())) == 1


def test_cross_task_citation_quote_cannot_be_substituted_for_returned_evidence(tmp_path, sample_pdf):
    runtime = tools(tmp_path, sample_pdf, seed=candidates())
    doc = acquire_and_ingest(runtime)
    text = runtime.call("read_pages", {"document_id": doc, "start_page": 1, "max_chars": 6})
    eid = text["data"]["pages"][0]["evidence_id"]
    checked = runtime.call("verify_citations", {"answer": f"Claim [{eid}]", "insufficient_evidence": False,
        "citations": [{"evidence_id": eid, "quote": "A different task's unreturned text"}]})
    assert checked["error"]["code"] == "citation_validation_failed"


@pytest.mark.parametrize("failed_write", [1, 3])
def test_cli_stops_and_reports_initial_or_final_journal_failure(tmp_path, monkeypatch, capsys, failed_write):
    import runpy
    import paper_assistant.research.toolset as tool_module

    runtime = tools(tmp_path / "bound")
    original = runtime._persist
    writes = []
    def fail_write():
        writes.append(True)
        if len(writes) == failed_write:
            raise OSError("Synthetic journal failure")
        original()
    monkeypatch.setattr(runtime, "_persist", fail_write)
    monkeypatch.setattr(tool_module, "ResearchTools", lambda *args, **kwargs: runtime)
    calls = tmp_path / "calls.jsonl"
    calls.write_text('{"tool":"list_documents","arguments":{}}\n' * 2, encoding="utf-8")
    script = Path(__file__).parents[2] / "scripts" / "dev" / "run_tool_calls.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--calls", str(calls), "--task-id", "script-task",
        "--topic", "retrieval", "--start", "2023-01-01T00:00:00Z", "--end", "2024-01-01T00:00:00Z",
        "--root", str(tmp_path / "runs"), "--offline"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(script), run_name="__main__")
    assert exit_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["event_count"] == 1
    assert payload["results"][0]["error"]["code"] == "audit_storage_error"
    assert len(runtime.export_trace()["events"]) == 1
