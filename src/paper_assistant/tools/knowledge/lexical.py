"""BM25 and reciprocal-rank fusion over the same source-preserving chunks."""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from paper_assistant.tools.knowledge.chunking import TextChunk
from paper_assistant.tools.knowledge.embedding import DenseIndex, SearchResult, TextEmbedder, search_dense_index
from paper_assistant.tools.knowledge.scope import validate_document_scope


TOKENIZER_VERSION = "han-unigram-bigram-latin-word-v1"
BM25_STATISTICS_SCOPE = "entire_index_before_document_filter"
_HAN = r"\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f"
_HAN_GAP = re.compile(rf"(?<=[{_HAN}])\s+(?=[{_HAN}])")
_TERMS = re.compile(rf"[{_HAN}]+|[a-z0-9]+(?:[-_][a-z0-9]+)*")


def lexical_tokens(text: str) -> list[str]:
    """Dictionary-free bilingual lexical baseline, not linguistic segmentation.

    Join whitespace between Han characters to repair PDF layout artifacts.
    Emit Han characters and adjacent bigrams; lowercase Latin words/numbers.
    No translation, stemming, query expansion, or annotation-derived vocabulary.
    """
    text = _HAN_GAP.sub("", unicodedata.normalize("NFKC", text).lower())
    tokens: list[str] = []
    for match in _TERMS.finditer(text):
        term = match.group()
        if re.fullmatch(rf"[{_HAN}]+", term):
            tokens.extend(term)
            tokens.extend(term[index:index + 2] for index in range(len(term) - 1))
        else:
            tokens.append(term)
    return tokens


@dataclass(frozen=True)
class BM25Index:
    chunks: tuple[TextChunk, ...]
    term_counts: tuple[Counter[str], ...]
    document_frequencies: Counter[str]
    lengths: tuple[int, ...]
    average_length: float
    k1: float = 1.5
    b: float = 0.75


def build_bm25_index(chunks: Sequence[TextChunk], *, k1: float = 1.5, b: float = 0.75) -> BM25Index:
    if not chunks:
        raise ValueError("Cannot build BM25 without chunks")
    if not math.isfinite(k1) or k1 <= 0 or not math.isfinite(b) or not 0 <= b <= 1:
        raise ValueError("BM25 requires finite k1 > 0 and 0 <= b <= 1")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ValueError("BM25 chunk IDs must be unique")
    counts = tuple(Counter(lexical_tokens(chunk.text)) for chunk in chunks)
    frequencies: Counter[str] = Counter()
    for count in counts:
        frequencies.update(count.keys())
    lengths = tuple(sum(count.values()) for count in counts)
    return BM25Index(tuple(chunks), counts, frequencies, lengths, sum(lengths) / len(lengths), k1, b)


def search_bm25_index(index: BM25Index, query: str, *, top_k: int = 5,
                      document_ids: Sequence[str] | None = None) -> list[SearchResult]:
    """Positive-IDF BM25; scope limits candidates, corpus statistics stay fixed."""
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")
    if not query.strip():
        raise ValueError("Query cannot be blank")
    scope = validate_document_scope(index.chunks, document_ids)
    terms = set(lexical_tokens(query))
    if not terms or index.average_length == 0:
        return []
    scores = []
    n = len(index.chunks)
    for position, counts in enumerate(index.term_counts):
        if scope is not None and index.chunks[position].document_id not in scope:
            continue
        score = 0.0
        length_factor = index.k1 * (1 - index.b + index.b * index.lengths[position] / index.average_length)
        for term in sorted(terms):
            tf = counts.get(term, 0)
            if tf:
                df = index.document_frequencies[term]
                idf = math.log1p((n - df + 0.5) / (df + 0.5))
                score += idf * tf * (index.k1 + 1) / (tf + length_factor)
        if score > 0:
            scores.append((position, score))
    # Equal scores preserve original index order, making ties reproducible.
    scores.sort(key=lambda pair: -pair[1])
    return [SearchResult(rank, score, index.chunks[position])
            for rank, (position, score) in enumerate(scores[:top_k], 1)]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[SearchResult]], *, top_k: int = 5, constant: float = 60,
) -> list[SearchResult]:
    """Equal-weight RRF. Ties use first appearance (dense list supplied first)."""
    if top_k <= 0 or not math.isfinite(constant) or constant <= 0:
        raise ValueError("RRF requires top_k > 0 and finite constant > 0")
    chunks: dict[str, TextChunk] = {}
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen = set()
        for rank, result in enumerate(ranking, 1):
            chunk = result.chunk
            if chunk.chunk_id in seen:
                raise ValueError("A ranking cannot contain duplicate chunk IDs")
            seen.add(chunk.chunk_id)
            if chunk.chunk_id in chunks and chunks[chunk.chunk_id] != chunk:
                raise ValueError("Conflicting chunks share the same ID")
            chunks[chunk.chunk_id] = chunk
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (constant + rank)
    ordered = sorted(scores, key=lambda identifier: -scores[identifier])[:top_k]
    return [SearchResult(rank, scores[identifier], chunks[identifier]) for rank, identifier in enumerate(ordered, 1)]


def search_hybrid_index(
    dense: DenseIndex, lexical: BM25Index, query: str, embedder: TextEmbedder,
    *, top_k: int = 5, candidate_k: int = 20, rrf_constant: float = 60,
    document_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    if candidate_k < top_k or top_k <= 0:
        raise ValueError("Hybrid requires candidate_k >= top_k > 0")
    if dense.chunks != lexical.chunks:
        raise ValueError("Hybrid indexes must use identical chunks in identical order")
    return reciprocal_rank_fusion([
        search_dense_index(dense, query, embedder, top_k=candidate_k, document_ids=document_ids),
        search_bm25_index(lexical, query, top_k=candidate_k, document_ids=document_ids),
    ], top_k=top_k, constant=rrf_constant)
