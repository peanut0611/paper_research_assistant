"""Batch ingestion for a directory of academic PDF files."""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf.errors import PdfReadError

from paper_assistant.tools.knowledge.pdf_parser import PageDocument, parse_pdf


@dataclass(frozen=True)
class BlankPage:
    """A source page from which no usable text could be extracted."""

    filename: str
    page_number: int


@dataclass(frozen=True)
class IngestionFailure:
    """A PDF that could not be parsed without stopping the whole batch."""

    filename: str
    error: str


@dataclass(frozen=True)
class IngestedDocument:
    """Source identity captured alongside the extracted text, not inferred later."""

    document_id: str
    filename: str
    source: str
    source_sha256: str
    page_count: int
    parse_warnings: tuple[str, ...] = ()


class _WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@dataclass(frozen=True)
class IngestionReport:
    """Structured result of ingesting one PDF directory."""

    pages: tuple[PageDocument, ...]
    processed_files: tuple[str, ...]
    blank_pages: tuple[BlankPage, ...]
    failures: tuple[IngestionFailure, ...]
    documents: tuple[IngestedDocument, ...] = ()


def ingest_pdf_directory(
    directory: str | Path,
    *,
    recursive: bool = False,
) -> IngestionReport:
    """Parse every PDF in a directory and isolate per-file failures."""

    paper_directory = Path(directory)
    if not paper_directory.is_dir():
        raise NotADirectoryError(f"PDF directory not found: {paper_directory}")

    pattern = "**/*.pdf" if recursive else "*.pdf"
    pdf_paths = sorted(
        paper_directory.glob(pattern),
        key=lambda path: str(path.relative_to(paper_directory)).casefold(),
    )

    # The parser's historical ID is the filename stem. Keep it, but reject
    # ambiguous nested filenames before parsing or writing any new artifacts.
    identifiers: dict[str, Path] = {}
    for pdf_path in pdf_paths:
        identifier = pdf_path.stem.casefold()
        if identifier in identifiers:
            raise ValueError(
                f"Duplicate document ID {pdf_path.stem!r}: "
                f"{identifiers[identifier]} and {pdf_path}"
            )
        identifiers[identifier] = pdf_path

    pages: list[PageDocument] = []
    processed_files: list[str] = []
    blank_pages: list[BlankPage] = []
    failures: list[IngestionFailure] = []
    documents: list[IngestedDocument] = []

    for pdf_path in pdf_paths:
        relative_name = str(pdf_path.relative_to(paper_directory))
        collector = _WarningCollector()
        logger = logging.getLogger("pypdf")
        logger.addHandler(collector)
        try:
            source_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            parsed_pages = parse_pdf(pdf_path)
            if hashlib.sha256(pdf_path.read_bytes()).hexdigest() != source_sha256:
                raise ValueError("PDF changed during extraction; run ingestion again")
        except (OSError, ValueError, PdfReadError) as error:
            failures.append(
                IngestionFailure(filename=relative_name, error=str(error))
            )
            continue
        finally:
            logger.removeHandler(collector)

        processed_files.append(relative_name)
        documents.append(IngestedDocument(
            document_id=pdf_path.stem, filename=pdf_path.name,
            source=relative_name, source_sha256=source_sha256,
            page_count=len(parsed_pages),
            parse_warnings=tuple(dict.fromkeys(collector.messages)),
        ))
        pages.extend(parsed_pages)
        blank_pages.extend(
            BlankPage(filename=relative_name, page_number=page.page_number)
            for page in parsed_pages
            if not page.text.strip()
        )

    return IngestionReport(
        pages=tuple(pages),
        processed_files=tuple(processed_files),
        blank_pages=tuple(blank_pages),
        failures=tuple(failures),
        documents=tuple(documents),
    )


def write_pages_jsonl(
    pages: tuple[PageDocument, ...] | list[PageDocument],
    output_path: str | Path,
) -> Path:
    """Write one page record per line for inspection and later chunking."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("w", encoding="utf-8") as output_file:
        for page in pages:
            output_file.write(json.dumps(asdict(page), ensure_ascii=False))
            output_file.write("\n")

    return destination

