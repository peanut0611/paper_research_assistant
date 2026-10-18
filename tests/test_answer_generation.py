import json
import runpy
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_assistant.answer_generation import (
    ANSWER_SCHEMA, GENERATION_INSTRUCTIONS, MODEL_NAME, OpenAIAnswerGenerator,
    TokenUsage, validate_answer,
)
from paper_assistant.embedding import build_dense_index, save_dense_index
from test_context_builder import build
from test_embedding import FakeEmbedder
from test_evaluation import result


def valid_output():
    return {"answer": "The evidence discusses retrieval. [E1]", "insufficient_evidence": False,
            "citations": [{"evidence_id": "E1", "quote": "retrieval"}]}


def response(data=None, *, status="completed", refusal=None):
    content = SimpleNamespace(type="refusal", refusal=refusal) if refusal else SimpleNamespace(type="output_text")
    return SimpleNamespace(
        output_text=json.dumps(valid_output() if data is None else data),
        model="gpt-5.4-mini-2026-03-17", id="resp_test", status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens") if status != "completed" else None,
        output=[SimpleNamespace(type="message", content=[content])],
        usage=SimpleNamespace(input_tokens=100, input_tokens_details=SimpleNamespace(cached_tokens=40),
                              output_tokens=30, output_tokens_details=SimpleNamespace(reasoning_tokens=0)),
    )


class FakeClient:
    def __init__(self, reply=None, count=100):
        self.count_calls = []
        self.create_calls = []
        self.reply = reply or response()
        self.count = count
        self.responses = SimpleNamespace(input_tokens=SimpleNamespace(count=self.count_tokens), create=self.create)

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=self.count)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.reply


def test_complete_pipeline_uses_exact_request_counter_schema_and_citations():
    client = FakeClient()
    adapter = OpenAIAnswerGenerator(client=client)
    run = adapter.answer_question("What is retrieval?", [result(1)], max_output_tokens=512)
    assert run.result.status == "answered"
    assert run.result.citations[0].sources[0].chunk_id == result(1).chunk.chunk_id
    assert run.context.prompt_token_count == 100
    assert len(client.create_calls) == 1
    # Base and populated prompts are counted once each; final rechecks are cached.
    assert len(client.count_calls) == 2
    counted = client.count_calls[-1]
    sent = client.create_calls[0]
    for field in counted:
        assert sent[field] == counted[field]
    assert counted["model"] == MODEL_NAME
    assert counted["instructions"] == GENERATION_INSTRUCTIONS
    assert counted["text"]["format"]["schema"] == ANSWER_SCHEMA
    assert sent["store"] is False
    assert sent["truncation"] == "disabled"
    assert sent["reasoning"] == {"effort": "none"}
    assert sent["service_tier"] == "default"
    assert sent["max_output_tokens"] == 512
    assert "tools" not in sent
    assert run.maximum_generation_cost_usd == pytest.approx((100*0.75 + 512*4.5)/1_000_000)
    assert run.result.usage.estimated_cost_usd == pytest.approx(0.000183)


def test_chinese_answer_quote_matching_ignores_pdf_whitespace():
    context = build([result(1, "能暂停时间五秒的时间停止手 表")])
    raw = json.dumps({"answer": "能暂停五秒。[E1]", "insufficient_evidence": False,
                      "citations": [{"evidence_id": "E1", "quote": "能暂停时间五秒的时间停止手表"}]})
    answer, insufficient, citations = validate_answer(raw, context)
    assert answer == "能暂停五秒。[E1]" and not insufficient
    assert citations[0].evidence_id == "E1"


@pytest.mark.parametrize("change", [
    {"answer": ""}, {"insufficient_evidence": "false"}, {"extra": 1},
    {"citations": [{"evidence_id": "E99", "quote": "retrieval"}]},
    {"citations": [{"evidence_id": "E1", "quote": "invented quote"}]},
    {"citations": [{"evidence_id": "E1", "quote": " "}]},
    {"citations": []}, {"answer": "No inline citation."},
    {"answer": "Wrong inline citation. [E2]"},
    {"citations": [{"evidence_id": "E1", "quote": "retrieval"}]*2},
])
def test_invalid_answers_are_not_presented_as_success(change):
    client = FakeClient(response({**valid_output(), **change}))
    run = OpenAIAnswerGenerator(client=client).answer_question("retrieval", [result(1)])
    assert run.result.status == "invalid_response"
    assert run.result.answer is None
    assert run.result.error
    assert run.result.usage.input_tokens == 100  # Preserve billed usage on failure.
    assert run.result.raw_output
    assert len(client.create_calls) == 1  # No automatic repair call.


def test_malformed_json_is_saved_as_invalid_response():
    reply = response()
    reply.output_text = "Not JSON"
    run = OpenAIAnswerGenerator(client=FakeClient(reply)).answer_question("retrieval", [result(1)])
    assert run.result.status == "invalid_response"
    assert run.result.raw_output == "Not JSON"


