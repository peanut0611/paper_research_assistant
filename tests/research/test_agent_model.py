import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_assistant.research.model import OpenAIDecisionPolicy, OpenAIModelConfig, RATES, _compact, _parse_action, action_function
from paper_assistant.research.agent import ResearchAgent, DecisionError
from paper_assistant.research.state import RunnerConfig, digest
from tests.research.test_agent_runner import Clock
from tests.research.test_agent_tools import tools
from tests.tools.test_paper_fetcher import candidates


def envelope(action=None):
    return {"action": action or {"kind": "finish", "reason": "insufficient_evidence", "summary": "缺少正文证据"},
            "purpose": "Check the next step", "updates": {"gaps": None, "selections": []}}


def response(data=None, *, status="completed", inputs=100, cached=40, outputs=30, refusal=False):
    call = SimpleNamespace(type="function_call", name="research_action", arguments=json.dumps(envelope() if data is None else data))
    return SimpleNamespace(model="gpt-5.4-mini-2026-03-17", id="resp_agent_test", status=status,
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])] if refusal else [call],
        usage=SimpleNamespace(input_tokens=inputs, input_tokens_details=SimpleNamespace(cached_tokens=cached),
            output_tokens=outputs, output_tokens_details=SimpleNamespace(reasoning_tokens=0)))


class FakeClient:
    def __init__(self, replies=None, choose=None):
        self.replies = list(replies or [])
        self.choose, self.calls, self.closed = choose, [], False
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.choose:
            return response(self.choose(json.loads(kwargs["input"][0]["content"])))
        reply = self.replies.pop(0) if self.replies else response()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


def controller(tmp_path, client=None, *, runtime=None, config=None, counter=None):
    runtime = runtime or tools(tmp_path)
    client = client or FakeClient()
    policy = OpenAIDecisionPolicy(client=client, config=config, token_counter=counter or (lambda request: 1000))
    clock = Clock()
    runtime.monotonic = clock.mono
    runner = ResearchAgent(runtime, policy, config=RunnerConfig(max_model_tokens=60000), now=clock.now, monotonic=clock.mono)
    return runner, client


def test_native_function_request_is_bounded_private_stateless_and_locally_estimated(tmp_path):
    runner, client = controller(tmp_path)
    estimate = runner.policy.estimate(runner.view())
    assert not client.calls
    assert estimate.max_tokens == 2200
    assert estimate.max_cost_usd == pytest.approx((1000 * .75 + 1200 * 4.5) / 1000000)
    result = runner.run()
    assert result["stop_reason"] == "insufficient_evidence"
    call = client.calls[0]
    assert call["store"] is False and call["parallel_tool_calls"] is False
    assert call["service_tier"] == "default" and call["reasoning"] == {"effort": "none"}
    assert call["tool_choice"] == {"type": "function", "name": "research_action"}
    assert "previous_response_id" not in call and "api_key" not in call
    assert result["usage"]["tokens"] == 130
    assert result["usage"]["cost_usd"] == pytest.approx((60*.75 + 40*.075 + 30*4.5)/1000000)
    assert result["model_events"][0]["metadata"]["response_id"] == "resp_agent_test"


def test_all_wire_objects_are_strict_required_and_optional_fields_are_nullable(tmp_path):
    runtime = tools(tmp_path)
    function = action_function(runtime.tool_specs())
    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(function)
    branch = function["parameters"]["properties"]["action"]["anyOf"][0]
    assert branch["properties"]["arguments"]["properties"]["max_results"]["anyOf"][-1] == {"type": "null"}


def test_optional_null_is_omitted_but_empty_scope_retains_its_meaning(tmp_path):
    runtime = tools(tmp_path)
    data = envelope({"kind": "tool", "tool": "search_documents", "arguments": {
        "query": "retrieval", "method": None, "top_k": None, "document_ids": []}})
    action = _parse_action(json.dumps(data), runtime.tool_specs())
    assert action["arguments"] == {"query": "retrieval", "document_ids": []}
    assert action["updates"] == {"selections": []}


