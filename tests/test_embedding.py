from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paper_assistant.documents.chunking import TextChunk
from paper_assistant.retrieval.embedding import (
    SentenceTransformerEmbedder,
    build_dense_index,
    load_dense_index,
    save_dense_index,
    search_dense_index,
)


class FakeEmbedder:
    model_name = "fake-keyword-model"

    @staticmethod
    def _embed(text: str) -> NDArray[np.float32]:
        lowered = text.lower()
        return np.asarray(
            [
                float("retrieval" in lowered or "search" in lowered),
                float("latency" in lowered or "speed" in lowered),
                float("vision" in lowered or "image" in lowered),
            ],
            dtype=np.float32,
        )

    def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.stack([self._embed(text) for text in texts])

    def embed_query(self, text: str) -> NDArray[np.float32]:
        return self._embed(text)


def make_chunk(chunk_index: int, text: str) -> TextChunk:
    return TextChunk(
        chunk_id=f"paper::page-0001::chunk-{chunk_index:04d}",
        document_id="paper",
        filename="paper.pdf",
        page_number=1,
        chunk_index=chunk_index,
        word_start=0,
        word_end=len(text.split()),
        text=text,
    )


def test_build_dense_index_normalizes_document_vectors() -> None:
    chunks = [
        make_chunk(1, "retrieval latency"),
        make_chunk(2, "vision image"),
    ]

    index = build_dense_index(chunks, FakeEmbedder())

    assert index.embeddings.shape == (2, 3)
    assert np.allclose(np.linalg.norm(index.embeddings, axis=1), 1.0)


def test_search_dense_index_ranks_semantic_match_first() -> None:
    chunks = [
        make_chunk(1, "retrieval systems"),
        make_chunk(2, "low latency speed"),
        make_chunk(3, "computer vision image"),
    ]
    embedder = FakeEmbedder()
    index = build_dense_index(chunks, embedder)

    results = search_dense_index(index, "search retrieval", embedder, top_k=2)

    assert [result.chunk.chunk_index for result in results] == [1, 2]
    assert results[0].score == pytest.approx(1.0)


def test_search_dense_index_rejects_invalid_top_k() -> None:
    chunks = [make_chunk(1, "retrieval")]
    embedder = FakeEmbedder()
    index = build_dense_index(chunks, embedder)

    with pytest.raises(ValueError, match="top_k must be greater than zero"):
        search_dense_index(index, "retrieval", embedder, top_k=0)


def test_dense_index_round_trip(tmp_path: Path) -> None:
    chunks = [
        make_chunk(1, "retrieval systems"),
        make_chunk(2, "computer vision"),
    ]
    original = build_dense_index(chunks, FakeEmbedder())

    save_dense_index(original, tmp_path)
    loaded = load_dense_index(tmp_path)

    assert loaded.model_name == original.model_name
    assert loaded.chunks == original.chunks
    assert np.array_equal(loaded.embeddings, original.embeddings)


def test_build_dense_index_rejects_zero_vector() -> None:
    chunks = [make_chunk(1, "unrecognized content")]

    with pytest.raises(ValueError, match="zero length"):
        build_dense_index(chunks, FakeEmbedder())


def test_sentence_transformer_embedder_rejects_silent_truncation() -> None:
    class FakeTokenizer:
        def __call__(self, texts: Sequence[str], **_: object) -> dict[str, list[list[int]]]:
            return {"input_ids": [list(range(len(text.split()))) for text in texts]}

    class FakeModel:
        tokenizer = FakeTokenizer()

    embedder = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    embedder._model = FakeModel()
    embedder.max_sequence_length = 3

    with pytest.raises(ValueError, match="exceeds the model limit of 3 tokens"):
        embedder._validate_input_lengths(
            ["one two three four"],
            input_kind="document",
        )


def test_model_cache_identity_tracks_resolved_checkpoint_tokenizer_and_prefixes():
    from types import SimpleNamespace
    class Model:
        tokenizer = SimpleNamespace(backend_tokenizer=SimpleNamespace(to_str=lambda: '{"tokenizer":"v1"}'))
        def __getitem__(self, index):
            return SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="checkpoint-v1")))
        def get_sentence_embedding_dimension(self):
            return 3
    embedder = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    embedder._model = Model()
    embedder.model_name = "fake-model"
    embedder.max_sequence_length = 512
    embedder.document_prefix = "passage: "
    embedder.query_prefix = "query: "
    first = embedder.cache_identity
    assert first["revision"] == "checkpoint-v1" and first["dimension"] == 3
    embedder.document_prefix = "changed: "
    assert embedder.cache_identity != first


def test_local_model_cache_identity_hashes_actual_weights_if_no_hub_revision():
    from types import SimpleNamespace
    class Tensor:
        def __init__(self):
            self.values = np.asarray([1, 2], dtype=np.float32)
        def detach(self): return self
        def cpu(self): return self
        def contiguous(self): return self
        def numpy(self): return self.values
    tensor = Tensor()
    class Model:
        tokenizer = SimpleNamespace(backend_tokenizer=SimpleNamespace(to_str=lambda: "tokenizer"))
        def __getitem__(self, index): return SimpleNamespace(auto_model=SimpleNamespace(config=None))
        def state_dict(self): return {"weights": tensor}
        def get_sentence_embedding_dimension(self): return 2
    embedder = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    embedder._model = Model()
    embedder.model_name, embedder.max_sequence_length = "local-model", 512
    embedder.document_prefix, embedder.query_prefix = "passage: ", "query: "
    first = embedder.cache_identity
    tensor.values[0] = 3
    assert first["revision"].startswith("sha256:")
    assert embedder.cache_identity["revision"] != first["revision"]