def test_insufficient_evidence_is_distinct_from_model_refusal():
    data = {"answer": "无法根据现有证据确定电池型号。", "insufficient_evidence": True, "citations": []}
    run = OpenAIAnswerGenerator(client=FakeClient(response(data))).answer_question("电池型号？", [result(1)])
    assert run.result.status == "insufficient_evidence"
    assert run.result.answer == data["answer"]
    refused = OpenAIAnswerGenerator(client=FakeClient(response(refusal="Cannot comply"))).answer_question("retrieval", [result(1)])
    assert refused.result.status == "model_refusal"
    assert refused.result.answer is None


def test_incomplete_output_is_not_parsed_as_a_complete_answer():
    run = OpenAIAnswerGenerator(client=FakeClient(response(status="incomplete"))).answer_question("retrieval", [result(1)])
    assert run.result.status == "incomplete"
    assert run.result.answer is None
    assert "max_output_tokens" in run.result.error
    assert run.result.usage.output_tokens == 30


def test_no_evidence_skips_generation():
    client = FakeClient()
    run = OpenAIAnswerGenerator(client=client).answer_question("unknown", [])
    assert run.result.status == "no_evidence"
    assert run.result.usage.estimated_cost_usd == 0
    assert not client.create_calls


def test_cost_guard_and_recount_reject_before_generation():
    client = FakeClient()
    adapter = OpenAIAnswerGenerator(client=client)
    with pytest.raises(ValueError, match="cost bound"):
        adapter.answer_question("retrieval", [result(1)], max_cost_usd=0.000001)
    assert not client.create_calls
    context = replace(build([result(1)]), prompt_token_count=0, max_input_tokens=99)
    with pytest.raises(ValueError, match="budget"):
        adapter.generate(context)
    assert not client.create_calls


@pytest.mark.parametrize("kwargs", [{"max_input_tokens": 400000}, {"max_output_tokens": 1},
                                     {"max_output_tokens": 128001}, {"max_cost_usd": float("nan")}])
def test_invalid_generation_limits(kwargs):
    client = FakeClient()
    with pytest.raises(ValueError):
        OpenAIAnswerGenerator(client=client).answer_question("retrieval", [result(1)], **kwargs)
    assert not client.create_calls
    assert not client.count_calls


def test_unavailable_token_counter_has_no_embedding_fallback():
    client = FakeClient()
    def unavailable(**kwargs):
        raise RuntimeError("Counter unavailable")
    client.responses.input_tokens.count = unavailable
    with pytest.raises(RuntimeError, match="Counter unavailable"):
        OpenAIAnswerGenerator(client=client).answer_question("retrieval", [result(1)])
    assert not client.create_calls


