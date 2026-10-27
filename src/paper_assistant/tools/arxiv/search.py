"""Bounded arXiv metadata search, reproducible snapshots, and discovery limits.

This module does not fetch PDFs, invoke a model, or infer that a paper was read.
Transport, clocks and request gate can be injected for fully offline tests.
"""

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from paper_assistant.research.task import ResearchTask
from paper_assistant.storage import atomic_write_bytes


ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
DEFAULT_CACHE = Path("data/literature_search")
MAX_RESPONSE_BYTES = 2_000_000
ATOM = "{http://www.w3.org/2005/Atom}"
OPEN_SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV = "{http://arxiv.org/schemas/atom}"
SORT_FIELDS = ("submittedDate", "relevance", "lastUpdatedDate")
_IDENTIFIER = re.compile(r"(?P<base>(?:\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|[a-z][A-Za-z0-9.-]*/\d{7}))(?P<version>v[1-9]\d*)?")


class LiteratureSearchError(Exception):
    """Machine-readable failure; an empty successful feed is a different result."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Search dates must include a UTC offset")
    return value.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def _validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("timeout must be in (0, 120] seconds")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    start_at: datetime
    end_at: datetime
    max_results: int = 5
    sort_by: str = "submittedDate"

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip() or len(self.query) > 500:
            raise ValueError("query must contain 1..500 characters")
        if any(ord(char) < 32 for char in self.query) or self.query.count('"') % 2:
            raise ValueError("Invalid control characters or unbalanced quotes in query")
        if not 1 <= len(self.terms) <= 20:
            raise ValueError("query must contain 1..20 words or quoted phrases")
        if _utc(self.start_at) >= _utc(self.end_at):
            raise ValueError("start_at must precede end_at")
        if type(self.max_results) is not int or not 1 <= self.max_results <= 10:
            raise ValueError("max_results must be an integer in 1..10")
        if self.sort_by not in SORT_FIELDS:
            raise ValueError("Unsupported arXiv sort field")

    @property
    def terms(self) -> tuple[str, ...]:
        # Plain text only. Field names, operators and range syntax from user or
        # model input cannot replace our date filter. Quotes group literal words.
        words = []
        for phrase, word in re.findall(r'"([^"]+)"|([^\W_]+(?:[-_][^\W_]+)*)', self.query):
            cleaned = " ".join(re.findall(r"[^\W_]+(?:[-_][^\W_]+)*", phrase or word))
            if cleaned:
                words.append(cleaned)
        return tuple(words)

    @property
    def api_query(self) -> str:
        terms = " AND ".join(f'(ti:"{term}" OR abs:"{term}")' for term in self.terms)
        # The source range is inclusive and minute-resolution. Fetch the boundary
        # minutes and enforce the exact half-open window on parsed timestamps.
        start = _utc(self.start_at).strftime("%Y%m%d%H%M")
        end = _utc(self.end_at).strftime("%Y%m%d%H%M")
        return f"({terms}) AND submittedDate:[{start} TO {end}]"

    def to_dict(self) -> dict:
        return {"query": self.query, "api_query": self.api_query,
                "start_at": self.start_at.isoformat(), "end_at": self.end_at.isoformat(),
                "utc_start_at": _utc(self.start_at).isoformat(), "utc_end_at": _utc(self.end_at).isoformat(),
                "boundary": "start_inclusive_end_exclusive", "date_field": "submitted",
                "max_results": self.max_results, "sort_by": self.sort_by, "sort_order": "descending"}

    @property
    def url(self) -> str:
        return ARXIV_ENDPOINT + "?" + urlencode({
            "search_query": self.api_query, "start": 0, "max_results": self.max_results,
            "sortBy": self.sort_by, "sortOrder": "descending",
        })

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PaperCandidate:
    arxiv_id: str
    version: int | None
    title: str
    authors: tuple[str, ...]
    abstract: str
    categories: tuple[str, ...]
    primary_category: str | None
    submitted_at: datetime
    updated_at: datetime
    abstract_url: str
    pdf_url: str
    original_id_url: str
    original_pdf_url: str | None
    fetched_at: datetime
    query: str
    raw_entry_xml: str
    fulltext_status: str = "abstract_only"

    def to_dict(self) -> dict:
        result = asdict(self)
        for name in ("submitted_at", "updated_at", "fetched_at"):
            result[name] = getattr(self, name).isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "PaperCandidate":
        """Read trusted persisted metadata, never model-supplied registration."""
        values = dict(data)
        for name in ("submitted_at", "updated_at", "fetched_at"):
            values[name] = _utc(datetime.fromisoformat(values[name]))
        for name in ("authors", "categories"):
            if not isinstance(values[name], list) or not all(isinstance(item, str) for item in values[name]):
                raise ValueError("Invalid candidate metadata")
            values[name] = tuple(values[name])
        candidate = cls(**values)
        identifier, version = parse_arxiv_url(candidate.original_id_url, kind="abs")
        if (identifier, version) != (candidate.arxiv_id, candidate.version):
            raise ValueError("Invalid persisted candidate identity")
        return candidate


@dataclass(frozen=True)
class SearchResult:
    request: SearchRequest
    candidates: tuple[PaperCandidate, ...]
    observed_candidates: tuple[PaperCandidate, ...]
    excluded_date_ids: tuple[str, ...]
    duplicate_entries: int
    total_results: int
    fetched_at: datetime
    served_at: datetime
    from_cache: bool
    stale_cache: bool
    snapshot_origin: str
    snapshot_path: str | None
    raw_sha256: str

    def to_dict(self) -> dict:
        return {"status": "success", "request": self.request.to_dict(),
                "candidates": [c.to_dict() for c in self.candidates],
                "observed_candidates": [c.to_dict() for c in self.observed_candidates],
                "excluded_date_ids": list(self.excluded_date_ids), "duplicate_entries": self.duplicate_entries,
                "total_results": self.total_results, "fetched_at": self.fetched_at.isoformat(),
                "served_at": self.served_at.isoformat(), "from_cache": self.from_cache,
                "stale_cache": self.stale_cache, "snapshot_origin": self.snapshot_origin,
                "snapshot_path": self.snapshot_path, "raw_sha256": self.raw_sha256,
                "coverage": "One bounded response from arXiv, not an exhaustive literature search."}


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    content_type: str
    body: bytes


class SearchTransport(Protocol):
    def get(self, url: str, *, timeout: float, max_bytes: int) -> HTTPResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """One HTTPS request, bounded reads, no redirects and no automatic retries."""

    def get(self, url: str, *, timeout: float, max_bytes: int) -> HTTPResponse:
        started = time.monotonic()
        request = Request(url, headers={"Accept": "application/atom+xml", "User-Agent": "PaperResearchAssistant/0.1 (personal research prototype)"})
        try:
            with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                pieces: list[bytes] = []
                size = 0
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise LiteratureSearchError("timeout", "arXiv response exceeded the request deadline")
                    # urllib's timeout otherwise applies afresh to every socket
                    # read. Shrink it to the remaining wall-clock allowance.
                    socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                    if socket is not None:
                        socket.settimeout(remaining)
                    read = getattr(response, "read1", response.read)
                    chunk = read(min(65536, max_bytes + 1 - size))
                    if not chunk:
                        break
                    pieces.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        raise LiteratureSearchError("response_too_large", "arXiv response exceeded the byte limit")
                return HTTPResponse(response.status, response.headers.get("Content-Type", ""), b"".join(pieces))
        except HTTPError as error:
            status = error.code
            error.close()
            return HTTPResponse(status, "", b"")
        except (TimeoutError, URLError, OSError, HTTPException) as error:
            code = "timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "connection_error"
            raise LiteratureSearchError(code, f"arXiv request failed ({code}); no automatic retry was made") from error


class FileRequestGate:
    """Serialize network calls across clients/processes sharing this directory.

    Keep the lock during the request, then wait at least three seconds after its
    completion before the next request. Multiple machines require external
    coordination; this local gate does not claim to enforce a global rate.
    """

    def __init__(self, directory: str | Path = DEFAULT_CACHE, *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.directory = Path(directory)
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep

    @staticmethod
    def _lock(file, unlock: bool = False) -> None:
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)

    @contextmanager
    def slot(self, deadline: float) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.directory / "request.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+b", buffering=0) as file:
            if os.fstat(file.fileno()).st_size == 0:
                file.write(b"0")
            while True:
                if self.monotonic() >= deadline:
                    raise LiteratureSearchError("timeout", "Deadline expired waiting for the shared arXiv connection")
                try:
                    self._lock(file)
                    break
                except OSError as error:
                    if error.errno not in (11, 13, 36):
                        raise
                    self.sleep(min(0.1, max(0, deadline - self.monotonic())))
            started = False
            try:
                file.seek(1)
                try:
                    stored = file.read().decode("ascii")
                    last = float(stored) if stored else 0.0
                except (ValueError, UnicodeError) as error:
                    raise LiteratureSearchError("rate_state_invalid", "Invalid arXiv rate state; no request sent") from error
                if not math.isfinite(last) or last < 0:
                    raise LiteratureSearchError("rate_state_invalid", "Invalid arXiv rate state; no request sent")
                delay = max(0, last + 3.0 - self.clock())
                if self.monotonic() + delay >= deadline:
                    raise LiteratureSearchError("timeout", "Deadline cannot accommodate the arXiv rate limit")
                if delay:
                    self.sleep(delay)
                started = True
                yield
            finally:
                if started:
                    file.seek(1)
                    file.write(str(self.clock()).encode("ascii"))
                    file.truncate()
                self._lock(file, unlock=True)


_SHARED_GATE = FileRequestGate()


def parse_arxiv_url(url: str, *, kind: str) -> tuple[str, int | None]:
    """Validate an arXiv metadata URL and extract its base ID and version."""
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or parsed.netloc not in ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
            or parsed.query or parsed.fragment or not parsed.path.startswith(f"/{kind}/")):
        raise ValueError("Invalid arXiv source URL")
    identifier = parsed.path[len(kind) + 2:]
    if kind == "pdf" and identifier.endswith(".pdf"):
        identifier = identifier[:-4]
    match = _IDENTIFIER.fullmatch(identifier)
    if match is None:
        raise ValueError("Invalid arXiv identifier")
    return match["base"], int(match["version"][1:]) if match["version"] else None


def _text(element: ET.Element, tag: str) -> str:
    value = element.findtext(ATOM + tag)
    if value is None or not value.strip():
        raise ValueError(f"Missing Atom {tag}")
    return " ".join(value.split())


def parse_arxiv_feed(body: bytes, request: SearchRequest, fetched_at: datetime) -> tuple[tuple[PaperCandidate, ...], int, int]:
    """Normalize a bounded feed; keep the highest returned version per base ID."""
    if len(body) > MAX_RESPONSE_BYTES or b"\x00" in body or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.IGNORECASE):
        raise LiteratureSearchError("invalid_response", "Oversized or unsupported XML response")
    try:
        feed = ET.fromstring(body)
        if feed.tag != ATOM + "feed":
            raise ValueError("Expected an Atom feed")
        entries = feed.findall(ATOM + "entry")
        if any("/api/errors" in entry.findtext(ATOM + "id", "") for entry in entries):
            raise LiteratureSearchError("api_error", "arXiv returned an API error entry; check the query")
        total = int(feed.findtext(OPEN_SEARCH + "totalResults", ""))
        start = int(feed.findtext(OPEN_SEARCH + "startIndex", ""))
        if total < len(entries) or start != 0 or len(entries) > request.max_results:
            raise ValueError("Invalid Atom result counts")
        candidates: dict[str, PaperCandidate] = {}
        for entry in entries:
            original_id = _text(entry, "id")
            base_id, version = parse_arxiv_url(original_id, kind="abs")
            submitted, updated = _timestamp(_text(entry, "published")), _timestamp(_text(entry, "updated"))
            if updated < submitted:
                raise ValueError("Revision predates the initial submission")
            authors = tuple(_text(author, "name") for author in entry.findall(ATOM + "author"))
            categories = tuple(dict.fromkeys(c.attrib["term"] for c in entry.findall(ATOM + "category")))
            if not authors or not categories:
                raise ValueError("Missing authors or categories")
            pdf_links = [link.attrib["href"] for link in entry.findall(ATOM + "link")
                         if link.get("title") == "pdf" or link.get("type") == "application/pdf"]
            original_pdf = pdf_links[0] if pdf_links else None
            if original_pdf:
                pdf_id, pdf_version = parse_arxiv_url(original_pdf, kind="pdf")
                if pdf_id != base_id or pdf_version is not None and pdf_version != version:
                    raise ValueError("PDF source does not match the entry ID")
            suffix = f"v{version}" if version is not None else ""
            primary = entry.find(ARXIV + "primary_category")
            candidate = PaperCandidate(
                base_id, version, _text(entry, "title"), authors, _text(entry, "summary"), categories,
                primary.get("term") if primary is not None else None, submitted, updated,
                f"https://arxiv.org/abs/{base_id}{suffix}", f"https://arxiv.org/pdf/{base_id}{suffix}",
                original_id, original_pdf, _utc(fetched_at), request.query,
                ET.tostring(entry, encoding="unicode"),
            )
            previous = candidates.get(base_id)
            if previous is not None and previous.submitted_at != candidate.submitted_at:
                raise ValueError("Conflicting initial submission dates for the same arXiv ID")
            if previous is None or (candidate.version or 0, candidate.updated_at) > (previous.version or 0, previous.updated_at):
                candidates[base_id] = candidate
        rows = list(candidates.values())
        if request.sort_by != "relevance":
            rows.sort(key=lambda row: row.submitted_at if request.sort_by == "submittedDate" else row.updated_at, reverse=True)
        return tuple(rows), total, len(entries) - len(rows)
    except (ValueError, TypeError, KeyError, ET.ParseError, OverflowError) as error:
        raise LiteratureSearchError("invalid_response", "Invalid arXiv Atom response; no search conclusion can be drawn") from error


class ArxivSearchClient:
    def __init__(self, *, transport: SearchTransport | None = None, cache_directory: str | Path = DEFAULT_CACHE / "snapshots",
                 gate: FileRequestGate | None = None, cache_ttl_seconds: float = 86400,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds < 0:
            raise ValueError("Invalid cache TTL")
        self.transport = transport if transport is not None else UrllibTransport()
        self.snapshot_origin = "live_arxiv_api" if transport is None else "injected_transport"
        self.cache_directory = Path(cache_directory)
        self.gate = gate if gate is not None else _SHARED_GATE
        self.cache_ttl_seconds, self.now, self.monotonic = cache_ttl_seconds, now, monotonic
        self.network_requests = 0

    def search(self, request: SearchRequest, *, offline: bool = False, timeout: float = 30) -> SearchResult:
        _validate_timeout(timeout)
        path = self.cache_directory / f"{request.cache_key}.json"
        served_at = _utc(self.now())
        snapshot = None
        if path.is_file():
            try:
                if path.stat().st_size > 3 * MAX_RESPONSE_BYTES:
                    raise ValueError("Snapshot too large")
                snapshot = json.loads(path.read_bytes())
                if snapshot["schema_version"] != 1 or snapshot["request_url"] != request.url:
                    raise ValueError("Snapshot request mismatch")
                body = base64.b64decode(snapshot["body_base64"], validate=True)
                fetched_at = _timestamp(snapshot["fetched_at"])
                if snapshot["raw_sha256"] != hashlib.sha256(body).hexdigest() or snapshot["origin"] not in ("live_arxiv_api", "injected_transport"):
                    raise ValueError("Snapshot checksum or origin mismatch")
                if not offline and snapshot["origin"] != self.snapshot_origin:
                    raise ValueError("A fixture snapshot cannot stand in for a live search")
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                raise LiteratureSearchError("invalid_cache", "Invalid search snapshot; no network fallback was attempted") from error
            stale = (served_at - fetched_at).total_seconds() >= self.cache_ttl_seconds or fetched_at > served_at
            if stale and not offline:
                snapshot = None
        elif offline:
            raise LiteratureSearchError("cache_miss", "No matching offline arXiv snapshot")

        cached = snapshot is not None
        if not cached:
            deadline = self.monotonic() + timeout
            with self.gate.slot(deadline):
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise LiteratureSearchError("timeout", "Deadline expired before the arXiv request")
                self.network_requests += 1
                response = self.transport.get(request.url, timeout=remaining, max_bytes=MAX_RESPONSE_BYTES)
                if self.monotonic() > deadline:
                    raise LiteratureSearchError("timeout", "arXiv request exceeded its deadline")
            if response.status != 200:
                code = "rate_limited" if response.status == 429 else "server_error" if response.status >= 500 else "http_error"
                raise LiteratureSearchError(code, f"arXiv returned HTTP {response.status}; no automatic retry was made", status=response.status)
            if response.content_type.split(";", 1)[0].strip().lower() not in ("application/atom+xml", "application/xml", "text/xml"):
                raise LiteratureSearchError("invalid_response", "arXiv did not return XML metadata")
            body = response.body
            fetched_at = _utc(self.now())
            snapshot = {"schema_version": 1, "request_url": request.url,
                        "request": request.to_dict(), "origin": self.snapshot_origin,
                        "fetched_at": fetched_at.isoformat(), "content_type": response.content_type,
                        "raw_sha256": hashlib.sha256(body).hexdigest(),
                        "body_base64": base64.b64encode(body).decode("ascii")}
            stale = False
        observed, total, duplicates = parse_arxiv_feed(body, request, fetched_at)
        if not cached:
            atomic_write_bytes(path, _json_bytes(snapshot))
        start, end = _utc(request.start_at), _utc(request.end_at)
        candidates = tuple(c for c in observed if start <= c.submitted_at < end)
        excluded = tuple(c.arxiv_id for c in observed if not start <= c.submitted_at < end)
        return SearchResult(request, candidates, observed, excluded, duplicates, total, fetched_at,
            _utc(self.now()), cached, stale, snapshot["origin"], str(path), snapshot["raw_sha256"])


class LiteratureSearchSession:
    """Discovery-only task accounting, including cache hits and failed searches.

    All observed base IDs, including date exclusions and later relevance rejects,
    consume candidate capacity. Model/tool/overall deadline budgets belong to the
    future Agent runner; this session enforces search rounds and candidate counts.
    """

    def __init__(self, task: ResearchTask, client: ArxivSearchClient) -> None:
        self.task, self.client = task, client
        self.search_rounds = 0
        self._candidates: dict[str, PaperCandidate] = {}
        self.history: list[dict] = []
        self._lock = threading.Lock()

    @property
    def observed_candidates(self) -> tuple[PaperCandidate, ...]:
        return tuple(self._candidates.values())

    def register_observed(self, candidates: tuple[PaperCandidate, ...]) -> None:
        """Seed trusted prior observations without resetting or spending rounds."""
        with self._lock:
            pending = dict(self._candidates)
            for candidate in candidates:
                if not isinstance(candidate, PaperCandidate):
                    raise ValueError("Expected source-produced candidate metadata")
                parse_arxiv_url(candidate.original_id_url, kind="abs")
                previous = pending.get(candidate.arxiv_id)
                if previous is not None and previous.submitted_at != candidate.submitted_at:
                    raise ValueError("Conflicting candidate submission dates")
                if previous is None or (candidate.version or 0) > (previous.version or 0):
                    pending[candidate.arxiv_id] = candidate
            if len(pending) > self.task.limits.max_candidates:
                raise ValueError("Observed candidates exceed task capacity")
            self._candidates = pending

    def restore_rounds(self, rounds: int) -> None:
        """Trusted coordinator only; accounting may never move backwards."""
        if type(rounds) is not int or not self.search_rounds <= rounds <= self.task.limits.max_search_rounds:
            raise ValueError("Invalid restored search rounds")
        self.search_rounds = rounds

    def search(self, query: str, *, max_results: int | None = None, sort_by: str = "submittedDate",
               offline: bool = False, timeout: float = 30) -> SearchResult:
        with self._lock:
            limits = self.task.limits
            if self.search_rounds >= limits.max_search_rounds:
                raise LiteratureSearchError("search_budget_exhausted", "Task search rounds are exhausted")
            remaining = limits.max_candidates - len(self._candidates)
            if remaining <= 0:
                raise LiteratureSearchError("candidate_budget_exhausted", "Task candidate capacity is exhausted")
            cap = min(remaining, limits.first_search_candidates if self.search_rounds == 0 else remaining)
            count = cap if max_results is None else max_results
            if type(count) is not int or not 1 <= count <= cap:
                raise ValueError(f"This search may request at most {cap} entries")
            request = SearchRequest(query, self.task.start_at, self.task.end_at, count, sort_by)
            _validate_timeout(timeout)
            self.search_rounds += 1
            before = self.client.network_requests
            event = {"round": self.search_rounds, "request": request.to_dict()}
            try:
                result = self.client.search(request, offline=offline, timeout=timeout)
                for candidate in result.observed_candidates:
                    previous = self._candidates.get(candidate.arxiv_id)
                    if previous is not None and previous.submitted_at != candidate.submitted_at:
                        raise LiteratureSearchError("invalid_response", "Conflicting initial submission dates across search rounds")
                for candidate in result.observed_candidates:
                    previous = self._candidates.get(candidate.arxiv_id)
                    if previous is None or (candidate.version or 0) > (previous.version or 0):
                        self._candidates[candidate.arxiv_id] = candidate
            except (LiteratureSearchError, OSError) as error:
                event.update(status="error", error_code=getattr(error, "code", "storage_error"))
                raise
            else:
                event.update(status="success", from_cache=result.from_cache,
                             candidate_ids=[c.arxiv_id for c in result.candidates],
                             excluded_date_ids=list(result.excluded_date_ids), snapshot_path=result.snapshot_path)
                return result
            finally:
                event.update(network_requests=self.client.network_requests - before,
                             cumulative_candidates=len(self._candidates))
                self.history.append(event)