@pytest.mark.parametrize("raw", [
    '{"action":{},"action":{},"purpose":"x","updates":{}}',
    '{"action":NaN,"purpose":"x","updates":{}}',
    json.dumps(envelope({"kind": "tool", "tool": "list_documents", "arguments": {"path": None}})),
    json.dumps(envelope({"kind": "tool", "tool": "search_literature", "arguments": {"query": None}})),
    json.dumps(envelope({"kind": "tool", "tool": "shell", "arguments": {}})),
])
def test_invalid_actions_are_rejected_locally(tmp_path, raw):
    with pytest.raises(Exception):
        _parse_action(raw, tools(tmp_path).tool_specs())


def test_frozen_request_after_estimation_ignores_transient_step_and_clock_fields(tmp_path):
    runner, client = controller(tmp_path)
    view = runner.view()
    runner.policy.estimate(view)
    view["steps"] += 1
    view["elapsed_seconds"] += 3
    view["pending"] = {"phase": "decision"}
    reply = runner.policy.decide(view)
    assert reply.usage.tokens == 130 and len(client.calls) == 1
    with pytest.raises(DecisionError, match="no API call"):
        runner.policy.decide(view)


def test_material_change_after_estimate_prevents_submission(tmp_path):
    runner, client = controller(tmp_path)
    view = runner.view()
    runner.policy.estimate(view)
    view["binding"]["task"]["topic"] = "changed"
    with pytest.raises(DecisionError, match="changed") as error:
        runner.policy.decide(view)
    assert error.value.usage.tokens == 0 and not client.calls


def test_projection_clips_abstracts_and_evidence_without_changing_stored_sources(tmp_path):
    runner, client = controller(tmp_path, runtime=tools(tmp_path, seed=candidates()))
    view = runner.view()
    view["candidates"][0]["abstract"] = "很长的摘要" * 5000
    view["evidence"] = {"E1": {"text": "original text " * 1000, "source": {"document_id": "doc"},
        "page_number": 1, "char_start": 0, "char_end": 14000}}
    original = digest(view)
    runner.policy.estimate(view)
    runner.policy.decide(view)
    prompt = json.loads(client.calls[0]["input"][0]["content"])
    assert prompt["candidates"][0]["abstract_truncated"] and len(prompt["candidates"][0]["abstract"]) == 1200
    assert prompt["evidence"][0]["text_truncated"] and len(prompt["evidence"][0]["text"]) == 1400
    assert digest(view) == original and "model_events" not in prompt


def test_context_limit_stops_before_http_without_unknown_usage_charge(tmp_path):
    runner, client = controller(tmp_path, counter=lambda request: 99999)
    result = runner.run()
    assert result["stop_reason"] == "model_context_too_large" and not client.calls
    assert result["usage"]["calls"] == 0 and result["last_error"]


