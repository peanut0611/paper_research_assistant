import json
import hashlib
import logging
from pathlib import Path

import pytest

from paper_assistant.documents.ingestion import (
    ingest_pdf_directory,
    write_pages_jsonl,
)


def test_ingest_pdf_directory_reads_sample_corpus(sample_pdf: Path) -> None:
    report = ingest_pdf_directory(sample_pdf.parent)

    assert len(report.processed_files) == 1
    assert len(report.pages) == 2
    assert report.blank_pages == ()
    assert report.failures == ()


def test_ingest_pdf_directory_isolates_invalid_pdf(tmp_path: Path) -> None:
    invalid_pdf = tmp_path / "broken.pdf"
    invalid_pdf.write_text("not a real PDF", encoding="utf-8")

    report = ingest_pdf_directory(tmp_path)

    assert report.processed_files == ()
    assert report.pages == ()
    assert len(report.failures) == 1
    assert report.failures[0].filename == "broken.pdf"


def test_ingest_pdf_directory_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError, match="PDF directory not found"):
        ingest_pdf_directory(tmp_path / "missing")


def test_write_pages_jsonl_preserves_page_metadata(tmp_path: Path, sample_pdf: Path) -> None:
    report = ingest_pdf_directory(sample_pdf.parent)
    output_path = write_pages_jsonl(report.pages, tmp_path / "pages.jsonl")

    records = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(records) == 2
    assert records[0]["page_number"] == 1
    assert records[0]["filename"].endswith(".pdf")
    assert records[0]["text"]


def test_ingestion_records_source_hash_page_count_and_parser_warning(sample_pdf, monkeypatch):
    import paper_assistant.documents.ingestion as ingestion

    original = ingestion.parse_pdf
    def parse_with_warning(path):
        logging.getLogger("pypdf").warning("Test extraction limit")
        return original(path)
    monkeypatch.setattr(ingestion, "parse_pdf", parse_with_warning)
    report = ingestion.ingest_pdf_directory(sample_pdf.parent)
    doc = report.documents[0]
    assert doc.source_sha256 == hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    assert doc.page_count == 2
    assert doc.parse_warnings == ("Test extraction limit",)


def test_recursive_duplicate_stems_are_rejected_before_parsing(sample_pdf):
    nested = sample_pdf.parent / "nested"
    nested.mkdir()
    (nested / sample_pdf.name).write_bytes(sample_pdf.read_bytes())
    with pytest.raises(ValueError, match="Duplicate document ID"):
        ingest_pdf_directory(sample_pdf.parent, recursive=True)


def test_pdf_changed_during_extraction_is_not_added(sample_pdf, monkeypatch):
    import paper_assistant.documents.ingestion as ingestion

    original = ingestion.parse_pdf
    def changing_parse(path):
        pages = original(path)
        path.write_bytes(path.read_bytes() + b"\n%changed")
        return pages
    monkeypatch.setattr(ingestion, "parse_pdf", changing_parse)
    report = ingestion.ingest_pdf_directory(sample_pdf.parent)
    assert not report.pages and not report.documents
    assert "changed during extraction" in report.failures[0].error

