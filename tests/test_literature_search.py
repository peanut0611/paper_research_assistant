import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from paper_assistant.literature.search import (
    ATOM, MAX_RESPONSE_BYTES, OPEN_SEARCH, ArxivSearchClient, FileRequestGate, HTTPResponse,
    LiteratureSearchError, LiteratureSearchSession, SearchRequest, UrllibTransport, parse_arxiv_feed,
)
from paper_assistant.agent.task import ResearchLimits, ResearchTask


FIXTURE = Path(__file__).parent / "fixtures" / "arxiv" / "synthetic_feed.xml"
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = list(responses or [HTTPResponse(200, "application/atom+xml; charset=utf-8", FIXTURE.read_bytes())])
        self.calls = []

    def get(self, url, *, timeout, max_bytes):
        self.calls.append((url, timeout, max_bytes))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class NoWaitGate:
    def __init__(self):
        self.calls = 0

    @contextmanager
    def slot(self, deadline):
        self.calls += 1
        yield


def request(**kwargs):
    return SearchRequest(**{
        "query": "hybrid retrieval", "start_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "end_at": datetime(2026, 10, 1, tzinfo=timezone.utc), **kwargs,
    })


def client(tmp_path, transport=None, **kwargs):
    return ArxivSearchClient(transport=transport or FakeTransport(), cache_directory=tmp_path / "snapshots",
                             gate=NoWaitGate(), now=lambda: NOW, **kwargs)


def feed_with_entries(entries):
    feed = ET.Element(ATOM + "feed")
    ET.SubElement(feed, OPEN_SEARCH + "totalResults").text = str(len(entries))
    ET.SubElement(feed, OPEN_SEARCH + "startIndex").text = "0"
    feed.extend(entries)
    return ET.tostring(feed, encoding="utf-8")


def test_query_literalizes_operators_url_encodes_and_uses_utc_minute_bounds():
    req = request(query='"hybrid retrieval" OR ti:中文',
                  start_at=datetime.fromisoformat("2026-09-01T00:00:30+08:00"))
    params = parse_qs(urlsplit(req.url).query)
    assert params["start"] == ["0"] and params["max_results"] == ["5"]
    assert params["sortBy"] == ["submittedDate"]
    assert params["search_query"] == [req.api_query]
    assert 'ti:"hybrid retrieval"' in req.api_query
    assert 'ti:"OR"' in req.api_query and 'ti:"中文"' in req.api_query
    assert req.api_query.endswith("submittedDate:[202608311600 TO 202610010000]")
    assert req.to_dict()["start_at"].endswith("+08:00")
    assert "中文" not in req.url