@pytest.mark.parametrize("candidate_count", [5, 10])
@pytest.mark.parametrize("indexed", [False, True])
def test_real_counter_keeps_candidate_state_and_evidence_within_budget(tmp_path, candidate_count, indexed):
    runner, client = controller(tmp_path, runtime=tools(tmp_path, seed=candidates()))
    view = runner.view()
    template = view["candidates"][0]
    view["candidates"] = []
    for i in range(candidate_count):
        row = json.loads(json.dumps(template))
        row.update(arxiv_id=f"2401.{i + 10:05d}", document_id=f"arxiv--2401.{i + 10:05d}--v2",
                   title="A detailed study of retrieval augmented generation and hallucination detection " * 3,
                   abstract="Long candidate abstract about retrieval and hallucination detection. " * 35)
        row["status"] = "fulltext_ready" if indexed and i == 0 else "downloaded" if i == 0 else "abstract_only"
        view["candidates"].append(row)
        view["selections"][row["arxiv_id"]] = {"selected": i == 0, "reason": "相关性判断理由" * 70}
    if indexed:
        view["evidence"] = {f"E{i}": {"text": "原始正文证据。" * 300,
            "source": {"document_id": view["candidates"][0]["document_id"], "arxiv_id": "2401.00010", "version": 2},
            "page_number": i, "char_start": 0, "char_end": 2100} for i in range(1, 7)}
    original = digest(view)
    policy = OpenAIDecisionPolicy(client=client)  # Production byte-based estimator, not the fixture's constant counter.
    estimate = policy.estimate(view)
    policy.decide(view)
    request = client.calls[0]
    prompt = json.loads(request["input"][0]["content"])
    assert policy._count(request) <= policy.config.max_input_tokens
    assert estimate.max_tokens <= policy.config.max_input_tokens + policy.config.max_output_tokens
    assert len(prompt["candidates"]) == candidate_count
    assert prompt["task"] == view["binding"]["task"]
    assert prompt["tool_limits"] == view["tool_limits"]
    assert prompt["completion"] == view["completion"]
    assert prompt["candidates"][0]["status"] == view["candidates"][0]["status"]
    assert digest(view) == original
    if indexed:
        assert prompt["evidence"] and all(view["evidence"][row["evidence_id"]]["text"].startswith(row["text"]) for row in prompt["evidence"])
        assert prompt["omitted_evidence_count"] == 6 - len(prompt["evidence"])
    else:
        assert prompt["candidates"][0]["next_action"] == {"tool": "ingest_paper", "arguments": {"arxiv_id": "2401.00010"}}


@pytest.mark.parametrize("tool,rows", [
    ("search_documents", [{"rank": 3, "evidence_id": "E5"}, {"rank": 1, "evidence_id": "E1"}, {"rank": 2, "evidence_id": "E2"}]),
    ("read_pages", [{"page_number": 1, "evidence_id": "E1"}, {"page_number": 2, "evidence_id": "E2"}, {"page_number": 5, "evidence_id": "E5"}]),
])
def test_compaction_prioritizes_ranked_hits_and_requested_page_order(tmp_path, tool, rows):
    runner, _ = controller(tmp_path)
    view = runner.view()
    view["evidence"] = {f"E{i}": {"text": f"source {i}", "source": {"document_id": "doc"},
        "page_number": i, "char_start": 0, "char_end": 8, "rank": i} for i in (1, 2, 5)}
    view["observations"] = [{"tool": tool, "arguments": {}, "result": {"success": True, "error": None,
        "data": {"results" if tool == "search_documents" else "pages": rows}}}]
    before = digest(view)
    projected = _compact(view, evidence_count=1)
    assert projected["evidence"][0]["evidence_id"] == "E1"
    assert projected["omitted_evidence_count"] == 2 and digest(view) == before


def test_completion_correction_survives_smallest_context_projection(tmp_path):
    runner, _ = controller(tmp_path)
    view = runner.view()
    view["last_error"] = {"code": "unverified_completion", "required_tool": "verify_citations", "message": "Long explanation " * 100}
    projected = _compact(view, notes=30, history=1, minimal=True)
    assert projected["last_error"]["code"] == "unverified_completion"
    assert projected["last_error"]["required_tool"] == "verify_citations"
    assert projected["completion"]["required_before_sufficient_finish"] == "verify_citations"
    assert not projected["completion"]["can_finish_sufficient_evidence"]


def test_malformed_call_gets_one_bounded_correction_and_keeps_both_actual_usages(tmp_path):
    bad = response()
    bad.output[0].arguments = "not JSON"
    client = FakeClient([bad, response()])
    runner, _ = controller(tmp_path, client)
    result = runner.run()
    assert result["stop_reason"] == "insufficient_evidence" and result["usage"]["calls"] == 2
    assert result["usage"]["tokens"] == 260
    second = json.loads(client.calls[1]["input"][0]["content"])
    assert second["last_error"]["code"] == "invalid_decision"
    assert result["model_events"][0]["metadata"]["validation_error"]


