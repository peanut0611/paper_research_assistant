import json
from pathlib import Path

import pytest

from paper_assistant.tools.knowledge.chunking import chunk_page, chunk_pages, write_chunks_jsonl
from paper_assistant.tools.knowledge.ingestion import ingest_pdf_directory
from paper_assistant.tools.knowledge.pdf_parser import PageDocument


def make_page(text: str) -> PageDocument:
    return PageDocument(
        document_id="paper-one",
        filename="paper-one.pdf",
        page_number=3,
        text=text,
    )


def test_chunk_page_creates_expected_overlap() -> None:
    page = make_page(" ".join(f"word-{number}" for number in range(1, 11)))

    chunks = chunk_page(page, max_words=4, overlap_words=1)

    assert [chunk.text for chunk in chunks] == [
        "word-1 word-2 word-3 word-4",
        "word-4 word-5 word-6 word-7",
        "word-7 word-8 word-9 word-10",
    ]
    assert [(chunk.word_start, chunk.word_end) for chunk in chunks] == [
        (0, 4),
        (3, 7),
        (6, 10),
    ]


def test_chunk_page_preserves_citation_metadata() -> None:
    chunks = chunk_page(make_page("one two three"), max_words=2, overlap_words=1)

    assert [chunk.chunk_index for chunk in chunks] == [1, 2]
    assert all(chunk.document_id == "paper-one" for chunk in chunks)
    assert all(chunk.filename == "paper-one.pdf" for chunk in chunks)
    assert all(chunk.page_number == 3 for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == 2


def test_chunk_page_skips_blank_text() -> None:
    assert chunk_page(make_page("   \n")) == []


@pytest.mark.parametrize(
    ("max_words", "overlap_words", "message"),
    [
        (0, 0, "max_words must be greater than zero"),
        (10, -1, "overlap_words cannot be negative"),
        (10, 10, "overlap_words must be smaller than max_words"),
    ],
)
def test_chunk_page_rejects_invalid_parameters(
    max_words: int,
    overlap_words: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        chunk_page(
            make_page("some text"),
            max_words=max_words,
            overlap_words=overlap_words,
        )


def test_chunk_sample_corpus_respects_default_word_limit(sample_pdf: Path) -> None:
    report = ingest_pdf_directory(sample_pdf.parent)

    chunks = chunk_pages(report.pages)

    assert chunks
    assert all(0 < chunk.word_count <= 180 for chunk in chunks)
    assert all(chunk.page_number in {page.page_number for page in report.pages} for chunk in chunks)


def test_write_chunks_jsonl_includes_derived_word_count(tmp_path: Path) -> None:
    chunks = chunk_page(make_page("one two three"), max_words=2, overlap_words=1)
    output_path = write_chunks_jsonl(chunks, tmp_path / "chunks.jsonl")

    records = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(records) == 2
    assert records[0]["word_count"] == 2
    assert records[0]["chunk_id"].endswith("chunk-0001")

