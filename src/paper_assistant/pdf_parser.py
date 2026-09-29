"""Page-level PDF text extraction.

This module intentionally starts with a small baseline: extract the text from
each PDF page and retain the source metadata needed for later citations.
"""

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from paper_assistant.text_cleaner import clean_page_text, remove_page_boilerplate


@dataclass(frozen=True)
class PageDocument:
    """Text extracted from one PDF page together with its source metadata."""

    document_id: str
    filename: str
    page_number: int
    text: str


def parse_pdf(path: str | Path, *, clean: bool = True) -> list[PageDocument]:
    """Extract page-level text and citation metadata from a PDF file."""

    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")

    reader = PdfReader(pdf_path)
    document_id = pdf_path.stem

    pages: list[PageDocument] = []

    for page_number, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text() or ""
        text = (
            clean_page_text(remove_page_boilerplate(raw_text))
            if clean
            else raw_text.strip()
        )
        pages.append(
            PageDocument(
                document_id=document_id,
                filename=pdf_path.name,
                page_number=page_number,
                text=text,
            )
        )

    return pages