@pytest.mark.parametrize("change", ["multiple", "wrong_name", "missing"])
def test_ambiguous_native_tool_output_never_executes_any_action(tmp_path, change):
    reply = response()
    if change == "multiple":
        reply.output *= 2
    elif change == "wrong_name":
        reply.output[0].name = "shell"
    else:
        reply.output = []
    runner, _ = controller(tmp_path, FakeClient([reply, reply]))
    result = runner.run()
    assert result["stop_reason"] == "invalid_decision_limit" and result["tool_event_count"] == 0
    assert result["usage"]["tokens"] == 260


@pytest.mark.parametrize("reply,reason", [(response(status="incomplete"), "openai_incomplete"),
                                        (response(refusal=True), "openai_refusal")])
def test_incomplete_or_refused_response_keeps_known_usage_and_does_not_retry(tmp_path, reply, reason):
    runner, client = controller(tmp_path, FakeClient([reply]))
    result = runner.run()
    assert result["stop_reason"] == reason and result["usage"]["tokens"] == 130
    assert result["usage"]["unknown_usage_calls"] == 0 and len(client.calls) == 1


@pytest.mark.parametrize("change", ["missing", "invalid", "wrong_model"])
def test_unknown_usage_or_pricing_stops_conservatively(tmp_path, change):
    reply = response()
    if change == "missing":
        reply.usage = None
    elif change == "invalid":
        reply.usage.input_tokens_details.cached_tokens = 999
    else:
        reply.model = "another-priced-model"
    runner, client = controller(tmp_path, FakeClient([reply]))
    result = runner.run()
    assert result["usage"]["unknown_usage_calls"] == 1 and result["usage"]["tokens"] == 2200
    assert result["tool_event_count"] == 0 and len(client.calls) == 1


def test_http_error_diagnostics_redact_key_and_never_retry(tmp_path, monkeypatch):
    import httpx2 as httpx
    from openai import BadRequestError
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-be-logged")
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = BadRequestError("bad", response=httpx.Response(400, request=request, headers={"x-request-id": "req_test"}),
        body={"error": {"message": "bad sk-test-should-not-be-logged", "code": "invalid_request_error"}})
    runner, client = controller(tmp_path, FakeClient([error]))
    result = runner.run()
    assert result["stop_reason"] == "openai_http_error" and len(client.calls) == 1
    assert "req_test" in result["last_error"]["message"]
    assert "sk-test-should-not-be-logged" not in runner.path.read_text(encoding="utf-8")


def test_api_timeout_is_bounded_by_remaining_runtime_and_not_retried(tmp_path):
    import httpx2 as httpx
    from openai import APITimeoutError
    error = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    runner, client = controller(tmp_path, FakeClient([error]))
    runner.policy.estimate(runner.view())
    view = runner.view()
    view["elapsed_seconds"] = 298
    with pytest.raises(DecisionError) as failure:
        runner.policy.decide(view)
    assert failure.value.code == "openai_timeout" and client.calls[0]["timeout"] == 2


