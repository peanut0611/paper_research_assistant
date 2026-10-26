import hashlib
import io
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from paper_assistant.literature.search import LiteratureSearchError, SearchRequest, parse_arxiv_feed
from paper_assistant.literature.fetcher import (
    PaperFetchError, PaperFetcher, PaperFetchSession, UrllibPDFTransport, load_search_candidates,
)
from paper_assistant.agent.task import ResearchLimits, ResearchTask


NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def candidates():
    root = Path(__file__).parent / "fixtures" / "arxiv"
    metadata = json.loads((root / "live_20261023.meta.json").read_text(encoding="utf-8"))
    raw = metadata["request"]
    request = SearchRequest(raw["query"], datetime.fromisoformat(raw["start_at"]),
                            datetime.fromisoformat(raw["end_at"]), raw["max_results"])
    return parse_arxiv_feed((root / "live_20261023.xml").read_bytes(), request, NOW)[0]


def task(**kwargs):
    return ResearchTask("retrieval augmented generation", ("Compare methods",),
                        datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, tzinfo=timezone.utc), **kwargs)


class FakeResponse:
    def __init__(self, body=b"", *, status=200, content_type="application/pdf", content_length="auto",
                 location=None, content_encoding=None, fail_after=None, on_read=None):
        self.body = io.BytesIO(body)
        self.status, self.content_type = status, content_type
        self.content_length = str(len(body)) if content_length == "auto" else content_length
        self.location, self.content_encoding = location, content_encoding
        self.fail_after, self.on_read = fail_after, on_read
        self.reads = []
        self.closed = False

    def read(self, size, *, timeout):
        self.reads.append((size, timeout))
        if self.on_read:
            self.on_read()
        if self.fail_after is not None and len(self.reads) > self.fail_after:
            raise PaperFetchError("connection_error", "Synthetic interrupted stream")
        return self.body.read(min(size, 80) if self.fail_after is not None else size)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @contextmanager
    def open(self, url, *, timeout):
        self.calls.append((url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        try:
            yield response
        finally:
            response.closed = True
            response.body.close()


class NoWaitGate:
    def __init__(self):
        self.calls = 0
    @contextmanager
    def slot(self, deadline):
        self.calls += 1
        yield


def session(tmp_path, responses, *, research_task=None, rows=None, **kwargs):
    fetcher = PaperFetcher(cache_directory=tmp_path / "cache", transport=FakeTransport(responses),
                           gate=NoWaitGate(), now=lambda: NOW, **kwargs)
    return PaperFetchSession(research_task or task(), candidates() if rows is None else rows, fetcher)


def assert_no_valid_cache(tmp_path):
    cache = tmp_path / "cache"
    assert not list(cache.rglob("manifest.json"))
    assert not list(cache.rglob("*.part"))
    assert not list(cache.rglob("*.pdf"))


def test_download_uses_pinned_canonical_url_and_records_hash_size_and_origin(tmp_path, sample_pdf):
    body = sample_pdf.read_bytes()
    sess = session(tmp_path, [FakeResponse(body)])
    candidate = candidates()[1]
    result = sess.fetch(candidate.arxiv_id)
    assert result.status == "downloaded" and not result.from_cache
    assert result.asset.requested_url == f"https://arxiv.org/pdf/{candidate.arxiv_id}v{candidate.version}"
    assert result.asset.source_sha256 == hashlib.sha256(body).hexdigest()
    assert result.asset.byte_count == len(body) and Path(result.asset.pdf_path).read_bytes() == body
    assert result.asset.origin == "injected_transport" and result.asset.fetched_at == NOW
    assert result.fetch_attempts == result.network_requests == 1
    assert not list(tmp_path.rglob("*.part"))
    assert json.loads(json.dumps(result.to_dict()))["status"] == "downloaded"


def test_supplied_candidate_urls_are_not_used_as_download_destinations(tmp_path, sample_pdf):
    candidate = replace(candidates()[0], pdf_url="https://evil.example/private", original_pdf_url="http://127.0.0.1/secret")
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())], rows=[candidate])
    assert sess.fetch(candidate.arxiv_id).status == "downloaded"
    assert sess.fetcher.transport.calls[0][0] == f"https://arxiv.org/pdf/{candidate.arxiv_id}v{candidate.version}"


