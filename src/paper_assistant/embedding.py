"""Dense embeddings, persistence, and cosine retrieval for paper chunks."""

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from paper_assistant.chunking import OffsetTokenizer, TextChunk


DEFAULT_MODEL_NAME = "intfloat/multilingual-e5-small"


class TextEmbedder(Protocol):
    """Minimal interface required by the dense retrieval pipeline."""

    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]: ...

    def embed_query(self, text: str) -> NDArray[np.float32]: ...


class SentenceTransformerEmbedder:
    """Sentence Transformers adapter configured for asymmetric E5 retrieval."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        *,
        cache_folder: str | Path = "data/models",
        batch_size: int = 32,
        device: str | None = None,
        show_progress_bar: bool = False,
        query_prefix: str = "query: ",
        document_prefix: str = "passage: ",
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.show_progress_bar = show_progress_bar
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix

        model_cache = Path(cache_folder).resolve()
        model_cache.mkdir(parents=True, exist_ok=True)

        # Keep every Hugging Face/Xet cache inside the project. Besides making
        # downloads reproducible, this avoids hidden writes to a user's home
        # cache in sandboxed or CI environments. These must be set before the
        # Hugging Face libraries are imported.
        os.environ.setdefault("HF_HOME", str(model_cache))
        os.environ.setdefault("HF_HUB_CACHE", str(model_cache / "hub"))
        os.environ.setdefault("HF_XET_CACHE", str(model_cache / "xet"))
        os.environ.setdefault("XDG_CACHE_HOME", str(model_cache / "xdg"))

        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(
            model_name,
            cache_folder=str(model_cache),
            device=device,
        )
        self.max_sequence_length = int(self._model.max_seq_length)

    @property
    def device(self) -> str:
        return str(self._model.device)

    @property
    def tokenizer(self) -> OffsetTokenizer:
        """The same tokenizer used for chunking and embedding length checks."""
        return self._model.tokenizer

    @property
    def dimension(self) -> int:
        dimension = self._model.get_sentence_embedding_dimension()
        if dimension is None:
            raise ValueError("Embedding model did not report its dimension")
        return int(dimension)

    def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        inputs = [f"{self.document_prefix}{text}" for text in texts]
        self._validate_input_lengths(inputs, input_kind="document")
        embeddings = self._model.encode(
            inputs,
            batch_size=self.batch_size,
            show_progress_bar=self.show_progress_bar,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(embeddings, dtype=np.float32)

    def embed_query(self, text: str) -> NDArray[np.float32]:
        if not text.strip():
            raise ValueError("Query cannot be blank")

        inputs = [f"{self.query_prefix}{text}"]
        self._validate_input_lengths(inputs, input_kind="query")
        embedding = self._model.encode(
            inputs,
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )[0]
        return np.asarray(embedding, dtype=np.float32)

    def _validate_input_lengths(
        self,
        inputs: Sequence[str],
        *,
        input_kind: str,
    ) -> None:
        tokenized = self._model.tokenizer(
            list(inputs),
            add_special_tokens=True,
            padding=False,
            truncation=False,
            verbose=False,
        )
        token_counts = [len(input_ids) for input_ids in tokenized["input_ids"]]
        over_limit = [
            (index, count)
            for index, count in enumerate(token_counts)
            if count > self.max_sequence_length
        ]
        if over_limit:
            examples = ", ".join(
                f"index {index}: {count} tokens"
                for index, count in over_limit[:5]
            )
            raise ValueError(
                f"{input_kind.capitalize()} text exceeds the model limit of "
                f"{self.max_sequence_length} tokens ({examples}). "
                "Reduce the chunk size instead of silently truncating text."
            )


@dataclass(frozen=True)
class DenseIndex:
    """Normalized document vectors aligned row-for-row with source chunks."""

    chunks: tuple[TextChunk, ...]
    embeddings: NDArray[np.float32]
    model_name: str

    @property
    def dimension(self) -> int:
        return int(self.embeddings.shape[1])


@dataclass(frozen=True)
class SearchResult:
    """A ranked chunk returned by dense cosine retrieval."""

    rank: int
    score: float
    chunk: TextChunk


def _normalize_rows(vectors: NDArray[np.float32]) -> NDArray[np.float32]:
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2:
        raise ValueError("Document embeddings must be a two-dimensional matrix")

    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Embedding vectors cannot have zero length")
    return array / norms


def _normalize_query(vector: NDArray[np.float32]) -> NDArray[np.float32]:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if norm == 0:
        raise ValueError("Query embedding cannot have zero length")
    return array / norm


def build_dense_index(
    chunks: Sequence[TextChunk],
    embedder: TextEmbedder,
) -> DenseIndex:
    """Embed chunks and build an in-memory normalized dense index."""

    if not chunks:
        raise ValueError("Cannot build an index without chunks")

    embeddings = embedder.embed_documents([chunk.text for chunk in chunks])
    if embeddings.shape[0] != len(chunks):
        raise ValueError("Embedding row count does not match chunk count")

    normalized = _normalize_rows(embeddings)
    return DenseIndex(
        chunks=tuple(chunks),
        embeddings=normalized,
        model_name=embedder.model_name,
    )


def search_dense_index(
    index: DenseIndex,
    query: str,
    embedder: TextEmbedder,
    *,
    top_k: int = 5,
) -> list[SearchResult]:
    """Rank chunks by cosine similarity to an embedded query."""

    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")
    if embedder.model_name != index.model_name:
        raise ValueError(
            "Query embedder model does not match the index model: "
            f"{embedder.model_name!r} != {index.model_name!r}"
        )

    query_vector = _normalize_query(embedder.embed_query(query))
    if query_vector.shape[0] != index.dimension:
        raise ValueError("Query embedding dimension does not match the index")

    scores = index.embeddings @ query_vector
    ranked_indexes = np.argsort(-scores, kind="stable")[:top_k]

    return [
        SearchResult(
            rank=rank,
            score=float(scores[chunk_position]),
            chunk=index.chunks[int(chunk_position)],
        )
        for rank, chunk_position in enumerate(ranked_indexes, start=1)
    ]


def save_dense_index(index: DenseIndex, directory: str | Path) -> Path:
    """Persist vectors, chunk metadata, and model configuration."""

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)

    np.save(destination / "embeddings.npy", index.embeddings, allow_pickle=False)

    with (destination / "chunks.jsonl").open("w", encoding="utf-8") as file:
        for chunk in index.chunks:
            file.write(json.dumps(asdict(chunk), ensure_ascii=False))
            file.write("\n")

    manifest = {
        "model_name": index.model_name,
        "dimension": index.dimension,
        "chunk_count": len(index.chunks),
        "normalized": True,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def load_dense_index(directory: str | Path) -> DenseIndex:
    """Load a dense index and validate alignment between vectors and chunks."""

    source = Path(directory)
    manifest = json.loads(
        (source / "manifest.json").read_text(encoding="utf-8")
    )
    embeddings = np.load(source / "embeddings.npy", allow_pickle=False)
    chunks = tuple(
        TextChunk(**json.loads(line))
        for line in (source / "chunks.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )

    if embeddings.ndim != 2:
        raise ValueError("Stored embeddings must be a two-dimensional matrix")
    if embeddings.shape[0] != len(chunks):
        raise ValueError("Stored embedding row count does not match chunk count")
    if embeddings.shape[1] != int(manifest["dimension"]):
        raise ValueError("Stored embedding dimension does not match manifest")
    if len(chunks) != int(manifest["chunk_count"]):
        raise ValueError("Stored chunk count does not match manifest")

    return DenseIndex(
        chunks=chunks,
        embeddings=np.asarray(embeddings, dtype=np.float32),
        model_name=str(manifest["model_name"]),
    )
