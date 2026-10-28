import json

from paper_assistant.agent.messages import AgentSession, completed_exchanges
from paper_assistant.research.model import convert_to_llm
from paper_assistant.research.state import AgentState
from tests.research.test_agent_model import FakeClient, controller, envelope, response
from tests.research.test_agent_runner import resumed
from tests.research.test_agent_tools import tools


def list_documents():
    return envelope({"kind": "tool", "tool": "list_documents", "arguments": {}})


def test_openai_requests_include_linked_tool_calls_and_results_and_persist_events(tmp_path):
    runner, client = controller(tmp_path, FakeClient([response(list_documents()), response()]))
    events = []
    runner.subscribe(lambda event: events.append(event["type"]))
    result = runner.run()
    assert len(client.calls[0]["input"]) == 1
    exchange = client.calls[1]["input"][1:]
    assert [row["type"] for row in exchange] == ["function_call", "function_call_output"]
    assert exchange[0]["call_id"] == exchange[1]["call_id"]
    assert json.loads(exchange[0]["arguments"])["action"]["tool"] == "list_documents"
    assert json.loads(exchange[1]["output"])["success"]
    assert [message["role"] for message in result["messages"]] == ["user", "assistant", "tool", "assistant", "tool"]
    assert len(completed_exchanges(result["messages"])) == 2  # Includes the controlled finish acknowledgement.
    assert events[0] == "agent_start" and events[-1] == "agent_end"
    assert AgentState.load(runner.path).to_dict() == result


def test_premature_finish_is_a_failed_tool_result_in_the_next_provider_request(tmp_path):
    premature = envelope({"kind": "finish", "reason": "sufficient_evidence", "summary": "I will verify next"})
    runner, client = controller(tmp_path, FakeClient([response(premature), response()]))
    result = runner.run()
    output = json.loads(client.calls[1]["input"][-1]["output"])
    assert not output["success"] and output["error"]["code"] == "unverified_completion"
    assert output["error"]["required_tool"] == "verify_citations"
    assert result["stop_reason"] == "insufficient_evidence" and result["usage"]["calls"] == 2


def test_resuming_uses_saved_transcript_without_repeating_a_completed_tool(tmp_path):
    runner, client = controller(tmp_path, FakeClient([response(list_documents()), response()]))
    first = runner.run(max_steps_per_run=1)
    exchange = completed_exchanges(first["messages"])[0]
    restored = resumed(runner)
    final = restored.run()
    assert final["tool_event_count"] == 1 and final["usage"]["calls"] == 2
    assert client.calls[1]["input"][1]["call_id"] == exchange[0]["id"]
    assert final["messages"][:3] == first["messages"]
    assert [row["sequence"] for row in final["agent_events"]] == list(range(1, len(final["agent_events"]) + 1))


def test_conversion_drops_large_exchanges_as_pairs_and_ignores_pending_call():
    from datetime import datetime, timezone
    session = AgentSession([], [], now=lambda: datetime.now(timezone.utc))
    session.assistant("oversize", "research_action", {"text": "a" * 4000})
    session.tool_result("oversize", {"success": True, "data": {}})
    session.assistant("pending", "research_action", {})
    items, omitted = convert_to_llm(session.messages)
    assert items == [] and omitted == 1


def test_successful_literature_feedback_reports_candidates_and_next_step(tmp_path):
    search = envelope({"kind": "tool", "tool": "search_literature", "arguments": {"query": "retrieval"}})
    runner, client = controller(tmp_path, FakeClient([response(search), response()]), runtime=tools(tmp_path))
    runner.run()
    request = client.calls[1]
    output = json.loads(request["input"][-1]["output"])
    summary = output["data_summary"]
    context = json.loads(request["input"][0]["content"])
    assert output["success"] and summary["candidate_count"] == 3
    assert set(summary["candidate_ids"]) == {row["arxiv_id"] for row in context["candidates"]}
    assert summary["candidate_details"] == "current_context.candidates"
    assert "fetch_paper" in summary["next_action"] and "evidence_ids" not in summary
    assert context["observations"][-1]["result_summary"]["candidate_count"] == 3


def test_empty_literature_feedback_explicitly_reports_no_candidates():
    from datetime import datetime, timezone
    session = AgentSession([], [], now=lambda: datetime.now(timezone.utc))
    session.assistant("search-empty", "research_action", envelope({
        "kind": "tool", "tool": "search_literature", "arguments": {"query": "no match"}}))
    session.tool_result("search-empty", {"success": True, "error": None, "data": {"candidates": []}})
    items, _ = convert_to_llm(session.messages)
    summary = json.loads(items[-1]["output"])["data_summary"]
    assert summary["candidate_count"] == 0 and summary["candidate_ids"] == []
    assert "Rewrite" in summary["next_action"] and "fetch_paper" not in summary["next_action"]


def test_duplicate_search_returns_linked_correction_instead_of_executing_again(tmp_path):
    search = envelope({"kind": "tool", "tool": "search_literature", "arguments": {"query": "retrieval", "max_results": 3}})
    runner, client = controller(tmp_path, FakeClient([response(search), response(search), response()]))
    result = runner.run()
    request = client.calls[2]
    exchange = request["input"][1:]
    assert exchange[0]["call_id"] == exchange[1]["call_id"]
    error = json.loads(exchange[1]["output"])["error"]
    assert error["code"] == "duplicate_literature_search" and error["candidate_count"] == 3
    assert "fetch" in error["message"]
    assert json.loads(request["input"][0]["content"])["last_error"]["code"] == error["code"]
    assert result["tool_event_count"] == result["tool_limits"]["search_rounds"] == 1


def test_real_sdk_serializes_complete_exchanges_with_original_call_ids(tmp_path):
    import httpx2 as httpx
    from openai import DefaultHttpxClient, OpenAI
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            assert payload["input"][1]["call_id"] == payload["input"][2]["call_id"] == "call_1"
        return httpx.Response(200, json={"id": f"resp_{len(requests)}", "object": "response", "created_at": 0,
            "status": "completed", "model": "gpt-5.4-mini-2026-03-17", "output": [{"type": "function_call",
            "id": f"fc_{len(requests)}", "call_id": f"call_{len(requests)}", "name": "research_action", "status": "completed",
            "arguments": json.dumps(list_documents() if len(requests) == 1 else envelope())}],
            "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}})
    sdk = OpenAI(api_key="sk-injected-no-network", max_retries=0,
                 http_client=DefaultHttpxClient(transport=httpx.MockTransport(handle), trust_env=False))
    try:
        runner, _ = controller(tmp_path, sdk)
        result = runner.run()
        assert result["stop_reason"] == "insufficient_evidence" and len(requests) == 2
        assert [call["id"] for call, _ in completed_exchanges(result["messages"])] == ["call_1", "call_2"]
    finally:
        sdk.close()
