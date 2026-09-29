from pathlib import Path

import pytest

from paper_assistant.pdf_parser import parse_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = PROJECT_ROOT / "paper" / (
    "SLM-based_Hybrid_Retrieval_for_Resource_Constrained_"
    "Retrieval-Augmented_Generation_on_Open_Super-Large_Crawled_Data.pdf"
)


def test_parse_pdf_preserves_page_metadata() -> None:
    pages = parse_pdf(SAMPLE_PDF)

    assert len(pages) == 4
    assert [page.page_number for page in pages] == [1, 2, 3, 4]
    assert all(page.filename == SAMPLE_PDF.name for page in pages)
    assert all(page.document_id == SAMPLE_PDF.stem for page in pages)


def test_parse_pdf_extracts_non_empty_text() -> None:
    pages = parse_pdf(SAMPLE_PDF)

    assert all(page.text for page in pages)
    assert "Hybrid Retrieval" in pages[0].text
    assert "Retrieval-Augmented" in pages[0].text
    assert all("Authorized licensed use" not in page.text for page in pages)
    assert all("Downloaded on" not in page.text for page in pages)


def test_parse_pdf_can_return_raw_text() -> None:
    cleaned_page = parse_pdf(SAMPLE_PDF)[0]
    raw_page = parse_pdf(SAMPLE_PDF, clean=False)[0]

    assert cleaned_page.text != raw_page.text
    assert len(cleaned_page.text.splitlines()) < len(raw_page.text.splitlines())


def test_parse_pdf_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="PDF file not found"):
        parse_pdf(tmp_path / "missing.pdf")