@pytest.mark.parametrize("identifier", ["missing", "../file", "https://arxiv.org/pdf/2401.00396v2", "2401.00396v2", "C:\\secret.pdf"])
def test_unknown_ids_urls_and_paths_cannot_fetch_or_consume_budget(tmp_path, identifier):
    sess = session(tmp_path, [])
    with pytest.raises(ValueError, match="Unknown candidate"):
        sess.fetch(identifier)
    assert sess.fetch_attempts == sess.fetcher.network_requests == 0
    assert not tmp_path.joinpath("cache").exists()


def test_unpinned_changed_or_out_of_window_candidates_are_rejected_atomically(tmp_path):
    row = candidates()[0]
    with pytest.raises(ValueError, match="pinned"):
        session(tmp_path, [], rows=[replace(row, version=None)])
    sess = session(tmp_path, [], rows=[row])
    with pytest.raises(ValueError, match="already pinned"):
        sess.register_candidates([replace(row, version=row.version + 1)])
    with pytest.raises(ValueError, match="outside"):
        sess.register_candidates([replace(candidates()[1], submitted_at=datetime(2020, 1, 1, tzinfo=timezone.utc))])
    with pytest.raises(ValueError, match="registered version"):
        sess.fetch(row.arxiv_id, version=row.version + 1)
    assert not sess.history and sess.fetch_attempts == 0


@pytest.mark.parametrize("location", [
    "https://evil.example/paper.pdf", "http://arxiv.org/pdf/2401.00396v2",
    "https://arxiv.org/pdf/2401.00396", "https://arxiv.org/pdf/2401.00396v3",
    "https://arxiv.org/pdf/2401.00544v2", "https://arxiv.org:8443/pdf/2401.00396v2",
    "https://arxiv.org@evil.example/pdf/2401.00396v2", "file:///tmp/a.pdf",
    "https://arxiv.org/pdf/2401.00396v2?redirect=evil", "https://arxiv.org/pdf/2401.00396v2#part",
])
def test_redirect_destinations_cannot_change_host_scheme_paper_or_version(tmp_path, location):
    sess = session(tmp_path, [FakeResponse(status=302, location=location)])
    result = sess.fetch("2401.00396")
    assert result.status == "download_failed" and result.error_code == "unsafe_redirect"
    assert len(sess.fetcher.transport.calls) == 1 and result.fetch_attempts == 1
    assert_no_valid_cache(tmp_path)


def test_allowed_relative_redirect_is_separately_gated_and_counted(tmp_path, sample_pdf):
    sess = session(tmp_path, [FakeResponse(status=302, location="/pdf/2401.00396v2.pdf"), FakeResponse(sample_pdf.read_bytes())])
    result = sess.fetch("2401.00396")
    assert result.status == "downloaded" and result.fetch_attempts == result.network_requests == 2
    assert len(result.asset.redirect_chain) == 2 and result.asset.final_url.endswith("v2.pdf")
    assert sess.fetcher.gate.calls == 2


def test_redirect_loop_and_missing_location_stop_without_extra_requests(tmp_path):
    sess = session(tmp_path, [FakeResponse(status=302, location="/pdf/2401.00396v2")])
    result = sess.fetch("2401.00396")
    assert result.error_code == "redirect_limit" and len(sess.fetcher.transport.calls) == 1
    sess = session(tmp_path, [FakeResponse(status=302)])
    assert sess.fetch("2401.00396").error_code == "unsafe_redirect"
    assert_no_valid_cache(tmp_path)


