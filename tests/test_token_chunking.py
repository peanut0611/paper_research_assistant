from pathlib import Path

import pytest

from paper_assistant.chunking import chunk_pages_by_tokens
from paper_assistant.pdf_parser import PageDocument
from paper_assistant.embedding import build_dense_index, load_dense_index, save_dense_index
from test_embedding import FakeEmbedder


class CharacterTokenizer:
    """One token per Unicode character, plus two optional special tokens."""

    def __call__(self, text: str, *, add_special_tokens=True, **kwargs):
        result = {"input_ids": list(range(len(text) + (2 if add_special_tokens else 0)))}
        if kwargs.get("return_offsets_mapping"):
            result["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return result


@pytest.mark.parametrize("text", [
    "中文没有空格也应该被正确切分而且每一个字符都保留下来。" * 3,
    "English retrieval with overlapping source text. " * 3,
    "中文 retrieval 混合 English 文本。" * 3,
])
def test_token_chunks_preserve_source_coverage_overlap_and_budget(text: str):
    page = PageDocument("doc", "doc.pdf", 2, text)
    tokenizer = CharacterTokenizer()
    chunks = chunk_pages_by_tokens([page], tokenizer, max_tokens=32, overlap_tokens=5)
    assert len(chunks) > 1
    covered = set()
    for chunk in chunks:
        assert chunk.text == text[chunk.char_start:chunk.char_end]
        assert chunk.token_count == len(tokenizer("passage: " + chunk.text)["input_ids"])
        assert chunk.token_count <= 32
        assert chunk.filename == "doc.pdf" and chunk.page_number == 2
        covered.update(range(chunk.char_start, chunk.char_end))
    assert covered == set(range(len(text)))
    for left, right in zip(chunks, chunks[1:]):
        assert right.char_start == left.char_end - 5
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


@pytest.mark.parametrize("max_tokens,overlap_tokens", [(0, 0), (10, 0), (32, -1), (32, 22)])
def test_invalid_token_budgets(max_tokens, overlap_tokens):
    with pytest.raises(ValueError):
        chunk_pages_by_tokens([], CharacterTokenizer(), max_tokens=max_tokens, overlap_tokens=overlap_tokens)


def test_blank_pages_and_zero_overlap():
    assert chunk_pages_by_tokens([PageDocument("d", "d.pdf", 1, "  ")], CharacterTokenizer()) == []
    chunks = chunk_pages_by_tokens(
        [PageDocument("d", "d.pdf", 1, "中文测试" * 20)],
        CharacterTokenizer(), max_tokens=32, overlap_tokens=0,
    )
    assert all(a.char_end == b.char_start for a, b in zip(chunks, chunks[1:]))


def test_rechecks_tokens_after_slicing_source():
    class BoundaryTokenizer(CharacterTokenizer):
        def __call__(self, text: str, *, add_special_tokens=True, **kwargs):
            result = super().__call__(text, add_special_tokens=add_special_tokens, **kwargs)
            # Simulate a tokenization change when passage content is attached.
            if add_special_tokens and text.startswith("passage: ") and text != "passage: ":
                result["input_ids"].extend([100] * 4)
            return result

    text = "中文和English" * 5
    tokenizer = BoundaryTokenizer()
    chunks = chunk_pages_by_tokens(
        [PageDocument("d", "d.pdf", 1, text)], tokenizer,
        max_tokens=32, overlap_tokens=0,
    )
    assert len(chunks[0].text) == 17  # 32 - prefix(9) - specials(2) - boundary(4)
    assert all(chunk.token_count <= 32 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == text


def test_token_metadata_survives_index_round_trip(tmp_path: Path):
    chunks = chunk_pages_by_tokens(
        [PageDocument("d", "d.pdf", 1, "中文 retrieval")], CharacterTokenizer(),
    )
    index = build_dense_index(chunks, FakeEmbedder())
    save_dense_index(index, tmp_path)
    assert load_dense_index(tmp_path).chunks == index.chunks
