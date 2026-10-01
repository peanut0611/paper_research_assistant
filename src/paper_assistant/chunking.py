"""Deterministic word-window chunking for extracted paper pages."""

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from paper_assistant.pdf_parser import PageDocument


@dataclass(frozen=True)
class TextChunk:
    """A retrievable text unit with enough metadata for source citations."""

    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    chunk_index: int
    word_start: int
    word_end: int
    text: str

    @property
    def word_count(self) -> int:
        """Number of words in the chunk."""

        return self.word_end - self.word_start


def _validate_chunk_parameters(max_words: int, overlap_words: int) -> None:
    if max_words <= 0:
        raise ValueError("max_words must be greater than zero")
    if overlap_words < 0:
        raise ValueError("overlap_words cannot be negative")
    if overlap_words >= max_words:
        raise ValueError("overlap_words must be smaller than max_words")


def chunk_page(
    page: PageDocument,
    *,
    max_words: int = 180,
    overlap_words: int = 36,
) -> list[TextChunk]:
    """Split one page into overlapping word windows.

    ``word_start`` is zero-based and ``word_end`` is exclusive. Keeping chunks
    within one page makes every citation unambiguous in this first baseline.
    """

    _validate_chunk_parameters(max_words, overlap_words)
    words = page.text.split()
    if not words:
        return []

    step = max_words - overlap_words
    chunks: list[TextChunk] = []
    word_start = 0
    chunk_index = 1

    while word_start < len(words):
        word_end = min(word_start + max_words, len(words))
        chunk_id = (
            f"{page.document_id}::page-{page.page_number:04d}::"
            f"chunk-{chunk_index:04d}"
        )
        chunks.append(
            TextChunk(
                chunk_id=chunk_id,
                document_id=page.document_id,
                filename=page.filename,
                page_number=page.page_number,
                chunk_index=chunk_index,
                word_start=word_start,
                word_end=word_end,
                text=" ".join(words[word_start:word_end]),
            )
        )

        if word_end == len(words):
            break

        word_start += step
        chunk_index += 1

    return chunks


def chunk_pages(
    pages: Iterable[PageDocument],
    *,
    max_words: int = 180,
    overlap_words: int = 36,
) -> list[TextChunk]:
    """Chunk a sequence of pages while preserving their source metadata."""

    _validate_chunk_parameters(max_words, overlap_words)
    chunks: list[TextChunk] = []

    for page in pages:
        chunks.extend(
            chunk_page(
                page,
                max_words=max_words,
                overlap_words=overlap_words,
            )
        )

    return chunks


def write_chunks_jsonl(
    chunks: Iterable[TextChunk],
    output_path: str | Path,
) -> Path:
    """Write one chunk record per line for embedding and inspection."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("w", encoding="utf-8") as output_file:
        for chunk in chunks:
            record = asdict(chunk)
            record["word_count"] = chunk.word_count
            output_file.write(json.dumps(record, ensure_ascii=False))
            output_file.write("\n")

    return destination