@pytest.mark.parametrize("status,expected,code", [
    (404, "unavailable", "unavailable"), (410, "unavailable", "unavailable"),
    (429, "download_failed", "rate_limited"), (503, "download_failed", "server_error"),
    (403, "download_failed", "http_error"), (206, "download_failed", "http_error"),
])
def test_http_failure_states_are_distinct_and_never_retry_automatically(tmp_path, status, expected, code):
    response = FakeResponse(b"private error page", status=status)
    sess = session(tmp_path, [response])
    result = sess.fetch("2401.00396")
    assert result.status == expected and result.error_code == code and result.http_status == status
    assert result.fetch_attempts == 1 and not response.reads and response.closed
    assert "private" not in result.error_message
    assert_no_valid_cache(tmp_path)


@pytest.mark.parametrize("kind", ["wrong-mime", "html", "partial", "wrong-length", "compressed", "oversize-header", "oversize-stream", "bad-length"])
def test_invalid_or_partial_download_does_not_publish_cache(tmp_path, sample_pdf, kind):
    body = sample_pdf.read_bytes()
    max_bytes = len(body) + 10
    options = {}
    if kind == "wrong-mime":
        options["content_type"] = "text/html"
    elif kind == "html":
        body = b"<html>not PDF</html>"
    elif kind == "partial":
        body = body[:100]
    elif kind == "wrong-length":
        options["content_length"] = str(len(body) + 1)
    elif kind == "compressed":
        options["content_encoding"] = "gzip"
    elif kind == "oversize-header":
        options["content_length"] = str(max_bytes + 1)
    elif kind == "oversize-stream":
        options["content_length"] = None
        body = body + b"x" * 20
    elif kind == "bad-length":
        options["content_length"] = "not-a-number"
    response = FakeResponse(body, **options)
    sess = session(tmp_path, [response], research_task=task(limits=ResearchLimits(max_pdf_bytes=max_bytes)))
    result = sess.fetch("2401.00396")
    assert result.status == "download_failed" and result.asset is None and result.fetch_attempts == 1
    assert_no_valid_cache(tmp_path)
    if kind in ("oversize-header", "wrong-mime", "compressed", "bad-length"):
        assert not response.reads


def test_interrupted_stream_cleans_temporary_file_and_allows_explicit_retry(tmp_path, sample_pdf):
    body = sample_pdf.read_bytes()
    sess = session(tmp_path, [FakeResponse(body, fail_after=1), FakeResponse(body)])
    first = sess.fetch("2401.00396")
    assert first.error_code == "connection_error" and first.fetch_attempts == 1
    assert_no_valid_cache(tmp_path)
    second = sess.fetch("2401.00396")
    assert second.status == "downloaded" and sess.fetch_attempts == 2


def test_deadline_applies_while_streaming_and_failure_consumes_attempt(tmp_path, sample_pdf):
    clock = [0.0]
    def advance():
        clock[0] += 2
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes(), on_read=advance)], monotonic=lambda: clock[0])
    result = sess.fetch("2401.00396", timeout=1)
    assert result.error_code == "timeout" and result.fetch_attempts == 1
    assert_no_valid_cache(tmp_path)


def test_cache_hit_checks_integrity_preserves_fetch_time_and_uses_no_request_budget(tmp_path, sample_pdf):
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())])
    first = sess.fetch("2401.00396")
    sess.fetcher.now = lambda: NOW + timedelta(days=2)
    cached = sess.fetch("2401.00396", offline=True)
    assert cached.status == "downloaded" and cached.from_cache
    assert cached.asset.fetched_at == first.asset.fetched_at
    assert cached.network_requests == cached.fetch_attempts == 0
    assert len(sess.downloaded_assets) == 1 and sess.fetcher.gate.calls == 1