def test_missing_key_fails_before_sdk_construction(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIAnswerGenerator()


def test_sdk_config_uses_official_endpoint_no_retries(monkeypatch):
    import openai
    captured = {}
    def factory(**kwargs):
        captured.update(kwargs)
        return FakeClient()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(openai, "OpenAI", factory)
    OpenAIAnswerGenerator()
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 60


def test_official_sdk_serialization_with_local_mock_transport():
    import httpx2
    from openai import DefaultHttpxClient, OpenAI
    requests = []
    def handle(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path.endswith("/input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        assert request.url.path.endswith("/responses")
        return httpx2.Response(200, json={
            "id": "resp_sdk_test", "object": "response", "created_at": 1,
            "status": "completed", "model": MODEL_NAME,
            "output": [{"id": "msg_test", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": json.dumps(valid_output()), "annotations": []}]}],
            "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
        })
    with OpenAI(api_key="test-key-not-real", max_retries=0,
                http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handle))) as client:
        run = OpenAIAnswerGenerator(client=client).answer_question("retrieval", [result(1)])
    assert run.result.status == "answered"
    assert len(requests) == 3
    assert requests[-1][1]["text"]["format"]["strict"] is True


@pytest.mark.parametrize("mode", ["api", "local"])
def test_ask_cli_with_fake_generation_client(tmp_path, monkeypatch, capsys, mode):
    import paper_assistant.answer_generation as generation
    import paper_assistant.embedding as embedding
    import tiktoken
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda model: SimpleNamespace(encode=lambda *a, **kw: [1]*100))
    client = FakeClient()
    monkeypatch.setattr(generation, "OpenAIAnswerGenerator", lambda **kwargs: OpenAIAnswerGenerator(client=client, **kwargs))
    monkeypatch.setattr(embedding, "SentenceTransformerEmbedder", lambda *a, **kw: FakeEmbedder())
    index = tmp_path / "index"
    save_dense_index(build_dense_index([result(1).chunk], FakeEmbedder()), index)
    output = tmp_path / "answer.json"
    monkeypatch.setattr(sys, "argv", ["ask_papers.py", "retrieval", "--index", str(index), "--output", str(output), "--token-count-mode", mode])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/ask_papers.py"), run_name="__main__")
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["result"]["status"] == "answered"
    assert saved["settings"]["requested_model"] == MODEL_NAME
    assert saved["context"]["token_counter_name"].startswith("OpenAI Responses" if mode == "api" else "Local tiktoken")
    assert bool(client.count_calls) == (mode == "api")
    assert saved["retrieval_method"] == "hybrid"
    assert "[E1]" in capsys.readouterr().out


def test_estimated_cost_accounts_for_cached_input():
    assert TokenUsage(1000000, 1000000, 0, 0).estimated_cost_usd == 0.075
    assert TokenUsage(1000000, 0, 1000000, 0).estimated_cost_usd == 5.25


def test_ask_cli_masks_api_error_body(tmp_path, monkeypatch, capsys):
    import httpx2
    from openai import AuthenticationError
    import paper_assistant.answer_generation as generation
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    failure = AuthenticationError(
        "sensitive-server-body-test-marker", response=httpx2.Response(401, request=request, headers={"x-request-id": "req_test"}),
        body={"code": "invalid_api_key", "message": "Incorrect API key provided: sk-test-secret-value"},
    )
    def fail(**kwargs):
        raise failure
    monkeypatch.setattr(generation, "OpenAIAnswerGenerator", fail)
    monkeypatch.setattr(sys, "argv", ["ask_papers.py", "retrieval"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/ask_papers.py"), run_name="__main__")
    assert raised.value.code == 1
    stderr = capsys.readouterr().err
    assert "401" in stderr and "req_test" in stderr
    assert "sensitive-server-body-test-marker" not in stderr
    assert "sk-test-secret-value" not in stderr
    assert "[REDACTED]" in stderr


def test_api_diagnostics_explain_parameter_and_request_stage(monkeypatch):
    import httpx2
    from openai import BadRequestError
    from paper_assistant.answer_generation import describe_api_error
    monkeypatch.setenv("OPENAI_API_KEY", "unusual-test-key")
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses/input_tokens")
    error = BadRequestError("raw exception should not be printed", response=httpx2.Response(400, request=request),
                            body={"error": {"message": "Unsupported parameter: text. Key unusual-test-key", "param": "text", "code": None}})
    message = describe_api_error(error)
    assert "stage=token_count" in message
    assert "Unsupported parameter: text" in message
    assert "param: text" in message
    assert "unusual-test-key" not in message


def test_api_diagnostics_do_not_dump_html_or_auth_headers():
    import httpx2
    from openai import BadRequestError
    from paper_assistant.answer_generation import describe_api_error
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses", headers={"Authorization": "Bearer do-not-print"})
    error = BadRequestError("raw HTML", response=httpx2.Response(400, request=request, headers={"content-type": "text/html"}),
                            body="<html>sensitive proxy response</html>")
    message = describe_api_error(error)
    assert "content_type=text/html" in message
    assert "stage=answer_generation" in message
    assert "sensitive proxy response" not in message
    assert "do-not-print" not in message


def test_local_counter_bypasses_broken_endpoint_and_counts_whole_request(monkeypatch):
    import tiktoken
    seen = []
    class Encoding:
        def encode(self, text, **kwargs):
            seen.append(json.loads(text))
            assert kwargs == {"disallowed_special": ()}
            return list(range(100))
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda model: Encoding())
    client = FakeClient()
    def broken(**kwargs):
        pytest.fail("Local mode must never call the remote token counter")
    client.responses.input_tokens.count = broken
    run = OpenAIAnswerGenerator(client=client, token_count_mode="local").answer_question(
        "中文 <|endoftext|>", [result(1)])
    assert run.result.status == "answered"
    assert len(client.create_calls) == 1
    assert len(seen) == 2  # Base and populated prompt; generation reuses cache.
    assert seen[-1]["instructions"] == GENERATION_INSTRUCTIONS
    assert seen[-1]["text"]["format"]["schema"] == ANSWER_SCHEMA
    assert run.context.prompt_token_count == 238  # ceil(100 * 1.10) + 128
    assert run.to_dict()["settings"]["input_count_is_estimate"] is True


def test_invalid_token_count_mode_fails_before_client_creation():
    with pytest.raises(ValueError, match="token_count_mode"):
        OpenAIAnswerGenerator(token_count_mode="unknown")


@pytest.mark.parametrize("key", ["", "sk-test\nsecret", "中文密钥"])
def test_bad_local_key_rejected_before_any_network(monkeypatch, key):
    from paper_assistant.answer_generation import create_openai_client
    monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(ValueError):
        create_openai_client()
