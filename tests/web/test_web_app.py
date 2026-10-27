"""Agent-only HTTP routes; no default corpus or paid requests."""
import io
import json
import threading
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from paper_assistant.web.jobs import ResearchJobService
from paper_assistant.web.server import PaperHTTPServer, PaperRequestHandler

class FakeSocket:
    """Exercise the real HTTP parser/handler without network or paid requests."""
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args):
        return self.request

    def sendall(self, data):
        self.output.extend(data)


def send_request(server, path="/api/research/jobs", payload=None, *, method="POST", origin=None,
                 host="127.0.0.1:8501", content_type="application/json", content_length=None, fetch_site=None):
    body = json.dumps({"topic": "test"} if payload is None else payload).encode()
    headers = [f"{method} {path} HTTP/1.0", f"Host: {host}", f"Content-Type: {content_type}",
               f"Content-Length: {len(body) if content_length is None else content_length}"]
    if origin:
        headers.append(f"Origin: {origin}")
    if fetch_site:
        headers.append(f"Sec-Fetch-Site: {fetch_site}")
    socket = FakeSocket(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
    PaperRequestHandler(socket, ("127.0.0.1", 12345), server)
    header, content = bytes(socket.output).split(b"\r\n\r\n", 1)
    status = int(header.split()[1])
    return status, header, content

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-http-test-never-returned")
    service = ResearchJobService(tmp_path, threading.Lock(), execute=lambda *args: 1)
    yield SimpleNamespace(research=service, server_address=("127.0.0.1", 8501))
    service.close()


def test_agent_assets_and_readiness_do_not_require_old_papers_or_index(server):
    for route in ("/", "/research.js", "/style.css", "/api/research/status"):
        status, headers, body = send_request(server, route, method="GET")
        assert status == 200 and b"sk-http-test" not in body
        assert b"no-store" in headers
    html = send_request(server, "/", method="GET")[2].decode()
    assert 'id="research-form"' in html
    assert 'id="rag-panel"' not in html and '/app.js' not in html
    assert not server.research.root.exists()


@pytest.mark.parametrize("route,method", [("/api/status", "GET"), ("/api/ask", "POST"),
    ("/app.js", "GET"), ("/../pyproject.toml", "GET"), ("/paper/test.pdf", "GET"),
    ("/data/index/dense/manifest.json", "GET")])
def test_retired_qa_routes_and_local_files_are_not_served(server, route, method):
    assert send_request(server, route, method=method)[0] == 404
    assert not server.research.root.exists()


@pytest.mark.parametrize("options,expected", [({"origin": "https://external.invalid"}, 403),
    ({"host": "external.invalid:8501"}, 403), ({"fetch_site": "cross-site"}, 403),
    ({"content_type": "text/plain"}, 415), ({"content_length": 999999}, 400),
    ({"payload": []}, 400), ({"payload": {"api_key": "do-not-accept"}}, 400)])
def test_invalid_requests_do_not_create_research_tasks(server, options, expected):
    assert send_request(server, **options)[0] == expected
    assert not server.research.root.exists()


@pytest.mark.parametrize("error_type", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_client_disconnect_does_not_produce_a_second_response(server, error_type):
    class DisconnectSocket(FakeSocket):
        attempts = 0
        def sendall(self, data):
            self.attempts += 1
            raise error_type("Client disconnected")
    socket = DisconnectSocket(b"GET /api/research/status HTTP/1.0\r\nHost: 127.0.0.1:8501\r\n\r\n")
    PaperRequestHandler(socket, ("127.0.0.1", 12345), server)
    assert socket.attempts == 1


def test_real_server_starts_without_any_paper_or_default_index(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with PaperHTTPServer(0, project_root=tmp_path) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/research/status", timeout=5) as response:
                assert response.status == 200 and not json.load(response)["key_ready"]
            assert not (tmp_path / "paper").exists() and not (tmp_path / "data").exists()
        finally:
            server.shutdown()
            worker.join(timeout=5)