@pytest.mark.parametrize("changed", ["pdf", "manifest", "partial-cache"])
def test_corrupt_cache_fails_closed_without_network_fallback(tmp_path, sample_pdf, changed):
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())])
    first = sess.fetch("2401.00396")
    pdf = Path(first.asset.pdf_path)
    if changed == "pdf":
        pdf.write_bytes(pdf.read_bytes().replace(b"Hybrid", b"HYBRID"))
    elif changed == "manifest":
        path = pdf.parent / "manifest.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["version"] += 1
        path.write_text(json.dumps(metadata), encoding="utf-8")
    else:
        pdf.unlink()
    result = sess.fetch("2401.00396")
    assert result.error_code == "invalid_cache" and result.network_requests == result.fetch_attempts == 0
    assert len(sess.fetcher.transport.calls) == 1


def test_versions_have_distinct_cache_keys_and_real_client_does_not_use_injected_cache_online(tmp_path, sample_pdf):
    row = candidates()[1]
    first = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())], rows=[row])
    original = first.fetch(row.arxiv_id)
    revised = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())], rows=[replace(row, version=3)])
    newer = revised.fetch(row.arxiv_id)
    assert Path(original.asset.pdf_path).parent != Path(newer.asset.pdf_path).parent
    real = PaperFetchSession(task(), [row], PaperFetcher(cache_directory=first.fetcher.cache_directory))
    assert real.fetch(row.arxiv_id).error_code == "invalid_cache"
    replay = real.fetch(row.arxiv_id, offline=True)
    assert replay.from_cache and replay.asset.origin == "injected_transport"


def test_failed_downloads_and_redirects_cannot_exceed_fetch_attempt_limit(tmp_path, sample_pdf):
    limits = ResearchLimits(max_fulltext_papers=1, max_fetch_attempts=1)
    sess = session(tmp_path, [FakeResponse(status=404)], research_task=task(target_papers=1, limits=limits))
    assert sess.fetch("2401.00396").status == "unavailable"
    stopped = sess.fetch("2401.00544")
    assert stopped.error_code == "fetch_budget_exhausted" and stopped.status == "budget_exhausted"
    assert sess.fetch_attempts == len(sess.fetcher.transport.calls) == 1
    assert_no_valid_cache(tmp_path)
    sess = session(tmp_path, [FakeResponse(status=302, location="/pdf/2401.00396v2.pdf")],
                   research_task=task(target_papers=1, limits=limits))
    assert sess.fetch("2401.00396").error_code == "fetch_budget_exhausted"
    assert len(sess.fetcher.transport.calls) == 1
    assert_no_valid_cache(tmp_path)


def test_cached_papers_count_toward_successful_paper_limit_and_repeat_hit_is_allowed_after_attempt_limit(tmp_path, sample_pdf):
    body = sample_pdf.read_bytes()
    warmer = session(tmp_path, [FakeResponse(body), FakeResponse(body)])
    warmer.fetch("2401.00396")
    warmer.fetch("2401.00544")
    limited = session(tmp_path, [], research_task=task(target_papers=1))
    assert limited.fetch("2401.00396", offline=True).from_cache
    assert limited.fetch("2401.00544", offline=True).error_code == "paper_budget_exhausted"
    assert limited.fetch_attempts == 0 and len(limited.downloaded_assets) == 1
    limits = ResearchLimits(max_fulltext_papers=1, max_fetch_attempts=1)
    repeat = session(tmp_path, [], research_task=task(target_papers=1, limits=limits))
    repeat.fetch_attempts = 1
    assert repeat.fetch("2401.00396", offline=True).from_cache
    assert repeat.fetch_attempts == 1


def test_offline_miss_and_invalid_parameters_do_not_request_network(tmp_path):
    sess = session(tmp_path, [])
    assert sess.fetch("2401.00396", offline=True).error_code == "cache_miss"
    with pytest.raises(ValueError):
        sess.fetch("2401.00396", timeout=0)
    assert sess.fetcher.network_requests == sess.fetch_attempts == 0


