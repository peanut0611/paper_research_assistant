"""Page-level PDF text extraction.

This module intentionally starts with a small baseline: extract the text from
each PDF page and retain the source metadata needed for later citations.
"""

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from paper_assistant.documents.text_cleaner import clean_page_text, remove_page_boilerplate


@dataclass(frozen=True)
class PageDocument:
    """Text extracted from one PDF page together with its source metadata."""

    document_id: str
    filename: str
    page_number: int
    text: str


class PDFLimitError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_pdf(path: str | Path, *, clean: bool = True, max_pages: int | None = None,
              max_text_chars: int | None = None) -> list[PageDocument]:
    """Extract page-level text and citation metadata from a PDF file."""

    for value in (max_pages, max_text_chars):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError("PDF limits must be positive integers")
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")

    reader = PdfReader(pdf_path)
    if max_pages is not None and len(reader.pages) > max_pages:
        raise PDFLimitError("page_limit_exceeded", "PDF exceeds the task page limit")
    document_id = pdf_path.stem

    pages: list[PageDocument] = []
    text_chars = 0

    for page_number, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text() or ""
        text = (
            clean_page_text(remove_page_boilerplate(raw_text))
            if clean
            else raw_text.strip()
        )
        text_chars += len(text)
        if max_text_chars is not None and text_chars > max_text_chars:
            raise PDFLimitError("text_limit_exceeded", "Extracted PDF text exceeds the task character limit")
        pages.append(
            PageDocument(
                document_id=document_id,
                filename=pdf_path.name,
                page_number=page_number,
                text=text,
            )
        )

    return pages
