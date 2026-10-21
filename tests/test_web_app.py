import io
import json
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import httpx2
import pytest
from openai import AuthenticationError

from paper_assistant import interactive
from paper_assistant.embedding import build_dense_index, save_dense_index
from paper_assistant.web_server import PaperRequestHandler
from test_answer_generation import FakeClient
from test_embedding import FakeEmbedder
from test_evaluation import result


@pytest.fixture
def local_session(tmp_path, monkeypatch):
    save_dense_index(build_dense_index([result(1).chunk], FakeEmbedder()), tmp_path)
    client = FakeClient()
    monkeypatch.setattr(interactive, "create_openai_client", lambda: nullcontext(client))
    monkeypatch.setattr(interactive, "SentenceTransformerEmbedder", lambda *a, **kw: FakeEmbedder())
    return interactive.LocalPaperSession(tmp_path), client


@pytest.mark.parametrize("method", ["dense", "bm25", "hybrid"])
def test_page_service_reuses_rag_and_does_not_generate_on_load(local_session, method):
    session, client = local_session
    assert not client.create_calls
    assert session._embedder is None
    payload = session.ask("retrieval", method=method, token_count_mode="api")
    assert payload["result"]["status"] == "answered"
    assert payload["result"]["citations"][0]["sources"][0]["page_number"] == 1
    assert payload["retrieval_method"] == method
    assert len(client.create_calls) == 1
    assert (session._embedder is None) == (method == "bm25")
    # Past questions/answers are not forwarded on the next independent question.
    session.ask("retrieval second question", method=method, token_count_mode="api")
    assert len(client.create_calls) == 2
    assert client.create_calls[-1]["input"] != client.create_calls[0]["input"]


@pytest.mark.parametrize("question,options", [("", {}), (None, {}), ("q"*4001, {}),
    ("q", {"top_k": True}), ("q", {"top_k": 11}), ("q", {"method": "other"}),
    ("q", {"token_count_mode": "unknown"})])
def test_invalid_questions_never_call_generation(local_session, question, options):
    session, client = local_session
    with pytest.raises(ValueError):
        session.ask(question, **options)
    assert not client.create_calls and not client.count_calls


class FakeSocket:
    """Exercise the real HTTP parser/handler without network or paid requests."""
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args):
        return self.request

    def sendall(self, data):
        self.output.extend(data)


def send_request(server, path="/api/ask", payload=None, *, method="POST", origin=None,
                 host="127.0.0.1:8501", content_type="application/json", content_length=None):
    body = json.dumps(payload or {"question": "retrieval"}).encode()
    headers = [f"{method} {path} HTTP/1.0", f"Host: {host}", f"Content-Type: {content_type}",
               f"Content-Length: {len(body) if content_length is None else content_length}"]
    if origin:
        headers.append(f"Origin: {origin}")
    socket = FakeSocket(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
    PaperRequestHandler(socket, ("127.0.0.1", 12345), server)
    header, content = bytes(socket.output).split(b"\r\n\r\n", 1)
    status = int(header.split()[1])
    return status, header, content


@pytest.fixture
def server(local_session):
    session, _ = local_session
    return SimpleNamespace(resource=lambda: session, server_address=("127.0.0.1", 8501),
                           question_lock=threading.Lock())


def test_actual_http_route_returns_answer_sources_and_download_payload(server, local_session):
    status, headers, content = send_request(server, payload={"question": "retrieval", "token_count_mode": "api"},
                                            origin="http://127.0.0.1:8501")
    assert status == 200
    assert b"application/json" in headers
    assert json.loads(content)["result"]["status"] == "answered"
    assert not server.question_lock.locked()
    assert len(local_session[1].create_calls) == 1


@pytest.mark.parametrize("options,expected", [({"origin": "https://external.invalid"}, 403),
    ({"host": "external.invalid:8501"}, 403), ({"content_type": "text/plain"}, 415),
    ({"content_length": 999999}, 400), ({"path": "/api/upload"}, 404),
    ({"payload": {"question": "q", "api_key": "should-not-be-accepted"}}, 400)])
def test_invalid_or_cross_origin_requests_never_generate(server, local_session, options, expected):
    assert send_request(server, **options)[0] == expected
    assert not local_session[1].create_calls


def test_duplicate_inflight_request_is_rejected_without_generation(server, local_session):
    server.question_lock.acquire()
    try:
        assert send_request(server)[0] == 409
        assert not local_session[1].create_calls
    finally:
        server.question_lock.release()


def test_status_and_static_assets_are_read_only_and_key_is_not_returned(server, local_session, monkeypatch):
    secret = "sk-local-test-secret-never-returned"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    for path in ("/", "/app.js", "/style.css", "/api/status"):
        status, _, body = send_request(server, path, method="GET")
        assert status == 200 and secret.encode() not in body
    metadata = json.loads(send_request(server, "/api/status", method="GET")[2])
    assert metadata["key_ready"] is True and metadata["ready"] is True
    assert not local_session[1].create_calls
    assert send_request(server, "/../pyproject.toml", method="GET")[0] == 404


def test_api_errors_are_redacted_and_busy_lock_is_released(server, monkeypatch):
    secret = "sk-secret-in-error"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    error = AuthenticationError("raw exception must not be returned",
        response=httpx2.Response(401, request=request),
        body={"error": {"message": f"Incorrect key {secret}", "code": "invalid_api_key"}})
    def fail(*args, **kwargs):
        raise error
    server.resource().ask = fail
    status, _, body = send_request(server)
    assert status == 502
    assert secret.encode() not in body and b"[REDACTED]" in body
    assert b"raw exception" not in body
    assert not server.question_lock.locked()
