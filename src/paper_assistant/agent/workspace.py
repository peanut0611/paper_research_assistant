"""Isolated, resumable fulltext knowledge base for one explicit research task."""

import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from paper_assistant.documents.chunking import chunk_pages_by_tokens
from paper_assistant.documents.ingestion import IngestedDocument
from paper_assistant.documents.store import DocumentStore, load_document_store, save_document_store
from paper_assistant.retrieval.embedding import DEFAULT_MODEL_NAME, DenseIndex, SentenceTransformerEmbedder, build_dense_index, load_dense_index, save_dense_index, search_dense_index
from paper_assistant.retrieval.lexical import BM25_STATISTICS_SCOPE, build_bm25_index, search_bm25_index, search_hybrid_index
from paper_assistant.literature.search import PaperCandidate
from paper_assistant.literature.fetcher import FetchResult, PaperFetchError, PaperFetcher, PaperFetchSession
from paper_assistant.documents.pdf_parser import PageDocument
from paper_assistant.agent.task import ResearchTask
from paper_assistant.storage import atomic_write_bytes


DEFAULT_RESEARCH_ROOT = Path("data/research_runs")


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str, *, warnings: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.warnings = tuple(warnings)


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while piece := file.read(65536):
            digest.update(piece)
    return digest.hexdigest()


