import json
from pathlib import Path

import pytest

from paper_assistant.corpus_ingestion import (
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

