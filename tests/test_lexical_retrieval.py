import math
from dataclasses import replace

import pytest

from paper_assistant.retrieval.embedding import build_dense_index
from paper_assistant.retrieval.lexical import (
    build_bm25_index, lexical_tokens, reciprocal_rank_fusion,
    search_bm25_index, search_hybrid_index,
)
from test_embedding import FakeEmbedder
from retrieval_fixtures import result


def test_bilingual_terms_and_pdf_whitespace_repair():
    assert lexical_tokens("坚 持训练") == lexical_tokens("坚持训练")
    assert lexical_tokens("中文 E5-small-v2 512") == ["中", "文", "中文", "e5-small-v2", "512"]
    assert lexical_tokens("中文，训练") == ["中", "文", "中文", "训", "练", "训练"]


def test_bm25_matches_hand_calculated_score_and_length_normalization():
    index = build_bm25_index([result(1, "alpha alpha beta").chunk, result(2, "beta").chunk])
    matches = search_bm25_index(index, "alpha", top_k=5)
    expected = math.log(2) * 2 * 2.5 / (2 + 1.5 * (0.25 + 0.75 * 3 / 2))
    assert len(matches) == 1
    assert matches[0].score == pytest.approx(expected)
    assert search_bm25_index(index, "beta")[0].chunk.chunk_index == 2
    assert search_bm25_index(index, "alpha alpha")[0].score == pytest.approx(expected)


def test_zero_lexical_matches_do_not_return_arbitrary_chunks():
    index = build_bm25_index([result(1, "时间停止手表").chunk])
    assert search_bm25_index(index, "How long can the watch stop time?") == []
    assert search_bm25_index(index, "时间停止")[0].score > 0
    assert search_bm25_index(index, "!!!") == []


def test_bm25_empty_content_and_stable_ties():
    index = build_bm25_index([result(1, "same").chunk, result(2, "same").chunk])
    assert [r.chunk.chunk_index for r in search_bm25_index(index, "same")] == [1, 2]
    assert search_bm25_index(build_bm25_index([result(1, "!!!").chunk]), "same") == []


@pytest.mark.parametrize("kwargs", [{"k1": 0}, {"k1": float("nan")}, {"b": -1}, {"b": 1.1}])
def test_invalid_bm25_parameters(kwargs):
    with pytest.raises(ValueError, match="BM25 requires"):
        build_bm25_index([result(1).chunk], **kwargs)


def test_rrf_hand_calculation_deduplication_and_ties():
    a, b, c = result(1), result(2), result(3)
    fused = reciprocal_rank_fusion([[a, b, c], [b, c, a]])
    assert [r.chunk.chunk_index for r in fused] == [2, 1, 3]
    assert fused[0].score == pytest.approx(1/61 + 1/62)
    assert len(fused) == 3
    # Symmetric scores retain first appearance from the dense list.
    assert reciprocal_rank_fusion([[a, b], [b, a]])[0].chunk == a.chunk
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_rejects_duplicate_and_conflicting_chunk_ids():
    a = result(1)
    with pytest.raises(ValueError, match="duplicate"):
        reciprocal_rank_fusion([[a, a]])
    with pytest.raises(ValueError, match="Conflicting"):
        reciprocal_rank_fusion([[a], [replace(a, chunk=replace(a.chunk, text="changed"))]])


def test_hybrid_no_lexical_matches_preserves_dense_ranking():
    chunks = [result(1, "vision").chunk, result(2, "retrieval").chunk]
    embedder = FakeEmbedder()
    dense = build_dense_index(chunks, embedder)
    lexical = build_bm25_index(chunks)
    assert search_bm25_index(lexical, "search") == []
    results = search_hybrid_index(dense, lexical, "search", embedder, top_k=2)
    assert [r.chunk.chunk_index for r in results] == [2, 1]
    with pytest.raises(ValueError, match="identical"):
        search_hybrid_index(dense, build_bm25_index(chunks[::-1]), "search", embedder)
    with pytest.raises(ValueError, match="candidate_k"):
        search_hybrid_index(dense, lexical, "search", embedder, top_k=5, candidate_k=2)
