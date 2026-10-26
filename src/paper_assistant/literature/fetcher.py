"""Registered arXiv PDF acquisition with bounded streaming and versioned cache.

The public task interface accepts candidate IDs, never model-provided URLs or
paths. A downloaded asset has passed transport/header/hash checks, not parsing,
indexing, OCR, or evidence assessment.
"""

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from paper_assistant.literature.search import FileRequestGate, LiteratureSearchError, PaperCandidate, SearchRequest, parse_arxiv_url
from paper_assistant.agent.task import ResearchTask
from paper_assistant.storage import atomic_write_bytes


DEFAULT_PAPER_CACHE = Path("data/paper_cache")
MAX_PDF_BYTES = 20_000_000
MAX_REDIRECTS = 2
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PDF_HEADER = re.compile(br"%PDF-(?:1\.[0-7]|2\.0)")


class PaperFetchError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code, self.status = code, status


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)


def _validate_options(timeout: float, max_bytes: int) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("timeout must be in (0, 120] seconds")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PDF_BYTES:
        raise ValueError(f"max_bytes must be an integer in 1..{MAX_PDF_BYTES}")


def _candidate_url(candidate: PaperCandidate) -> str:
    if not isinstance(candidate, PaperCandidate) or not isinstance(candidate.arxiv_id, str):
        raise ValueError("Expected registered paper metadata")
    if type(candidate.version) is not int or candidate.version < 1:
        raise ValueError("A pinned positive arXiv version is required before fetching")
    url = f"https://arxiv.org/pdf/{candidate.arxiv_id}v{candidate.version}"
    if parse_arxiv_url(url, kind="pdf") != (candidate.arxiv_id, candidate.version):
        raise ValueError("Invalid arXiv identity")
    return url


def _validate_download_url(url: str, candidate: PaperCandidate) -> None:
    try:
        if (urlsplit(url).scheme != "https"
                or parse_arxiv_url(url, kind="pdf") != (candidate.arxiv_id, candidate.version)):
            raise ValueError("Different paper, version or scheme")
    except (TypeError, ValueError) as error:
        raise PaperFetchError("unsafe_redirect", "PDF location is outside the allowed arXiv paper/version") from error


@dataclass(frozen=True)
class CachedPaper:
    arxiv_id: str
    version: int
    title: str
    requested_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    pdf_path: str
    source_sha256: str
    byte_count: int
    content_type: str
    fetched_at: datetime
    origin: str

    def to_dict(self) -> dict:
        return {**asdict(self), "fetched_at": self.fetched_at.isoformat()}


@dataclass(frozen=True)
class FetchResult:
    arxiv_id: str
    version: int
    status: str
    asset: CachedPaper | None
    from_cache: bool
    error_code: str | None
    error_message: str | None
    http_status: int | None
    fetch_attempts: int
    network_requests: int

    def to_dict(self) -> dict:
        return {**asdict(self), "asset": self.asset.to_dict() if self.asset else None}


class DownloadResponse(Protocol):
    status: int
    content_type: str
    content_length: str | None
    content_encoding: str | None
    location: str | None

    def read(self, size: int, *, timeout: float) -> bytes: ...


class PDFTransport(Protocol):
    def open(self, url: str, *, timeout: float): ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _HTTPResponse:
    def __init__(self, response) -> None:
        self.response = response
        self.status = response.code
        self.content_type = response.headers.get("Content-Type", "")
        self.content_length = response.headers.get("Content-Length")
        self.content_encoding = response.headers.get("Content-Encoding")
        self.location = response.headers.get("Location")

    def read(self, size: int, *, timeout: float) -> bytes:
        socket = getattr(getattr(getattr(self.response, "fp", None), "raw", None), "_sock", None)
        if socket is not None:
            socket.settimeout(timeout)
        return getattr(self.response, "read1", self.response.read)(size)