@pytest.mark.parametrize("changes", [
    {"query": "!!!"}, {"query": ""}, {"query": '"unclosed'}, {"query": "line\nbreak"},
    {"max_results": 11}, {"max_results": True}, {"sort_by": "random"},
    {"start_at": datetime(2026, 1, 1)}, {"end_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
])
def test_invalid_search_request_is_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_parse_preserves_provenance_and_selects_highest_version_per_base_id(tmp_path):
    result = client(tmp_path).search(request())
    assert result.total_results == 4 and result.duplicate_entries == 1
    assert len(result.observed_candidates) == 3
    assert result.excluded_date_ids == ("2610.00003", "2608.00002")
    assert [paper.arxiv_id for paper in result.candidates] == ["2609.00001"]
    paper = result.candidates[0]
    assert paper.version == 2 and paper.title.endswith("Revision")
    assert paper.pdf_url == "https://arxiv.org/pdf/2609.00001v2"
    assert paper.original_pdf_url == "http://arxiv.org/pdf/2609.00001v2"
    assert paper.fetched_at == NOW and paper.query == "hybrid retrieval"
    assert paper.fulltext_status == "abstract_only" and paper.raw_entry_xml
    assert result.snapshot_origin == "injected_transport"
    assert not result.from_cache and Path(result.snapshot_path).is_file()
    assert json.loads(json.dumps(result.to_dict()))["candidates"][0]["version"] == 2


def test_exact_second_boundaries_are_filtered_after_minute_query(tmp_path):
    root = ET.fromstring(FIXTURE.read_bytes())
    entry = root.findall(ATOM + "entry")[0]
    body = feed_with_entries([entry])
    transport = FakeTransport([HTTPResponse(200, "application/xml", body)])
    result = client(tmp_path, transport).search(request(
        start_at=datetime.fromisoformat("2026-09-10T12:00:01Z"),
        end_at=datetime.fromisoformat("2026-09-10T12:00:30Z")))
    assert not result.candidates and result.excluded_date_ids == ("2609.00001",)


def test_empty_feed_is_success_not_connection_error(tmp_path):
    result = client(tmp_path, FakeTransport([HTTPResponse(200, "text/xml", feed_with_entries([]))])).search(request())
    assert not result.candidates and result.total_results == 0


@pytest.mark.parametrize("status,expected", [(429, "rate_limited"), (503, "server_error"), (400, "http_error"), (302, "http_error")])
def test_http_failures_are_not_cached_retried_or_converted_to_no_results(tmp_path, status, expected):
    transport = FakeTransport([HTTPResponse(status, "text/html", b"private failure details")])
    instance = client(tmp_path, transport)
    with pytest.raises(LiteratureSearchError) as caught:
        instance.search(request())
    assert caught.value.code == expected and caught.value.status == status
    assert "private" not in str(caught.value)
    assert len(transport.calls) == 1 and not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("body", [
    b"<html>gateway error</html>", b"not XML", b"<feed/>",
    b'<!DOCTYPE feed [<!ENTITY x "expanded">]><feed>&x;</feed>',
    b"x" * (MAX_RESPONSE_BYTES + 1),
    b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id></entry></feed>',
], ids=["html", "not-xml", "namespace", "dtd", "oversized", "api-error"])
def test_bad_or_api_error_xml_never_becomes_a_successful_empty_feed(tmp_path, body):
    instance = client(tmp_path, FakeTransport([HTTPResponse(200, "application/atom+xml", body)]))
    with pytest.raises(LiteratureSearchError) as caught:
        instance.search(request())
    assert caught.value.code in ("invalid_response", "api_error")
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("replacement", [
    "https://evil.example/abs/2609.00001v1", "https://arxiv.org@evil.example/abs/2609.00001v1",
    "https://arxiv.org/abs/2609.00001v1?redirect=x", "https://arxiv.org/abs/2613.00001v1",
])
def test_untrusted_source_urls_or_ids_are_rejected(replacement):
    body = FIXTURE.read_bytes().replace(b"http://arxiv.org/abs/2609.00001v1", replacement.encode())
    with pytest.raises(LiteratureSearchError, match="Invalid arXiv Atom"):
        parse_arxiv_feed(body, request(), NOW)


def test_conflicting_submission_dates_or_foreign_pdf_links_are_rejected():
    body = FIXTURE.read_bytes().replace(b"http://arxiv.org/pdf/2609.00001v2", b"https://elsewhere.example/a.pdf")
    with pytest.raises(LiteratureSearchError):
        parse_arxiv_feed(body, request(), NOW)
    entries = ET.fromstring(FIXTURE.read_bytes()).findall(ATOM + "entry")[:2]
    entries[1].find(ATOM + "published").text = "2026-09-11T12:00:00Z"
    with pytest.raises(LiteratureSearchError):
        parse_arxiv_feed(feed_with_entries(entries), request(), NOW)


def test_legacy_identifier_and_relevance_order_are_preserved():
    entries = ET.fromstring(FIXTURE.read_bytes()).findall(ATOM + "entry")
    entry = entries[2]
    entry.find(ATOM + "id").text = "http://arxiv.org/abs/hep-ex/0307015"
    rows, _, _ = parse_arxiv_feed(feed_with_entries([entry, entries[0]]), request(sort_by="relevance"), NOW)
    assert [row.arxiv_id for row in rows] == ["hep-ex/0307015", "2609.00001"]
    assert rows[0].version is None


def test_response_entry_limit_and_content_type_are_enforced(tmp_path):
    with pytest.raises(LiteratureSearchError):
        parse_arxiv_feed(FIXTURE.read_bytes(), request(max_results=1), NOW)
    instance = client(tmp_path, FakeTransport([HTTPResponse(200, "text/html", FIXTURE.read_bytes())]))
    with pytest.raises(LiteratureSearchError, match="XML metadata"):
        instance.search(request())


def test_same_request_uses_cache_without_request_gate_and_keeps_original_fetch_time(tmp_path):
    instance = client(tmp_path)
    first = instance.search(request())
    instance.now = lambda: NOW + timedelta(hours=1)
    cached = instance.search(request())
    assert cached.from_cache and not cached.stale_cache
    assert cached.fetched_at == first.fetched_at and cached.served_at > cached.fetched_at
    assert instance.gate.calls == instance.network_requests == 1


def test_expired_cache_refreshes_online_but_replays_explicitly_offline(tmp_path):
    transport = FakeTransport([HTTPResponse(200, "application/xml", FIXTURE.read_bytes())] * 2)
    instance = client(tmp_path, transport)
    instance.search(request())
    instance.now = lambda: NOW + timedelta(days=2)
    old = instance.search(request(), offline=True)
    assert old.from_cache and old.stale_cache and instance.network_requests == 1
    fresh = instance.search(request())
    assert not fresh.from_cache and fresh.fetched_at == NOW + timedelta(days=2)
    assert instance.network_requests == 2


def test_offline_cache_miss_or_changed_raw_body_does_not_request_network(tmp_path):
    instance = client(tmp_path)
    with pytest.raises(LiteratureSearchError) as caught:
        instance.search(request(), offline=True)
    assert caught.value.code == "cache_miss" and not instance.network_requests
    result = instance.search(request())
    path = Path(result.snapshot_path)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["body_base64"] = base64.b64encode(b"changed").decode()
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(LiteratureSearchError) as caught:
        instance.search(request())
    assert caught.value.code == "invalid_cache" and instance.network_requests == 1


def task(**kwargs):
    req = request()
    return ResearchTask("hybrid retrieval", ("Compare methods",), req.start_at, req.end_at, **kwargs)


def test_session_counts_all_observed_ids_not_just_in_window_and_uses_remaining_capacity(tmp_path):
    transport = FakeTransport([HTTPResponse(200, "application/xml", FIXTURE.read_bytes())] * 2)
    instance = client(tmp_path, transport)
    session = LiteratureSearchSession(task(limits=ResearchLimits(max_candidates=7)), instance)
    first = session.search("hybrid retrieval")
    assert first.request.max_results == 5
    assert len(session.observed_candidates) == 3 and len(first.candidates) == 1
    second = session.search("hybrid retrieval evidence")
    assert second.request.max_results == 4
    assert len(session.observed_candidates) == 3
    assert session.history[-1]["cumulative_candidates"] == 3
    with pytest.raises(LiteratureSearchError) as caught:
        session.search("another")
    assert caught.value.code == "search_budget_exhausted" and instance.network_requests == 2


def test_failed_search_and_cached_search_both_consume_rounds(tmp_path):
    transport = FakeTransport([LiteratureSearchError("connection_error", "failed"),
                               HTTPResponse(200, "application/xml", FIXTURE.read_bytes())])
    session = LiteratureSearchSession(task(), client(tmp_path, transport))
    with pytest.raises(LiteratureSearchError):
        session.search("hybrid retrieval")
    assert session.search_rounds == 1 and session.history[0]["status"] == "error"
    session.search("hybrid retrieval", max_results=5)
    with pytest.raises(LiteratureSearchError, match="rounds"):
        session.search("hybrid retrieval")
    cached = LiteratureSearchSession(task(), session.client)
    cached.search("hybrid retrieval", max_results=5)
    cached.search("hybrid retrieval", max_results=5)
    assert all(event["from_cache"] and event["network_requests"] == 0 for event in cached.history)
    with pytest.raises(LiteratureSearchError):
        cached.search("hybrid retrieval")


def test_candidate_cap_and_first_round_cap_do_not_silently_expand(tmp_path):
    session = LiteratureSearchSession(task(), client(tmp_path))
    with pytest.raises(ValueError, match="at most 5"):
        session.search("hybrid retrieval", max_results=6)
    assert session.search_rounds == 0
    session = LiteratureSearchSession(task(limits=ResearchLimits(max_candidates=3, first_search_candidates=3)), client(tmp_path))
    entries = ET.fromstring(FIXTURE.read_bytes()).findall(ATOM + "entry")
    session.client.transport = FakeTransport([HTTPResponse(200, "application/xml", feed_with_entries([entries[0], entries[2], entries[3]]))])
    session.search("hybrid retrieval")
    with pytest.raises(LiteratureSearchError) as caught:
        session.search("different")
    assert caught.value.code == "candidate_budget_exhausted"


class FakeTime:
    def __init__(self):
        self.value = 100.0
        self.sleeps = []
    def clock(self):
        return self.value
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def test_shared_gate_enforces_three_seconds_across_gate_instances_and_failed_requests(tmp_path):
    clock = FakeTime()
    gate = FileRequestGate(tmp_path, clock=clock.clock, monotonic=clock.clock, sleep=clock.sleep)
    with pytest.raises(RuntimeError):
        with gate.slot(150):
            clock.value += 2
            raise RuntimeError("request failed")
    second = FileRequestGate(tmp_path, clock=clock.clock, monotonic=clock.clock, sleep=clock.sleep)
    with second.slot(150):
        assert clock.value == 105
    assert clock.sleeps == [3.0]
    with pytest.raises(LiteratureSearchError, match="rate limit"):
        with gate.slot(106):
            pytest.fail("Must not request before budget permits rate-limit wait")


def test_shared_gate_serializes_connections_and_bounds_lock_wait(tmp_path):
    gate = FileRequestGate(tmp_path)
    observed = []
    def contender():
        try:
            with FileRequestGate(tmp_path).slot(time.monotonic() + 0.15):
                observed.append("incorrectly acquired")
        except LiteratureSearchError as error:
            observed.append(error.code)
    with gate.slot(time.monotonic() + 2):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive() and observed == ["timeout"]


def test_cli_can_replay_fixture_without_network_and_emit_utf8_in_gbk_environment(tmp_path):
    req = request()
    instance = client(tmp_path)
    instance.search(req)
    root = Path(__file__).parents[1]
    output = tmp_path / "result.json"
    run = subprocess.run([
        sys.executable, str(root / "scripts" / "search_literature.py"), req.query,
        "--start", req.start_at.isoformat(), "--end", req.end_at.isoformat(), "--offline",
        "--cache-directory", str(instance.cache_directory), "--output", str(output),
    ], cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "gbk"},
        capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["snapshot_origin"] == "injected_transport" and result["from_cache"]
    assert result["llm_calls"] == result["pdf_fetch_attempts"] == 0
    assert result["discovery_usage"][0]["network_requests"] == 0
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_frozen_live_response_replays_original_metadata_without_network():
    root = FIXTURE.parent
    metadata = json.loads((root / "live_20261023.meta.json").read_text(encoding="utf-8"))
    body = (root / "live_20261023.xml").read_bytes()
    assert metadata["synthetic"] is False
    assert hashlib.sha256(body).hexdigest() == metadata["raw_sha256"]
    data = metadata["request"]
    req = SearchRequest(data["query"], datetime.fromisoformat(data["start_at"]),
                        datetime.fromisoformat(data["end_at"]), data["max_results"], data["sort_by"])
    rows, total, duplicates = parse_arxiv_feed(body, req, datetime.fromisoformat(metadata["fetched_at"]))
    assert total == metadata["expected_total_results"] and duplicates == 0
    assert [{key: row.to_dict()[key] for key in expected} for row, expected in zip(rows, metadata["expected_candidates"], strict=True)] == metadata["expected_candidates"]
    assert all(req.start_at <= row.submitted_at < req.end_at for row in rows)
    assert all(row.updated_at >= req.end_at for row in rows)
    # arXiv identifier month is not a substitute for the actual published date.
    assert rows[0].arxiv_id.startswith("2401.") and rows[0].submitted_at.year == 2023


def test_utf16_dtd_is_rejected_before_xml_entity_expansion():
    body = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE feed [<!ENTITY x "test">]><feed>&x;</feed>'.encode("utf-16")
    with pytest.raises(LiteratureSearchError, match="unsupported XML"):
        parse_arxiv_feed(body, request(), NOW)


def test_live_client_cannot_silently_use_a_synthetic_online_cache(tmp_path):
    instance = client(tmp_path)
    instance.search(request())
    live = ArxivSearchClient(cache_directory=instance.cache_directory, now=lambda: NOW)
    with pytest.raises(LiteratureSearchError) as caught:
        live.search(request())
    assert caught.value.code == "invalid_cache" and live.network_requests == 0
    assert live.search(request(), offline=True).snapshot_origin == "injected_transport"


def test_session_cross_round_version_dedup_keeps_first_submission_consistent(tmp_path):
    entries = ET.fromstring(FIXTURE.read_bytes()).findall(ATOM + "entry")
    transport = FakeTransport([HTTPResponse(200, "application/xml", feed_with_entries([entry])) for entry in entries[:2]])
    session = LiteratureSearchSession(task(), client(tmp_path, transport))
    session.search("hybrid")
    session.search("hybrid revised")
    assert len(session.observed_candidates) == 1 and session.observed_candidates[0].version == 2


def test_cross_round_conflicting_dates_fail_without_replacing_registered_candidates(tmp_path):
    entries = ET.fromstring(FIXTURE.read_bytes()).findall(ATOM + "entry")[:2]
    entries[1].find(ATOM + "published").text = "2026-09-11T12:00:00Z"
    transport = FakeTransport([HTTPResponse(200, "application/xml", feed_with_entries([entry])) for entry in entries])
    session = LiteratureSearchSession(task(), client(tmp_path, transport))
    session.search("hybrid")
    with pytest.raises(LiteratureSearchError, match="Conflicting"):
        session.search("hybrid revised")
    assert session.history[-1]["status"] == "error"
    assert session.observed_candidates[0].version == 1


def test_invalid_timeout_does_not_consume_a_search_round(tmp_path):
    session = LiteratureSearchSession(task(), client(tmp_path))
    with pytest.raises(ValueError):
        session.search("hybrid", timeout=0)
    assert session.search_rounds == 0 and not session.history


def test_invalid_shared_rate_state_fails_closed_and_releases_lock(tmp_path):
    (tmp_path / "request.lock").write_bytes(b"0not-a-timestamp")
    gate = FileRequestGate(tmp_path)
    with pytest.raises(LiteratureSearchError) as caught:
        with gate.slot(time.monotonic() + 1):
            pytest.fail("Corrupt rate state must not allow a network request")
    assert caught.value.code == "rate_state_invalid"
    (tmp_path / "request.lock").write_bytes(b"0")
    with gate.slot(time.monotonic() + 1):
        pass


class FakeHTTPStream:
    status = 200
    headers = {"Content-Type": "application/atom+xml"}
    def __init__(self, body):
        self.body = io.BytesIO(body)
        self.timeouts = []
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.body.close()
    def read(self, count):
        return self.body.read(count)


def test_default_transport_bounds_bytes_and_sets_headers_and_remaining_socket_timeout(monkeypatch):
    import paper_assistant.literature.search as module
    response = FakeHTTPStream(b"xml-body")
    calls = []
    def open_request(req, *, timeout):
        calls.append((req, timeout))
        return response
    monkeypatch.setattr(module, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    result = UrllibTransport().get(request().url, timeout=1, max_bytes=100)
    assert result.body == b"xml-body" and result.status == 200
    assert calls[0][0].get_header("Accept") == "application/atom+xml"
    assert 0 < response.timeouts[0] <= 1
    response = FakeHTTPStream(b"oversized")
    with pytest.raises(LiteratureSearchError) as caught:
        UrllibTransport().get(request().url, timeout=1, max_bytes=3)
    assert caught.value.code == "response_too_large"


@pytest.mark.parametrize("error,code", [(URLError("unavailable"), "connection_error"), (TimeoutError(), "timeout")])
def test_default_transport_normalizes_connection_errors_without_retry(monkeypatch, error, code):
    import paper_assistant.literature.search as module
    calls = []
    def fail(req, *, timeout):
        calls.append(req)
        raise error
    monkeypatch.setattr(module, "build_opener", lambda *args: SimpleNamespace(open=fail))
    with pytest.raises(LiteratureSearchError) as caught:
        UrllibTransport().get(request().url, timeout=1, max_bytes=100)
    assert caught.value.code == code and len(calls) == 1


def test_default_transport_does_not_follow_redirects_or_echo_error_body(monkeypatch):
    import paper_assistant.literature.search as module
    from urllib.request import HTTPRedirectHandler
    handlers = []
    def build(*args):
        handlers.extend(args)
        def fail(req, *, timeout):
            raise HTTPError(req.full_url, 302, "Redirect", {}, io.BytesIO(b"untrusted body"))
        return SimpleNamespace(open=fail)
    monkeypatch.setattr(module, "build_opener", build)
    response = UrllibTransport().get(request().url, timeout=1, max_bytes=100)
    assert response.status == 302 and response.body == b""
    redirect = next(handler for handler in handlers if isinstance(handler, HTTPRedirectHandler))
    assert redirect.redirect_request(None, None, 302, "", {}, "https://elsewhere.example") is None