@pytest.mark.parametrize("skip_ingestion_once", [False, True])
@pytest.mark.parametrize("premature_finish_once", [False, True])
def test_injected_model_branches_on_observations_and_checks_real_returned_quotes(tmp_path, sample_pdf, skip_ingestion_once, premature_finish_once):
    runtime = tools(tmp_path, sample_pdf)
    def choose(view):
        if not view["candidates"]:
            return envelope({"kind": "tool", "tool": "search_literature", "arguments": {"query": "retrieval augmented generation", "max_results": 3}})
        candidate = next(row for row in view["candidates"] if row["arxiv_id"] == "2401.00396")
        if candidate["status"] == "abstract_only":
            value = envelope({"kind": "tool", "tool": "fetch_paper", "arguments": {"arxiv_id": "2401.00396"}})
            value["updates"]["selections"] = [{"arxiv_id": "2401.00396", "selected": True, "reason": "Injected fixture title relevance"}]
            return value
        if candidate["status"] == "downloaded":
            if skip_ingestion_once and not any(row["tool"] == "search_documents" for row in view["observations"]):
                return envelope({"kind": "tool", "tool": "search_documents", "arguments": {
                    "query": "retrieval", "document_ids": [candidate["document_id"]]}})
            if skip_ingestion_once:
                previous = view["observations"][-1]
                assert previous["error"]["code"] == "document_not_indexed"
                assert previous["result_summary"]["required_action"] == candidate["next_action"]
            return envelope({"kind": "tool", "tool": "ingest_paper", "arguments": {"arxiv_id": "2401.00396"}})
        if not view["evidence"]:
            return envelope({"kind": "tool", "tool": "search_documents", "arguments": {"query": "retrieval", "method": "bm25"}})
        if not any(row["tool"] == "verify_citations" for row in view["observations"]):
            if premature_finish_once:
                if not view["last_error"]:
                    return envelope({"kind": "finish", "reason": "sufficient_evidence", "summary": "I will verify citations next"})
                assert view["last_error"]["required_tool"] == "verify_citations"
                assert not view["completion"]["can_finish_sufficient_evidence"]
            evidence = view["evidence"][0]
            return envelope({"kind": "tool", "tool": "verify_citations", "arguments": {
                "answer": f"Injected fixture excerpt [{evidence['evidence_id']}]", "insufficient_evidence": False,
                "citations": [{"evidence_id": evidence["evidence_id"], "quote": evidence["text"][:10]}]}})
        return envelope({"kind": "finish", "reason": "sufficient_evidence", "summary": "Injected transport validation only"})
    runner, client = controller(tmp_path, FakeClient(choose=choose), runtime=runtime)
    result = runner.run()
    assert result["stop_reason"] == "sufficient_evidence" and len(client.calls) == 6 + skip_ingestion_once + premature_finish_once
    assert result["usage"]["tokens"] == 130 * len(client.calls) and result["tool_event_count"] == 5 + skip_ingestion_once
    assert not any(row["simulated"] for row in result["model_events"])  # Adapter path; transport is explicitly injected.
    assert result["selections"]["2401.00396"]["selected"]