class UrllibPDFTransport:
    """One streaming request per open; redirects are explicitly handled above it."""

    @contextmanager
    def open(self, url: str, *, timeout: float) -> Iterator[DownloadResponse]:
        request = Request(url, headers={"Accept": "application/pdf", "Accept-Encoding": "identity",
                                       "User-Agent": "PaperResearchAssistant/0.1 (personal research prototype)"})
        try:
            try:
                response = build_opener(_NoRedirect()).open(request, timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                yield _HTTPResponse(response)
        except (TimeoutError, URLError, OSError, HTTPException) as error:
            code = "timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "connection_error"
            raise PaperFetchError(code, f"PDF request failed ({code}); no automatic retry was made") from error


class PaperFetcher:
    """Cache/transport adapter; task callers should use PaperFetchSession.fetch."""

    def __init__(self, *, cache_directory: str | Path = DEFAULT_PAPER_CACHE,
                 transport: PDFTransport | None = None, gate: FileRequestGate | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.cache_directory = Path(cache_directory)
        self.transport = transport if transport is not None else UrllibPDFTransport()
        self.origin = "live_arxiv_pdf" if transport is None else "injected_transport"
        # Same lock path as metadata search, so requests are serialized together.
        self.gate = gate if gate is not None else FileRequestGate()
        self.now, self.monotonic = now, monotonic
        self.network_requests = 0

    def cached_paper(self, candidate: PaperCandidate, *, max_bytes: int = MAX_PDF_BYTES) -> CachedPaper | None:
        """Validate a registered version's cache without issuing any request."""
        _validate_options(45, max_bytes)
        return self._cached(candidate, max_bytes=max_bytes, offline=True)

    def _entry_directory(self, candidate: PaperCandidate) -> Path:
        _candidate_url(candidate)
        key = hashlib.sha256(f"arxiv:{candidate.arxiv_id}:v{candidate.version}".encode()).hexdigest()
        root = self.cache_directory.resolve()
        entry = root / key
        if not entry.resolve().is_relative_to(root):
            raise PaperFetchError("invalid_cache", "Paper cache directory escapes the selected root")
        return entry

    @staticmethod
    def _check_file(path: Path, *, max_bytes: int) -> tuple[str, int]:
        size = path.stat().st_size
        if not 8 <= size <= max_bytes:
            raise PaperFetchError("file_size_invalid", "PDF is empty, too small, or exceeds the byte limit")
        digest = hashlib.sha256()
        tail = b""
        count = 0
        with path.open("rb") as file:
            first = file.read(8)
            if _PDF_HEADER.fullmatch(first) is None:
                raise PaperFetchError("not_pdf", "Response does not have a supported PDF header")
            digest.update(first)
            count += len(first)
            tail = first
            while chunk := file.read(65536):
                count += len(chunk)
                if count > max_bytes:
                    raise PaperFetchError("file_size_invalid", "PDF exceeds the byte limit")
                digest.update(chunk)
                tail = (tail + chunk)[-1024:]
        if count != size or b"%%EOF" not in tail:
            raise PaperFetchError("incomplete_pdf", "PDF transfer is incomplete or has no end marker")
        return digest.hexdigest(), count

    def _cached(self, candidate: PaperCandidate, *, max_bytes: int, offline: bool) -> CachedPaper | None:
        directory = self._entry_directory(candidate)
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            if manifest_path.stat().st_size > 65536:
                raise ValueError("Manifest too large")
            metadata = json.loads(manifest_path.read_bytes())
            if (metadata["schema_version"] != 1 or metadata["source"] != "arxiv"
                    or metadata["arxiv_id"] != candidate.arxiv_id or metadata["version"] != candidate.version
                    or type(metadata["version"]) is not int or type(metadata["byte_count"]) is not int
                    or not isinstance(metadata["title"], str)
                    or _SHA256.fullmatch(metadata["source_sha256"]) is None
                    or metadata["origin"] not in ("live_arxiv_pdf", "injected_transport")
                    or not offline and metadata["origin"] != self.origin):
                raise ValueError("Invalid cache identity or provenance")
            pdf = directory / f"{metadata['source_sha256']}.pdf"
            if not pdf.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Cache file escapes its entry")
            sha256, byte_count = self._check_file(pdf, max_bytes=max_bytes)
            if sha256 != metadata["source_sha256"] or byte_count != metadata["byte_count"]:
                raise ValueError("PDF checksum or size mismatch")
            chain = tuple(metadata["redirect_chain"])
            if (not 1 <= len(chain) <= MAX_REDIRECTS + 1 or chain[0] != _candidate_url(candidate)
                    or chain[-1] != metadata["final_url"] or metadata["requested_url"] != chain[0]
                    or metadata["content_type"] != "application/pdf"):
                raise ValueError("Invalid cached transfer metadata")
            for url in chain:
                _validate_download_url(url, candidate)
            return CachedPaper(candidate.arxiv_id, candidate.version, metadata["title"], chain[0], chain[-1], chain,
                               str(pdf), sha256, byte_count, "application/pdf",
                               _utc(datetime.fromisoformat(metadata["fetched_at"])), metadata["origin"])
        except (ValueError, TypeError, KeyError, OSError, PaperFetchError) as error:
            raise PaperFetchError("invalid_cache", "Cached PDF or metadata failed validation; no download fallback was attempted") from error

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise PaperFetchError("timeout", "PDF acquisition exceeded the request deadline")
        return remaining

    def _download(self, candidate: PaperCandidate, file, *, deadline: float, max_bytes: int,
                  reserve_attempt: Callable[[], None]) -> tuple[str, tuple[str, ...]]:
        url = _candidate_url(candidate)
        chain: list[str] = []
        while True:
            _validate_download_url(url, candidate)
            if url in chain or len(chain) >= MAX_REDIRECTS + 1:
                raise PaperFetchError("redirect_limit", "PDF redirect loop or redirect limit reached")
            chain.append(url)
            reserve_attempt()
            try:
                with self.gate.slot(deadline):
                    remaining = self._remaining(deadline)
                    self.network_requests += 1
                    with self.transport.open(url, timeout=remaining) as response:
                        self._remaining(deadline)
                        if response.status in (301, 302, 303, 307, 308):
                            if not response.location:
                                raise PaperFetchError("unsafe_redirect", "PDF redirect has no location")
                            url = urljoin(url, response.location)
                            _validate_download_url(url, candidate)
                            continue
                        if response.status != 200:
                            code = ("unavailable" if response.status in (404, 410) else "rate_limited" if response.status == 429
                                    else "server_error" if response.status >= 500 else "http_error")
                            raise PaperFetchError(code, f"PDF endpoint returned HTTP {response.status}; no automatic retry was made", status=response.status)
                        if response.content_type.split(";", 1)[0].strip().lower() != "application/pdf":
                            raise PaperFetchError("not_pdf", "PDF endpoint did not return application/pdf")
                        if response.content_encoding and response.content_encoding.lower() != "identity":
                            raise PaperFetchError("unsupported_encoding", "PDF endpoint returned an unexpected content encoding")
                        length = None
                        if response.content_length is not None:
                            if not re.fullmatch(r"[0-9]+", response.content_length.strip()):
                                raise PaperFetchError("invalid_response", "Invalid PDF Content-Length")
                            try:
                                length = int(response.content_length)
                            except ValueError as error:
                                raise PaperFetchError("invalid_response", "Invalid PDF Content-Length") from error
                            if not 8 <= length <= max_bytes:
                                raise PaperFetchError("file_size_invalid", "Declared PDF size exceeds the permitted bounds")
                        received = 0
                        while True:
                            chunk = response.read(min(65536, max_bytes + 1 - received), timeout=self._remaining(deadline))
                            self._remaining(deadline)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_bytes:
                                raise PaperFetchError("file_size_invalid", "Streamed PDF exceeds the byte limit")
                            try:
                                file.write(chunk)
                            except OSError as error:
                                raise PaperFetchError("storage_error", "Could not write the temporary PDF") from error
                        if length is not None and received != length:
                            raise PaperFetchError("incomplete_pdf", "PDF Content-Length does not match the completed transfer")
                        return url, tuple(chain)
            except LiteratureSearchError as error:
                raise PaperFetchError(error.code, "PDF request could not acquire the shared arXiv connection") from error

    def _fetch_registered(self, candidate: PaperCandidate, *, reserve_attempt: Callable[[], None],
                          offline: bool, timeout: float, max_bytes: int) -> tuple[CachedPaper, bool]:
        _validate_options(timeout, max_bytes)
        cached = self._cached(candidate, max_bytes=max_bytes, offline=offline)
        if cached is not None:
            return cached, True
        if offline:
            raise PaperFetchError("cache_miss", "No validated PDF cache for this registered paper/version")
        directory = self._entry_directory(candidate)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            deadline = self.monotonic() + timeout
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", suffix=".part", delete=False) as file:
                temporary = Path(file.name)
                final_url, chain = self._download(candidate, file, deadline=deadline, max_bytes=max_bytes,
                                                  reserve_attempt=reserve_attempt)
                file.flush()
                os.fsync(file.fileno())
            sha256, byte_count = self._check_file(temporary, max_bytes=max_bytes)
            self._remaining(deadline)
            destination = directory / f"{sha256}.pdf"
            temporary.replace(destination)
            asset = CachedPaper(candidate.arxiv_id, candidate.version, candidate.title, _candidate_url(candidate),
                                final_url, chain, str(destination), sha256, byte_count, "application/pdf", _utc(self.now()), self.origin)
            # Immutable content file first, atomic manifest last. An interrupted
            # manifest write can leave an orphan, never a valid partial cache.
            metadata = {**asset.to_dict(), "schema_version": 1, "source": "arxiv"}
            metadata.pop("pdf_path")
            atomic_write_bytes(directory / "manifest.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))
            return asset, False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class PaperFetchSession:
    """Task-local registry and acquisition budget, before any Agent integration."""

    def __init__(self, task: ResearchTask, candidates: Sequence[PaperCandidate], fetcher: PaperFetcher) -> None:
        self.task, self.fetcher = task, fetcher
        self._candidates: dict[str, PaperCandidate] = {}
        self._assets: dict[str, CachedPaper] = {}
        self._successful_ids: set[str] = set()
        self.fetch_attempts = 0
        self.history: list[dict] = []
        self._lock = threading.RLock()
        self.register_candidates(candidates)

    def register_candidates(self, candidates: Sequence[PaperCandidate]) -> None:
        with self._lock:
            pending = dict(self._candidates)
            start, end = self.task.utc_window
            for candidate in candidates:
                _candidate_url(candidate)
                if (not isinstance(candidate.title, str) or not candidate.title.strip()
                        or _utc(candidate.updated_at) < _utc(candidate.submitted_at)):
                    raise ValueError("Invalid candidate title or submission/revision dates")
                if not start <= _utc(candidate.submitted_at) < end:
                    raise ValueError("Candidate initial submission is outside the task window")
                previous = pending.get(candidate.arxiv_id)
                if previous is not None and previous.version != candidate.version:
                    raise ValueError("A paper version is already pinned in this task")
                pending[candidate.arxiv_id] = candidate
            if len(pending) > self.task.limits.max_candidates:
                raise ValueError("Registered candidates exceed the task limit")
            self._candidates = pending

    @property
    def downloaded_assets(self) -> tuple[CachedPaper, ...]:
        return tuple(self._assets.values())

    @property
    def downloaded_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._successful_ids))

    def restore_downloaded_ids(self, identifiers: Sequence[str]) -> None:
        """Reserve task paper capacity from trusted persisted acquisition state."""
        with self._lock:
            pending = self._successful_ids | set(identifiers)
            if not pending <= self._candidates.keys() or len(pending) > min(self.task.target_papers, self.task.limits.max_fulltext_papers):
                raise ValueError("Invalid previously downloaded paper set")
            self._successful_ids = pending

    def restore_attempts(self, attempts: int) -> None:
        """Trusted coordinator only; failed and redirected attempts survive resume."""
        if type(attempts) is not int or not self.fetch_attempts <= attempts <= self.task.limits.max_fetch_attempts:
            raise ValueError("Invalid restored acquisition attempts")
        self.fetch_attempts = attempts

    def _reserve_attempt(self) -> None:
        if self.fetch_attempts >= self.task.limits.max_fetch_attempts:
            raise PaperFetchError("fetch_budget_exhausted", "Task PDF fetch attempts are exhausted")
        self.fetch_attempts += 1

    def fetch(self, arxiv_id: str, *, version: int | None = None, offline: bool = False, timeout: float = 45) -> FetchResult:
        with self._lock:
            _validate_options(timeout, self.task.limits.max_pdf_bytes)
            if not isinstance(arxiv_id, str) or arxiv_id not in self._candidates:
                raise ValueError("Unknown candidate ID; register search results before fetching")
            candidate = self._candidates[arxiv_id]
            if version is not None and (type(version) is not int or version != candidate.version):
                raise ValueError("Requested version is not the registered version")
            before_attempts, before_network = self.fetch_attempts, self.fetcher.network_requests
            asset = None
            cached = False
            error = None
            try:
                if arxiv_id not in self._successful_ids and len(self._successful_ids) >= min(self.task.target_papers, self.task.limits.max_fulltext_papers):
                    raise PaperFetchError("paper_budget_exhausted", "Task successful-PDF limit is exhausted")
                asset, cached = self.fetcher._fetch_registered(candidate, reserve_attempt=self._reserve_attempt,
                    offline=offline, timeout=timeout, max_bytes=self.task.limits.max_pdf_bytes)
                self._assets[arxiv_id] = asset
                self._successful_ids.add(arxiv_id)
            except PaperFetchError as caught:
                error = caught
            except OSError:
                error = PaperFetchError("storage_error", "PDF cache could not be read or written")
            status = ("downloaded" if asset else "unavailable" if error.code == "unavailable"
                      else "budget_exhausted" if error.code.endswith("budget_exhausted") else "download_failed")
            result = FetchResult(arxiv_id, candidate.version, status, asset, cached,
                                 error.code if error else None, str(error) if error else None,
                                 error.status if error else None, self.fetch_attempts - before_attempts,
                                 self.fetcher.network_requests - before_network)
            self.history.append(result.to_dict())
            return result


def load_search_candidates(path: str | Path, *, allow_injected: bool = False) -> tuple[ResearchTask, tuple[PaperCandidate, ...]]:
    """Read a caller-selected search report; this path is not a model tool argument."""
    source = Path(path)
    if source.stat().st_size > 6_000_000:
        raise ValueError("Search report exceeds the input limit")
    try:
        report = json.loads(source.read_bytes())
        if report["status"] != "success" or report["snapshot_origin"] not in (
            ("live_arxiv_api", "injected_transport") if allow_injected else ("live_arxiv_api",)
        ):
            raise ValueError("Expected a full successful search report from the live API")
        raw = report["request"]
        request = SearchRequest(raw["query"], datetime.fromisoformat(raw["start_at"]),
                                datetime.fromisoformat(raw["end_at"]), raw["max_results"], raw["sort_by"])
        task = ResearchTask(request.query, (request.query,), request.start_at, request.end_at)
        rows = report["candidates"]
        if not isinstance(rows, list) or len(rows) > min(task.limits.max_candidates, request.max_results):
            raise ValueError("Invalid candidate list")
        candidates = []
        for row in rows:
            data = dict(row)
            for field in ("submitted_at", "updated_at", "fetched_at"):
                data[field] = _utc(datetime.fromisoformat(data[field]))
            for field in ("authors", "categories"):
                if not isinstance(data[field], list) or not all(isinstance(item, str) for item in data[field]):
                    raise ValueError("Invalid candidate metadata")
                data[field] = tuple(data[field])
            candidate = PaperCandidate(**data)
            _candidate_url(candidate)
            if not isinstance(candidate.title, str) or not candidate.title.strip():
                raise ValueError("Candidate title is required")
            candidates.append(candidate)
        return task, tuple(candidates)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ValueError("Invalid search report; use the full JSON saved by scripts/search_literature.py with pinned versions") from error