@dataclass(frozen=True)
class WorkspaceConfig:
    model_name: str = DEFAULT_MODEL_NAME
    max_tokens: int = 384
    overlap_tokens: int = 64
    max_pdf_pages: int = 100
    max_text_chars: int = 1_000_000
    parse_timeout_seconds: float = 45

    def __post_init__(self) -> None:
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name cannot be blank")
        for name, cap in (("max_tokens", 512), ("max_pdf_pages", 100), ("max_text_chars", 1_000_000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in 1..{cap}")
        if type(self.overlap_tokens) is not int or not 0 <= self.overlap_tokens < self.max_tokens:
            raise ValueError("Invalid token overlap")
        timeout = self.parse_timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("Parse timeout must be in (0, 120] seconds")


@dataclass(frozen=True)
class ExtractedPDF:
    pages: tuple[PageDocument, ...]
    warnings: tuple[str, ...] = ()


def parse_pdf_isolated(path: Path, config: WorkspaceConfig) -> ExtractedPDF:
    """Kill and reap the extraction process when its wall-clock limit expires."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH")]))}
    try:
        result = subprocess.run([
            sys.executable, "-m", "paper_assistant.documents.pdf_worker", str(path),
            str(config.max_pdf_pages), str(config.max_text_chars),
        ], capture_output=True, timeout=config.parse_timeout_seconds, env=env)
    except subprocess.TimeoutExpired as error:
        raise WorkspaceError("parse_timeout", "PDF extraction exceeded its process deadline") from error
    if result.returncode != 0:
        raise WorkspaceError("parse_failed", "PDF extraction process failed")
    try:
        output = json.loads(result.stdout)
        if output["status"] != "success":
            raise WorkspaceError(output["code"], "PDF extraction failed or exceeded its configured limits", warnings=output.get("warnings", ()))
        return ExtractedPDF(tuple(PageDocument(**page) for page in output["pages"]), tuple(output["warnings"]))
    except (ValueError, KeyError, TypeError) as error:
        raise WorkspaceError("parse_failed", "Invalid extraction process result") from error


def _processing_identity() -> dict:
    root = Path(__file__).resolve().parents[1]
    return {"pypdf": importlib.metadata.version("pypdf"), "numpy": np.__version__,
            "code_sha256": {name: _hash_file(root / name) for name in (
                "documents/pdf_parser.py", "documents/pdf_worker.py", "documents/text_cleaner.py",
                "documents/chunking.py", "retrieval/embedding.py", "agent/workspace.py")}}


class ResearchWorkspace:
    """One writer per task. Immutable artifacts are published before the manifest.

    A fixed task/config is never silently repurposed. Use a new task ID after a
    condition, parser, chunking or model configuration change. Failed ingestion
    never replaces a previously committed usable snapshot.
    """

    def __init__(self, task_id: str, task: ResearchTask, candidates: Sequence[PaperCandidate], *,
                 root: str | Path = DEFAULT_RESEARCH_ROOT, fetcher: PaperFetcher | None = None,
                 config: WorkspaceConfig | None = None, embedder_factory: Callable[[str], Any] | None = None,
                 parser: Callable[[Path, WorkspaceConfig], ExtractedPDF] = parse_pdf_isolated) -> None:
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", task_id):
            raise ValueError("task_id must contain 1..64 letters, digits, underscores or hyphens")
        if task_id.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError("Reserved task directory name")
        root = Path(root).resolve()
        self.directory = root / task_id
        if not self.directory.resolve().is_relative_to(root):
            raise ValueError("Research task directory must stay within its workspace root")
        self.task, self.task_id = task, task_id
        self.config = config or WorkspaceConfig()
        self.fetcher = fetcher if fetcher is not None else PaperFetcher()
        # Reuse registration validation without fetching or spending any budget.
        PaperFetchSession(task, candidates, self.fetcher)
        self._candidates = {candidate.arxiv_id: candidate for candidate in candidates}
        self._embedder_factory = embedder_factory or SentenceTransformerEmbedder
        self._embedder = None
        self._identity = None
        self._parser = parser
        self._lock = threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.directory / "workspace.json"
        contract = {"schema_version": 1, "task_id": task_id, "task": task.to_dict(),
                    "config": asdict(self.config), "processing": _processing_identity()}
        if self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_bytes())
            if (not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), dict)
                    or not isinstance(manifest.get("events"), list)
                    or "active_index" not in manifest or "embedding_identity" not in manifest):
                raise ValueError("Invalid research workspace manifest")
            if any(_bytes(manifest.get(key)) != _bytes(value) for key, value in contract.items()):
                raise ValueError("Task/config/processing version changed; use a new task_id")
            if set(manifest["documents"]) - set(self._candidates):
                raise ValueError("Resume must include all previously registered candidates")
            self._manifest = manifest
        else:
            self._manifest = {**contract, "embedding_identity": None, "documents": {}, "active_index": None, "events": []}
        documents = dict(self._manifest["documents"])
        for candidate in candidates:
            document_id = f"arxiv--{candidate.arxiv_id.replace('/', '--')}--v{candidate.version}"
            if candidate.arxiv_id in documents:
                record = documents[candidate.arxiv_id]
                required = {"document_id", "status", "arxiv_id", "version", "source_sha256", "source", "page_count", "warnings", "artifact", "build_id"}
                if not isinstance(record, dict) or not required <= record.keys():
                    raise ValueError("Invalid research document record")
                if record["document_id"] != document_id or record["version"] != candidate.version or record["arxiv_id"] != candidate.arxiv_id:
                    raise ValueError("Candidate version changed; use a new task_id")
                continue
            documents[candidate.arxiv_id] = {
                "arxiv_id": candidate.arxiv_id, "version": candidate.version, "document_id": document_id,
                "title": candidate.title, "abstract": candidate.abstract, "authors": list(candidate.authors),
                "abstract_url": f"https://arxiv.org/abs/{candidate.arxiv_id}v{candidate.version}",
                "pdf_url": f"https://arxiv.org/pdf/{candidate.arxiv_id}v{candidate.version}",
                "submitted_at": candidate.submitted_at.isoformat(), "updated_at": candidate.updated_at.isoformat(),
                "status": "abstract_only" if candidate.abstract.strip() else "metadata_only",
                "source_sha256": None, "source": None, "page_count": None, "blank_pages": [],
                "warnings": [], "artifact": None, "build_id": None, "last_error": None,
            }
        self._commit(documents=documents)
        if self._manifest["active_index"] is not None:
            self._load_active()

    def _commit(self, **changes) -> None:
        manifest = {**self._manifest, **changes}
        content = _bytes(manifest)
        atomic_write_bytes(self.manifest_path, content)
        # Detach from in-flight row dictionaries. A later row.update must not
        # make an uncommitted document appear ready in the active manifest.
        self._manifest = json.loads(content)

    def _path(self, relative: str) -> Path:
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise WorkspaceError("invalid_workspace", "Invalid task artifact path")
        path = self.directory / relative
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise WorkspaceError("invalid_workspace", "Task artifact path escapes its workspace")
        return path

    def _get_embedder(self):
        if self._embedder is None:
            embedder = self._embedder_factory(self.config.model_name)
            if embedder.model_name != self.config.model_name or self.config.max_tokens > embedder.max_sequence_length:
                raise WorkspaceError("embedding_config_mismatch", "Embedding model or token limit does not match the workspace")
            identity = embedder.cache_identity
            if not isinstance(identity, dict) or not identity.get("revision"):
                raise WorkspaceError("embedding_config_mismatch", "Embedder must provide a stable checkpoint/tokenizer identity")
            if self._manifest["embedding_identity"] is not None and _bytes(identity) != _bytes(self._manifest["embedding_identity"]):
                raise WorkspaceError("embedding_config_mismatch", "Resolved model/tokenizer changed; use a new task_id")
            self._commit(embedding_identity=identity)
            self._embedder, self._identity = embedder, identity
        return self._embedder

    def _load_active(self) -> tuple[DenseIndex, DocumentStore] | None:
        active = self._manifest["active_index"]
        if active is None:
            return None
        try:
            path = self._path(active["path"])
            index, store = load_dense_index(path), load_document_store(path)
            rows = [row for row in self._manifest["documents"].values() if row["status"] == "fulltext_ready"]
            expected = {row["document_id"]: row for row in rows}
            if (store.build_id != active["build_id"] or set(expected) != {doc.document_id for doc in store.documents}
                    or index.model_name != self.config.model_name):
                raise ValueError("Task document set or build mismatch")
            for doc in store.documents:
                row = expected[doc.document_id]
                if (doc.source_sha256 != row["source_sha256"] or doc.source != row["source"]
                        or doc.page_count != row["page_count"] or list(doc.parse_warnings) != row["warnings"]):
                    raise ValueError("Task source version mismatch")
            store.verify_sources(self.directory)
            return index, store
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise WorkspaceError("invalid_workspace", "Task index/pages/sources failed validation") from error

    def list_documents(self) -> list[dict]:
        return json.loads(_bytes(list(self._manifest["documents"].values())))

    @property
    def registered_candidates(self) -> tuple[PaperCandidate, ...]:
        return tuple(self._candidates.values())

    def register_candidates(self, candidates: Sequence[PaperCandidate]) -> None:
        """Trusted coordinator entry point, deliberately absent from model tools."""
        with self._lock:
            pending = {**self._candidates}
            for candidate in candidates:
                previous = pending.get(candidate.arxiv_id)
                if previous is not None and previous != candidate:
                    if previous.version != candidate.version or previous.submitted_at != candidate.submitted_at:
                        raise ValueError("Task candidate identity is already pinned")
                    continue
                pending[candidate.arxiv_id] = candidate
            PaperFetchSession(self.task, tuple(pending.values()), self.fetcher)
            documents = dict(self._manifest["documents"])
            for identifier, candidate in pending.items():
                if identifier in documents:
                    continue
                documents[identifier] = {
                    "arxiv_id": identifier, "version": candidate.version,
                    "document_id": f"arxiv--{identifier.replace('/', '--')}--v{candidate.version}",
                    "title": candidate.title, "abstract": candidate.abstract, "authors": list(candidate.authors),
                    "abstract_url": f"https://arxiv.org/abs/{identifier}v{candidate.version}",
                    "pdf_url": f"https://arxiv.org/pdf/{identifier}v{candidate.version}",
                    "submitted_at": candidate.submitted_at.isoformat(), "updated_at": candidate.updated_at.isoformat(),
                    "status": "abstract_only" if candidate.abstract.strip() else "metadata_only",
                    "source_sha256": None, "source": None, "page_count": None, "blank_pages": [],
                    "warnings": [], "artifact": None, "build_id": None, "last_error": None,
                }
            self._commit(documents=documents)
            self._candidates = pending

    def record_fetch_result(self, result: FetchResult) -> None:
        """Record the task fetch session's observation without claiming parsing."""
        with self._lock:
            if result.arxiv_id not in self._candidates or result.version != self._candidates[result.arxiv_id].version:
                raise ValueError("Fetch result is outside the registered task")
            row = dict(self._manifest["documents"][result.arxiv_id])
            if (result.asset is not None and row["source_sha256"] is not None
                    and row["source_sha256"] != result.asset.source_sha256):
                raise WorkspaceError("source_mismatch", "Pinned task PDF content changed; existing task state was preserved")
            row.update(fetch_status=result.status, fetch_error=result.error_code)
            if result.status == "downloaded" and result.asset is not None:
                row.update(source_sha256=result.asset.source_sha256, origin=result.asset.origin)
            if row["status"] in ("metadata_only", "abstract_only", "downloaded", "unavailable", "download_failed", "budget_exhausted"):
                row["status"] = result.status
            self._commit(documents={**self._manifest["documents"], result.arxiv_id: row})

    @property
    def index_directory(self) -> Path | None:
        active = self._manifest["active_index"]
        return self._path(active["path"]) if active else None

    def _copy_source(self, source: Path, relative: str, expected_sha256: str) -> Path:
        destination = self._path(relative)
        if destination.exists():
            if _hash_file(destination) != expected_sha256:
                raise WorkspaceError("source_mismatch", "Frozen task PDF differs from the registered version")
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as file:
                temporary = Path(file.name)
                with source.open("rb") as incoming:
                    shutil.copyfileobj(incoming, file, length=65536)
            if _hash_file(temporary) != expected_sha256:
                raise WorkspaceError("source_mismatch", "PDF changed while copying it into the task")
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination

    def _publish_index(self, index: DenseIndex, pages: Sequence[PageDocument], sources: Sequence[IngestedDocument], parent: str) -> dict:
        store = DocumentStore(index, pages, sources)
        relative = f"{parent}/{store.build_id}"
        destination = self._path(relative)
        if destination.exists():
            if load_document_store(destination).build_id != store.build_id:
                raise WorkspaceError("invalid_workspace", "Existing artifact differs from its content identity")
            return {"path": relative, "build_id": store.build_id}
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=destination.parent))
        try:
            save_dense_index(index, stage)
            save_document_store(store, stage)
            load_document_store(stage)
            stage.replace(destination)
        finally:
            if stage.exists():
                # Only remove this operation's own staging directory, never an
                # artifact path read from a manifest or supplied by a model.
                if not stage.name.startswith(".stage-") or stage.is_symlink() or not stage.resolve().is_relative_to(self.directory.resolve()):
                    raise WorkspaceError("invalid_workspace", "Unsafe staging cleanup path")
                shutil.rmtree(stage)
        return {"path": relative, "build_id": store.build_id}

    def _combine(self, documents: dict) -> dict:
        indexes, pages, sources = [], [], []
        for row in sorted(documents.values(), key=lambda row: row["document_id"]):
            if row["status"] != "fulltext_ready":
                continue
            path = self._path(row["artifact"])
            index, store = load_dense_index(path), load_document_store(path)
            if index.model_name != self.config.model_name or store.build_id != row["build_id"]:
                raise WorkspaceError("invalid_workspace", "Document artifact model or build differs from its task record")
            store.verify_sources(self.directory)
            indexes.append(index)
            pages.extend(store.export_pages())
            sources.append(IngestedDocument(row["document_id"], row["document_id"] + ".pdf", row["source"],
                                            row["source_sha256"], row["page_count"], tuple(row["warnings"])))
        combined = DenseIndex(tuple(chunk for index in indexes for chunk in index.chunks),
                              np.vstack([index.embeddings for index in indexes]), self.config.model_name)
        return self._publish_index(combined, pages, sources, "indexes")

    def ingest_paper(self, arxiv_id: str) -> dict:
        with self._lock:
            if not isinstance(arxiv_id, str) or arxiv_id not in self._candidates:
                raise ValueError("Unknown candidate ID")
            row = dict(self._manifest["documents"][arxiv_id])
            reused = False
            try:
                ready = [r for r in self._manifest["documents"].values() if r["status"] == "fulltext_ready"]
                if row["status"] != "fulltext_ready" and len(ready) >= min(self.task.target_papers, self.task.limits.max_fulltext_papers):
                    raise WorkspaceError("paper_budget_exhausted", "Task indexed-paper limit is exhausted")
                asset = self.fetcher.cached_paper(self._candidates[arxiv_id], max_bytes=self.task.limits.max_pdf_bytes)
                if asset is None:
                    raise WorkspaceError("cache_miss", "Paper must be acquired before ingestion; no network request was made")
                if row["status"] == "fulltext_ready":
                    self._get_embedder()
                    if row["source_sha256"] != asset.source_sha256:
                        raise WorkspaceError("source_mismatch", "Pinned PDF content changed; use a new task_id")
                    self._load_active()
                    reused = True
                else:
                    row.update(status="downloaded", source_sha256=asset.source_sha256, origin=asset.origin,
                               source=f"sources/{row['document_id']}.pdf", last_error=None)
                    self._commit(documents={**self._manifest["documents"], arxiv_id: row})
                    path = self._copy_source(Path(asset.pdf_path), row["source"], asset.source_sha256)
                    extracted = self._parser(path, self.config)
                    pages = tuple(replace(page, document_id=row["document_id"], filename=row["document_id"] + ".pdf") for page in extracted.pages)
                    row.update(page_count=len(pages), warnings=list(extracted.warnings),
                               blank_pages=[page.page_number for page in pages if not page.text.strip()])
                    if len(pages) > self.config.max_pdf_pages or sum(len(page.text) for page in pages) > self.config.max_text_chars:
                        raise WorkspaceError("parse_limit_exceeded", "Extraction exceeded task page/text limits")
                    if _hash_file(path) != asset.source_sha256:
                        raise WorkspaceError("source_mismatch", "PDF changed during extraction")
                    if not any(page.text.strip() for page in pages):
                        raise WorkspaceError("no_text_layer", "No usable text extracted; OCR is not implemented")
                    try:
                        embedder = self._get_embedder()
                        chunks = chunk_pages_by_tokens(pages, embedder.tokenizer, max_tokens=self.config.max_tokens,
                                                       overlap_tokens=self.config.overlap_tokens, document_prefix=embedder.document_prefix)
                        index = build_dense_index(chunks, embedder)
                    except (RuntimeError, ValueError) as error:
                        raise WorkspaceError("embedding_failed", "Task chunking or embedding failed") from error
                    source = IngestedDocument(row["document_id"], row["document_id"] + ".pdf", row["source"],
                                               asset.source_sha256, len(pages), extracted.warnings)
                    artifact = self._publish_index(index, pages, [source], "artifacts")
                    row.update(status="fulltext_ready", artifact=artifact["path"], build_id=artifact["build_id"], last_error=None)
                    documents = {**self._manifest["documents"], arxiv_id: row}
                    active = self._combine(documents)
                    self._commit(documents=documents, active_index=active)
                event = {"arxiv_id": arxiv_id, "status": "fulltext_ready", "reused": reused,
                         "document_id": row["document_id"], "error_code": None}
            except (WorkspaceError, PaperFetchError, ValueError, OSError) as error:
                code = getattr(error, "code", "ingestion_failed")
                previous = self._manifest["documents"][arxiv_id]
                failed = {**row, "last_error": code}
                if getattr(error, "warnings", ()):
                    failed["warnings"] = list(error.warnings)
                if previous["status"] == "fulltext_ready":
                    failed = {**previous, "last_error": code}
                elif code in ("no_text_layer", "page_limit_exceeded", "text_limit_exceeded", "parse_limit_exceeded"):
                    failed["status"] = "unusable"
                elif code not in ("cache_miss", "paper_budget_exhausted"):
                    failed["status"] = "parse_failed"
                else:
                    failed["status"] = previous["status"]
                self._commit(documents={**self._manifest["documents"], arxiv_id: failed})
                event = {"arxiv_id": arxiv_id, "status": "ingestion_failed", "reused": False,
                         "document_id": row["document_id"], "error_code": code}
            self._commit(events=[*self._manifest["events"], event])
            return event

    def _source(self, document_id: str) -> dict:
        for row in self._manifest["documents"].values():
            if row["document_id"] == document_id and row["status"] == "fulltext_ready":
                return {key: row[key] for key in ("arxiv_id", "version", "document_id", "title", "abstract_url", "pdf_url", "source_sha256")}
        raise ValueError("Unknown fulltext document ID")

    def read_pages(self, document_id: str, start_page: int, end_page: int | None = None, **kwargs) -> dict:
        with self._lock:
            source = self._source(document_id)
            active = self._load_active()
            if active is None:
                raise ValueError("Task has no indexed fulltext")
            return {**active[1].read_pages(document_id, start_page, end_page, **kwargs).to_dict(),
                    "source": source, "evidence_type": "fulltext"}

    def search_documents(self, query: str, *, method: str = "hybrid", top_k: int = 3,
                         document_ids: Sequence[str] | None = None) -> dict:
        with self._lock:
            if method not in ("dense", "bm25", "hybrid") or type(top_k) is not int or not 1 <= top_k <= 10:
                raise ValueError("Invalid retrieval method or top_k")
            if not isinstance(query, str) or not query.strip() or len(query) > 4000:
                raise ValueError("query must contain 1..4000 characters")
            if document_ids is not None:
                if isinstance(document_ids, (str, bytes)) or not isinstance(document_ids, Sequence):
                    raise ValueError("document_ids must be a sequence")
                for identifier in document_ids:
                    self._source(identifier)
            active = self._load_active()
            if active is None or document_ids == [] or document_ids == ():
                return {"build_id": None if active is None else active[1].build_id, "results": [], "method": method}
            index, store = active
            lexical = build_bm25_index(index.chunks)
            if method == "bm25":
                results = search_bm25_index(lexical, query, top_k=top_k, document_ids=document_ids)
            else:
                embedder = self._get_embedder()
                results = (search_dense_index(index, query, embedder, top_k=top_k, document_ids=document_ids) if method == "dense"
                           else search_hybrid_index(index, lexical, query, embedder, top_k=top_k, document_ids=document_ids))
            return {"build_id": store.build_id, "method": method, "bm25_statistics_scope": BM25_STATISTICS_SCOPE,
                    "results": [{**asdict(result), "source": self._source(result.chunk.document_id), "evidence_type": "fulltext"} for result in results]}
