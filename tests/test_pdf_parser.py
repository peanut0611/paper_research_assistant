from pathlib import Path
from unittest.mock import patch

import pytest

from paper_assistant.documents.pdf_parser import PDFLimitError, parse_pdf


def test_parse_pdf_preserves_page_metadata(sample_pdf: Path) -> None:
    pages = parse_pdf(sample_pdf)

    assert len(pages) == 2
    assert [page.page_number for page in pages] == [1, 2]
    assert all(page.filename == sample_pdf.name for page in pages)
    assert all(page.document_id == sample_pdf.stem for page in pages)


def test_parse_pdf_extracts_non_empty_text(sample_pdf: Path) -> None:
    pages = parse_pdf(sample_pdf)

    assert all(page.text for page in pages)
    assert "Hybrid Retrieval" in pages[0].text
    assert "Retrieval-Augmented" in pages[0].text
    assert all("Authorized licensed use" not in page.text for page in pages)
    assert all("Downloaded on" not in page.text for page in pages)


def test_parse_pdf_can_return_raw_text(sample_pdf: Path) -> None:
    cleaned_page = parse_pdf(sample_pdf)[0]
    raw_page = parse_pdf(sample_pdf, clean=False)[0]

    assert cleaned_page.text != raw_page.text
    assert len(cleaned_page.text.splitlines()) < len(raw_page.text.splitlines())


def test_parse_pdf_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="PDF file not found"):
        parse_pdf(tmp_path / "missing.pdf")


def test_page_limit_is_checked_before_any_text_extraction(sample_pdf):
    with patch("pypdf._page.PageObject.extract_text", side_effect=AssertionError("Must not extract")):
        with pytest.raises(PDFLimitError) as caught:
            parse_pdf(sample_pdf, max_pages=1)
    assert caught.value.code == "page_limit_exceeded"


def test_character_limit_stops_instead_of_silently_truncating(sample_pdf):
    with pytest.raises(PDFLimitError) as caught:
        parse_pdf(sample_pdf, max_text_chars=3)
    assert caught.value.code == "text_limit_exceeded"
