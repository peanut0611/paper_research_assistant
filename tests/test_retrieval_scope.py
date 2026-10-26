from dataclasses import replace

import pytest

from paper_assistant.retrieval.embedding import build_dense_index, search_dense_index
from paper_assistant.retrieval.lexical import build_bm25_index, search_bm25_index, search_hybrid_index
from test_embedding import FakeEmbedder, make_chunk


@pytest.fixture
def scoped_search():
    chunks = [replace(make_chunk(i, text), document_id=doc) for i, (doc, text) in enumerate([
        ("a", "retrieval retrieval"), ("b", "retrieval"), ("target", "retrieval latency"),
    ], 1)]
    embedder = FakeEmbedder()
    dense = build_dense_index(chunks, embedder)
    lexical = build_bm25_index(chunks)
    return {
        "dense": lambda ids: search_dense_index(dense, "retrieval", embedder, top_k=1, document_ids=ids),
        "bm25": lambda ids: search_bm25_index(lexical, "retrieval", top_k=1, document_ids=ids),
        "hybrid": lambda ids: search_hybrid_index(dense, lexical, "retrieval", embedder,
                                                   top_k=1, candidate_k=1, document_ids=ids),
    }


@pytest.mark.parametrize("method", ["dense", "bm25", "hybrid"])
def test_scope_is_applied_before_top_k_and_hybrid_candidate_cutoff(scoped_search, method):
    search = scoped_search[method]
    assert search(None)[0].chunk.document_id != "target"
    result = search(["target"])
    assert len(result) == 1 and result[0].chunk.document_id == "target" and result[0].rank == 1
    assert search([]) == []


@pytest.mark.parametrize("method", ["dense", "bm25", "hybrid"])
@pytest.mark.parametrize("scope", [["unknown"], ["a", "unknown"], "target", [False]])
def test_invalid_scope_never_falls_back_to_full_corpus(scoped_search, method, scope):
    with pytest.raises(ValueError):
        scoped_search[method](scope)


def test_bm25_keeps_same_corpus_statistics_when_scope_changes():
    chunks = [replace(make_chunk(i, text), document_id=doc) for i, (doc, text) in enumerate([
        ("a", "retrieval retrieval"), ("target", "retrieval latency"),
    ], 1)]
    index = build_bm25_index(chunks)
    full = search_bm25_index(index, "retrieval")
    scoped = search_bm25_index(index, "retrieval", document_ids=["target"])
    assert scoped[0].score == next(r.score for r in full if r.chunk.document_id == "target")


def test_explicit_empty_scope_does_not_embed_a_query():
    class NoEmbedding(FakeEmbedder):
        def embed_query(self, text):
            raise AssertionError("Empty scope must not load or call the model")
    index = build_dense_index([make_chunk(1, "retrieval")], FakeEmbedder())
    assert search_dense_index(index, "query", NoEmbedding(), document_ids=[]) == []
