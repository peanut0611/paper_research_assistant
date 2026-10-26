"""Version-bound document catalog and bounded reads of cleaned source pages.

All paths are chosen by the caller when opening a store. Document tools accept
registered IDs only; they never interpret an ID as a filesystem path.
"""

import hashlib
import json
import re
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np

from paper_assistant.documents.ingestion import IngestedDocument
from paper_assistant.retrieval.embedding import DenseIndex, load_dense_index
from paper_assistant.documents.pdf_parser import PageDocument


MAX_PAGES_PER_READ = 5
MAX_READ_CHARS = 12000
STORE_FILENAME = "document_store.json"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _index_fingerprint(index: DenseIndex) -> str:
    digest = hashlib.sha256(_json_bytes({
        "model_name": index.model_name, "dimension": index.dimension,
        "chunks": [asdict(chunk) for chunk in index.chunks],
    }))
    digest.update(np.asarray(index.embeddings, dtype="<f4").tobytes(order="C"))
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    filename: str
    source: str
    source_sha256: str
    version: str
    page_count: int
    fulltext_status: str
    parse_warnings: tuple[str, ...]


@dataclass(frozen=True)
class PageExcerpt:
    page_number: int
    text: str
    char_start: int
    char_end: int
    original_char_count: int
    truncated: bool


@dataclass(frozen=True)
class PageRead:
    document_id: str
    filename: str
    build_id: str
    document_version: str
    requested_start_page: int
    requested_end_page: int
    pages: tuple[PageExcerpt, ...]
    truncated: bool
    next_page: int | None
    next_char_start: int | None
    parse_warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


class DocumentStore:
    """Page snapshot checked against the exact chunks and vectors."""

    def __init__(self, index: DenseIndex, pages: Sequence[PageDocument], sources: Sequence[IngestedDocument]) -> None:
        if (index.embeddings.ndim != 2 or index.embeddings.shape[0] != len(index.chunks)
                or not np.isfinite(index.embeddings).all()):
            raise ValueError("Invalid embedding matrix or chunk alignment")
        source_map: dict[str, IngestedDocument] = {}
        identifiers: set[str] = set()
        for source in sources:
            if not source.document_id or source.document_id.casefold() in identifiers:
                raise ValueError("Duplicate or empty document ID")
            identifiers.add(source.document_id.casefold())
            if not re.fullmatch(r"[0-9a-f]{64}", source.source_sha256):
                raise ValueError("Source SHA-256 must be lowercase hexadecimal")
            if type(source.page_count) is not int or source.page_count < 0:
                raise ValueError("Invalid source page count")
            if not source.filename or not source.source or any(not isinstance(w, str) for w in source.parse_warnings):
                raise ValueError("Invalid source metadata")
            source_map[source.document_id] = source
        if not source_map:
            raise ValueError("Cannot create a document store without sources")

        page_map: dict[tuple[str, int], PageDocument] = {}
        for page in pages:
            source = source_map.get(page.document_id)
            if source is None or page.filename != source.filename:
                raise ValueError("Page source is not registered or filename differs")
            if type(page.page_number) is not int or not 1 <= page.page_number <= source.page_count or not isinstance(page.text, str):
                raise ValueError("Invalid page number or text")
            key = (page.document_id, page.page_number)
            if key in page_map:
                raise ValueError("Duplicate page record")
            page_map[key] = page
        for source in sources:
            if any((source.document_id, number) not in page_map for number in range(1, source.page_count + 1)):
                raise ValueError("Missing page record (including blank pages)")

        chunk_ids: set[str] = set()
        indexed_documents: set[str] = set()
        indexed_pages: set[tuple[str, int]] = set()
        for chunk in index.chunks:
            if chunk.chunk_id in chunk_ids:
                raise ValueError("Duplicate chunk ID")
            chunk_ids.add(chunk.chunk_id)
            page = page_map.get((chunk.document_id, chunk.page_number))
            if page is None or chunk.filename != page.filename:
                raise ValueError("Chunk source does not match the page snapshot")
            if (type(chunk.char_start) is not int or type(chunk.char_end) is not int
                    or not 0 <= chunk.char_start < chunk.char_end <= len(page.text)):
                raise ValueError("Chunk requires valid character offsets; rebuild legacy indexes")
            if page.text[chunk.char_start:chunk.char_end] != chunk.text:
                raise ValueError("Chunk text differs from page snapshot; rebuild stale data")
            indexed_documents.add(chunk.document_id)
            indexed_pages.add((chunk.document_id, chunk.page_number))
        if any(page.text.strip() and key not in indexed_pages for key, page in page_map.items()):
            raise ValueError("Text page has no indexed chunks; rebuild the incomplete index")

        self.documents = tuple(DocumentRecord(
            document_id=source.document_id, filename=source.filename,
            source=source.source, source_sha256=source.source_sha256,
            version=f"sha256:{source.source_sha256}", page_count=source.page_count,
            fulltext_status="fulltext_ready" if source.document_id in indexed_documents else "unusable",
            parse_warnings=tuple(source.parse_warnings),
        ) for source in sorted(sources, key=lambda s: s.document_id))
        self._pages = MappingProxyType(page_map)
        self._documents = MappingProxyType({doc.document_id: doc for doc in self.documents})
        self.index_fingerprint = _index_fingerprint(index)
        self.build_id = hashlib.sha256(_json_bytes(self._payload())).hexdigest()

    def _payload(self) -> dict:
        return {
            "schema_version": 1, "index_fingerprint": self.index_fingerprint,
            "documents": [asdict(doc) for doc in self.documents],
            "pages": [asdict(self._pages[key]) for key in sorted(self._pages)],
        }

    def list_documents(self) -> tuple[DocumentRecord, ...]:
        """Fulltext ready means indexed text is available, not read by a model."""
        return self.documents

    def export_pages(self) -> tuple[PageDocument, ...]:
        """Return the validated snapshot for composing a task-scoped index."""
        return tuple(self._pages[key] for key in sorted(self._pages))

    def read_pages(self, document_id: str, start_page: int, end_page: int | None = None,
                   *, max_chars: int = 6000, char_start: int = 0) -> PageRead:
        """Read physical PDF pages, using offsets in cleaned text, end exclusive.

        char_start resumes the first requested page only. A continuation cursor
        identifies omitted text so callers cannot mistake a prefix for a page.
        """
        if not isinstance(document_id, str) or document_id not in self._documents:
            raise ValueError(f"Unknown document ID: {document_id!r}")
        document = self._documents[document_id]
        if end_page is None:
            end_page = start_page
        if type(start_page) is not int or type(end_page) is not int or not 1 <= start_page <= end_page <= document.page_count:
            raise ValueError("Page range is outside the document")
        if end_page - start_page + 1 > MAX_PAGES_PER_READ:
            raise ValueError(f"Read at most {MAX_PAGES_PER_READ} pages per call")
        if type(max_chars) is not int or not 1 <= max_chars <= MAX_READ_CHARS:
            raise ValueError(f"max_chars must be in 1..{MAX_READ_CHARS}")
        if type(char_start) is not int or not 0 <= char_start <= len(self._pages[(document_id, start_page)].text):
            raise ValueError("char_start is outside the first page")

        excerpts: list[PageExcerpt] = []
        remaining = max_chars
        next_page = next_char = None
        for number in range(start_page, end_page + 1):
            text = self._pages[(document_id, number)].text
            start = char_start if number == start_page else 0
            end = min(len(text), start + remaining)
            excerpts.append(PageExcerpt(number, text[start:end], start, end, len(text), start > 0 or end < len(text)))
            remaining -= end - start
            if end < len(text):
                next_page, next_char = number, end
                break
            if remaining == 0 and number < end_page:
                next_page, next_char = number + 1, 0
                break
        return PageRead(
            document_id, document.filename, self.build_id, document.version,
            start_page, end_page, tuple(excerpts), next_page is not None or char_start > 0,
            next_page, next_char, document.parse_warnings,
        )

    def verify_sources(self, directory: str | Path) -> None:
        """Explicitly check current local PDFs; offline snapshots remain usable without them."""
        root = Path(directory).resolve()
        for doc in self.documents:
            path = (root / doc.source).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Source path escapes the selected directory")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != doc.source_sha256:
                raise ValueError(f"Source PDF is missing or changed: {doc.document_id}; rebuild the index")