def test_legacy_uppercase_subject_id_is_supported_without_path_based_cache_names(tmp_path, sample_pdf):
    row = replace(candidates()[0], arxiv_id="math.GT/0309132", version=1)
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())], rows=[row])
    result = sess.fetch(row.arxiv_id)
    assert result.status == "downloaded"
    assert result.asset.requested_url == "https://arxiv.org/pdf/math.GT/0309132v1"
    assert len(Path(result.asset.pdf_path).parent.name) == 64


def search_report(path, rows=None, *, origin="live_arxiv_api"):
    req = SearchRequest("retrieval augmented generation", task().start_at, task().end_at)
    path.write_text(json.dumps({"status": "success", "snapshot_origin": origin, "request": req.to_dict(),
                               "candidates": [row.to_dict() for row in (candidates() if rows is None else rows)]}), encoding="utf-8")
    return path


def test_loading_search_report_and_cli_offline_replay_without_utf8_regression(tmp_path, sample_pdf):
    report = search_report(tmp_path / "search.json")
    parsed_task, rows = load_search_candidates(report)
    assert len(rows) == 3 and parsed_task.start_at == task().start_at
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())])
    sess.fetch("2401.00396")
    root = Path(__file__).parents[1]
    output = tmp_path / "fetch.json"
    command = [sys.executable, str(root / "scripts" / "fetch_papers.py"), "--search-results", str(report),
               "--arxiv-id", "2401.00396", "--cache-directory", str(sess.fetcher.cache_directory),
               "--offline", "--output", str(output)]
    result = subprocess.run(command, cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "gbk"},
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["from_cache"] and payload["status"] == "downloaded" and not payload["indexed"]
    assert payload["llm_calls"] == payload["network_requests"] == 0
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_summary_or_injected_search_report_is_not_a_live_fetch_registry(tmp_path):
    path = search_report(tmp_path / "search.json", origin="injected_transport")
    with pytest.raises(ValueError, match="Invalid search report"):
        load_search_candidates(path)
    assert load_search_candidates(path, allow_injected=True)[1]
    path.write_text('{"status":"success","candidates":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_search_candidates(path)


class MockHTTPStream:
    code = 200
    def __init__(self, body):
        self.body = io.BytesIO(body)
        self.headers = {"Content-Type": "application/pdf", "Content-Length": str(len(body))}
        self.timeouts = []
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.body.close()
    def read(self, size):
        return self.body.read(size)


def test_default_transport_streams_sets_headers_and_shrinks_socket_timeout(monkeypatch):
    import paper_assistant.literature.fetcher as module
    response = MockHTTPStream(b"pdf bytes")
    requests = []
    def open_request(request, *, timeout):
        requests.append(request)
        return response
    monkeypatch.setattr(module, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    with UrllibPDFTransport().open("https://arxiv.org/pdf/2401.00396v2", timeout=3) as result:
        assert result.read(3, timeout=2) == b"pdf"
        assert result.content_length == "9" and result.status == 200
    assert response.timeouts == [2]
    assert requests[0].get_header("Accept-encoding") == "identity"


@pytest.mark.parametrize("error,code", [(URLError("failed"), "connection_error"), (TimeoutError(), "timeout")])
def test_default_transport_normalizes_network_errors_without_retry(monkeypatch, error, code):
    import paper_assistant.literature.fetcher as module
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise error
    monkeypatch.setattr(module, "build_opener", lambda *args: SimpleNamespace(open=fail))
    with pytest.raises(PaperFetchError) as caught:
        with UrllibPDFTransport().open("https://arxiv.org/pdf/2401.00396v2", timeout=3):
            pytest.fail("No response expected")
    assert caught.value.code == code and len(calls) == 1


def test_default_transport_surfaces_redirect_headers_without_following(monkeypatch):
    import paper_assistant.literature.fetcher as module
    handlers = []
    def opener(*args):
        handlers.extend(args)
        def fail(request, *, timeout):
            raise HTTPError(request.full_url, 302, "redirect", {"Location": "/pdf/2401.00396v2.pdf"}, io.BytesIO(b"error"))
        return SimpleNamespace(open=fail)
    monkeypatch.setattr(module, "build_opener", opener)
    with UrllibPDFTransport().open("https://arxiv.org/pdf/2401.00396v2", timeout=3) as response:
        assert response.status == 302 and response.location.endswith(".pdf")
    assert handlers[0].redirect_request(None, None, 302, "", {}, "https://evil.example") is None


def test_replacement_candidate_succeeds_within_remaining_budget(tmp_path, sample_pdf):
    limits = ResearchLimits(max_fulltext_papers=1, max_fetch_attempts=2)
    sess = session(tmp_path, [FakeResponse(status=404), FakeResponse(sample_pdf.read_bytes())],
                   research_task=task(target_papers=1, limits=limits))
    assert sess.fetch("2401.00396").status == "unavailable"
    replacement = sess.fetch("2401.00544")
    assert replacement.status == "downloaded" and sess.fetch_attempts == 2
    assert sess.fetch("2401.00280").error_code == "paper_budget_exhausted"
    assert len(sess.fetcher.transport.calls) == 2


def test_redirect_chain_cannot_exceed_two_hops(tmp_path):
    sess = session(tmp_path, [
        FakeResponse(status=302, location="https://export.arxiv.org/pdf/2401.00396v2"),
        FakeResponse(status=302, location="https://www.arxiv.org/pdf/2401.00396v2"),
        FakeResponse(status=302, location="https://arxiv.org/pdf/2401.00396v2.pdf"),
    ])
    result = sess.fetch("2401.00396")
    assert result.error_code == "redirect_limit"
    assert result.fetch_attempts == result.network_requests == 3
    assert_no_valid_cache(tmp_path)


def test_shared_gate_timeout_counts_attempt_but_sends_no_request(tmp_path):
    class DeadlineGate:
        @contextmanager
        def slot(self, deadline):
            raise LiteratureSearchError("timeout", "No time to wait")
            yield
    sess = session(tmp_path, [])
    sess.fetcher.gate = DeadlineGate()
    result = sess.fetch("2401.00396")
    assert result.error_code == "timeout" and result.fetch_attempts == 1 and result.network_requests == 0
    assert_no_valid_cache(tmp_path)


def test_manifest_publish_failure_does_not_create_a_valid_cache_and_retry_recovers(tmp_path, sample_pdf, monkeypatch):
    import paper_assistant.literature.fetcher as module
    body = sample_pdf.read_bytes()
    sess = session(tmp_path, [FakeResponse(body), FakeResponse(body)])
    original = module.atomic_write_bytes
    def fail(*args):
        raise OSError("Synthetic disk failure")
    monkeypatch.setattr(module, "atomic_write_bytes", fail)
    failed = sess.fetch("2401.00396")
    assert failed.error_code == "storage_error"
    # A fully transferred content file may remain, but without the commit
    # manifest it is not a cache hit and can never be ingested as a valid asset.
    assert not list((tmp_path / "cache").rglob("manifest.json"))
    assert not list((tmp_path / "cache").rglob("*.part"))
    assert sess.fetch("2401.00396", offline=True).error_code == "cache_miss"
    monkeypatch.setattr(module, "atomic_write_bytes", original)
    retried = sess.fetch("2401.00396")
    assert retried.status == "downloaded" and not retried.from_cache
    assert sess.fetch_attempts == 2


def test_pdf_cache_root_rejects_entry_symlink_escape(tmp_path, sample_pdf):
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes())])
    root = sess.fetcher.cache_directory
    root.mkdir()
    key = hashlib.sha256(b"arxiv:2401.00396:v2").hexdigest()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / key).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    result = sess.fetch("2401.00396")
    assert result.error_code == "invalid_cache" and result.network_requests == 0
    assert not list(outside.iterdir())