def test_missing_key_cli_exits_before_creating_task_and_no_paid_calls(tmp_path):
    root = Path(__file__).parents[2]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    # An explicitly empty environment value also blocks a developer's real .env.
    env["OPENAI_API_KEY"] = ""
    result = subprocess.run([sys.executable, str(root / "scripts" / "run_research.py"), "--task-id", "missing-key",
        "--topic", "retrieval", "--start", "2023-01-01T00:00:00Z", "--end", "2024-01-01T00:00:00Z",
        "--root", str(tmp_path / "runs"), "--offline-sources"], cwd=root, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and "OPENAI_API_KEY is not configured" in result.stderr
    assert not (tmp_path / "runs").exists()


def test_owned_client_uses_official_endpoint_no_retries_and_is_closed(tmp_path, monkeypatch):
    import paper_assistant.research.model as module
    import openai
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-no-network")
    policy = module.OpenAIDecisionPolicy(config=OpenAIModelConfig(trust_env=False))
    assert str(policy.client.base_url) == "https://api.openai.com/v1/"
    assert policy.client.max_retries == 0
    policy.close()
    assert policy.client.is_closed()


@pytest.mark.parametrize("kwargs", [{"model": "other"}, {"max_output_tokens": True}, {"max_input_tokens": 1},
                                      {"timeout_seconds": float("nan")}, {"trust_env": 1}])
def test_model_configuration_rejects_unpriced_models_or_unsafe_limits(kwargs):
    with pytest.raises(ValueError):
        OpenAIModelConfig(**kwargs)


def test_actual_sdk_serializes_function_tools_and_parses_response_usage_without_network(tmp_path):
    import httpx2 as httpx
    from openai import DefaultHttpxClient, OpenAI
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/v1/responses"
        return httpx.Response(200, json={"id": "resp_sdk_fixture", "object": "response", "created_at": 0,
            "status": "completed", "model": "gpt-5.4-mini-2026-03-17", "output": [{"type": "function_call",
            "id": "fc_fixture", "call_id": "call_fixture", "name": "research_action", "status": "completed",
            "arguments": json.dumps(envelope())}], "usage": {"input_tokens": 100, "output_tokens": 30,
            "total_tokens": 130, "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0}}})
    sdk = OpenAI(api_key="sk-injected-sdk-fixture", base_url="https://api.openai.com/v1", max_retries=0,
        http_client=DefaultHttpxClient(transport=httpx.MockTransport(handle), trust_env=False))
    try:
        runner, _ = controller(tmp_path, sdk)
        result = runner.run()
        assert len(requests) == 1 and requests[0]["tools"][0]["strict"]
        assert "timeout" not in requests[0]
        assert result["model_events"][0]["metadata"]["response_id"] == "resp_sdk_fixture"
        assert result["usage"]["tokens"] == 130
    finally:
        sdk.close()


def test_default_counter_counts_complete_request_without_api_or_tokenizer_download(tmp_path, monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("Token reservation must be local")
    monkeypatch.setattr(socket, "create_connection", no_network)
    runner, client = controller(tmp_path)
    policy = OpenAIDecisionPolicy(client=client)
    estimate = policy.estimate(runner.view())
    assert 1000 < estimate.max_tokens <= policy.config.max_input_tokens + policy.config.max_output_tokens
    assert not client.calls


def test_cli_saves_real_adapter_state_and_resumes_stopped_without_additional_request(tmp_path, monkeypatch, capsys):
    import paper_assistant.research.model as module
    clients = []
    def factory(**kwargs):
        client = FakeClient()
        clients.append(client)
        return client
    monkeypatch.setattr(module, "create_openai_client", factory)
    script = Path(__file__).parents[2] / "scripts" / "run_research.py"
    base = [str(script), "--task-id", "injected-cli", "--root", str(tmp_path / "runs"), "--offline-sources"]
    monkeypatch.setattr(sys, "argv", base + ["--topic", "retrieval", "--start", "2023-01-01T00:00:00Z", "--end", "2024-01-01T00:00:00Z"])
    runpy.run_path(str(script), run_name="__main__")
    first = json.loads(capsys.readouterr().out)
    assert first["stop_reason"] == "insufficient_evidence" and first["openai_requests_attempted"] == 1
    assert first["report_status"] == "formal_research_report_not_implemented" and clients[0].closed
    state_path = Path(first["state"])
    original = state_path.read_bytes()
    monkeypatch.setattr(sys, "argv", base + ["--resume"])
    runpy.run_path(str(script), run_name="__main__")
    second = json.loads(capsys.readouterr().out)
    assert second["usage"] == first["usage"] and not clients[1].calls and clients[1].closed
    assert state_path.read_bytes() == original


def test_api_usage_above_reservation_is_still_recorded_and_no_action_runs(tmp_path):
    runner, _ = controller(tmp_path, FakeClient([response(inputs=99999)]))
    result = runner.run()
    assert result["stop_reason"] == "model_usage_exceeded_reservation"
    assert result["usage"]["tokens"] == 100029 and result["tool_event_count"] == 0


def test_large_old_answers_and_queries_are_summarized_in_model_view(tmp_path):
    runner, client = controller(tmp_path)
    view = runner.view()
    view["observations"] = [{"tool": "verify_citations", "arguments": {"answer": "x" * 12000,
        "citations": [{"evidence_id": "E1", "quote": "y" * 4000}], "insufficient_evidence": False},
        "result": {"success": True, "error": None, "data": {"answer": "x" * 12000, "valid": True, "insufficient_evidence": False}}}]
    view["queries"] = [{"tool": "search_documents", "query": "z" * 4000, "success": True}]
    runner.policy.estimate(view)
    runner.policy.decide(view)
    prompt = json.loads(client.calls[0]["input"][0]["content"])
    observation = prompt["observations"][0]
    assert observation["arguments"]["cited_evidence_ids"] == ["E1"]
    assert "quote" not in observation["arguments"] and "answer" not in observation["arguments"]
    assert observation["result_summary"]["answer_truncated"]
    assert len(prompt["queries"][0]["query"]) == 300 and prompt["queries"][0]["query_truncated"]
    assert len(view["observations"][0]["arguments"]["answer"]) == 12000