def save_document_store(store: DocumentStore, directory: str | Path) -> Path:
    """Attach a snapshot to an existing matching index; manifest is written last.

    A partial write fails validation on the next load. Concurrent index writes
    are not supported; callers should complete construction before serving it.
    """
    root = Path(directory)
    if _index_fingerprint(load_dense_index(root)) != store.index_fingerprint:
        raise ValueError("Saved index differs from the document store")
    payload = {**store._payload(), "build_id": store.build_id}
    content = _json_bytes(payload)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["document_store"] = {
        "schema_version": 1, "build_id": store.build_id,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    _atomic_write(root / STORE_FILENAME, content)
    _atomic_write(manifest_path, _json_bytes(manifest))
    return root / STORE_FILENAME


def load_document_store(directory: str | Path, *, source_directory: str | Path | None = None) -> DocumentStore:
    """Reject unbound legacy data and any changed pages, chunks, or vectors."""
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    binding = manifest.get("document_store")
    if not isinstance(binding, dict) or binding.get("schema_version") != 1:
        raise ValueError("Index has no bound document store; re-ingest the paper into a new research task")
    content = (root / STORE_FILENAME).read_bytes()
    if hashlib.sha256(content).hexdigest() != binding.get("sha256"):
        raise ValueError("Document store checksum mismatch; rebuild stale data")
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported document store schema")
    try:
        sources = [IngestedDocument(
            document_id=doc["document_id"], filename=doc["filename"], source=doc["source"],
            source_sha256=doc["source_sha256"], page_count=doc["page_count"],
            parse_warnings=tuple(doc["parse_warnings"]),
        ) for doc in payload["documents"]]
        store = DocumentStore(load_dense_index(root), [PageDocument(**page) for page in payload["pages"]], sources)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid document store records; rebuild the snapshot") from error
    if (store.build_id != binding.get("build_id") or store.build_id != payload.get("build_id")
            or _json_bytes(store._payload()) != _json_bytes({k: v for k, v in payload.items() if k != "build_id"})):
        raise ValueError("Document/index build mismatch; rebuild stale data")
    if source_directory is not None:
        store.verify_sources(source_directory)
    return store
