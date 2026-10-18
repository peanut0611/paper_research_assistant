"""Page-local word and multilingual token chunking with source metadata."""

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

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
    char_start: int | None = None
    char_end: int | None = None
    token_count: int | None = None

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


class OffsetTokenizer(Protocol):
    """A fast tokenizer supporting source character offsets."""

    def __call__(self, text: str, **kwargs: Any) -> Any: ...


def chunk_pages_by_tokens(
    pages: Iterable[PageDocument],
    tokenizer: OffsetTokenizer,
    *,
    max_tokens: int = 384,
    overlap_tokens: int = 64,
    document_prefix: str = "passage: ",
) -> list[TextChunk]:
    """Split Chinese, English, or mixed pages using the embedding tokenizer.

    max_tokens includes the document prefix and special tokens. Overlap counts
    content tokens. Each emitted substring is re-tokenized to account for
    tokenization changes at boundaries. Character offsets are zero-based,
    end-exclusive positions in the cleaned page, preserving exact source text.
    Word ranges remain available for compatibility; use character ranges for
    Chinese because whitespace-delimited words are not meaningful there.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")
    overhead = len(tokenizer(document_prefix, add_special_tokens=True)["input_ids"])
    content_budget = max_tokens - overhead
    if content_budget <= 0:
        raise ValueError("max_tokens must leave room for document content")
    if not 0 <= overlap_tokens < content_budget:
        raise ValueError("overlap_tokens must be non-negative and smaller than the content budget")

    chunks: list[TextChunk] = []
    for page in pages:
        if not page.text.strip():
            continue
        encoded = tokenizer(
            page.text, add_special_tokens=False, return_offsets_mapping=True,
            truncation=False, verbose=False,
        )
        offsets = [(start, end) for start, end in encoded["offset_mapping"] if end > start]
        if not offsets:
            raise ValueError(f"Tokenizer produced no offsets for {page.filename}, page {page.page_number}")
        words = list(re.finditer(r"\S+", page.text))
        start = 0
        chunk_index = 1
        while start < len(offsets):
            end = min(start + content_budget, len(offsets))
            char_start = offsets[start][0]
            while end > start:
                char_end = offsets[end - 1][1]
                text = page.text[char_start:char_end]
                token_count = len(tokenizer(
                    document_prefix + text, add_special_tokens=True,
                    truncation=False, verbose=False,
                )["input_ids"])
                if token_count <= max_tokens:
                    break
                end -= 1
            if end == start:
                raise ValueError("A source character cannot fit within max_tokens")
            chunks.append(TextChunk(
                chunk_id=f"{page.document_id}::page-{page.page_number:04d}::chunk-{chunk_index:04d}",
                document_id=page.document_id, filename=page.filename,
                page_number=page.page_number, chunk_index=chunk_index,
                word_start=sum(word.end() <= char_start for word in words),
                word_end=sum(word.start() < char_end for word in words),
                text=text, char_start=char_start, char_end=char_end,
                token_count=token_count,
            ))
            if end == len(offsets):
                break
            start = max(start + 1, end - overlap_tokens)
            chunk_index += 1
    return chunks
