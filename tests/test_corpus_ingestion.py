import json
from pathlib import Path

import pytest

from paper_assistant.corpus_ingestion import (
    ingest_pdf_directory,
    write_pages_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAPER_DIRECTORY = PROJECT_ROOT / "paper"


def test_ingest_pdf_directory_reads_sample_corpus() -> None:
    report = ingest_pdf_directory(PAPER_DIRECTORY)

    assert len(report.processed_files) == 1
    assert len(report.pages) == 4
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


def test_write_pages_jsonl_preserves_page_metadata(tmp_path: Path) -> None:
    report = ingest_pdf_directory(PAPER_DIRECTORY)
    output_path = write_pages_jsonl(report.pages, tmp_path / "pages.jsonl")

    records = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(records) == 4
    assert records[0]["page_number"] == 1
    assert records[0]["filename"].endswith(".pdf")
    assert records[0]["text"]

