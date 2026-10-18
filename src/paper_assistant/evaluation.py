"""Source-aware retrieval evaluation independent of any embedding model."""

import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter

from paper_assistant.embedding import SearchResult


@dataclass(frozen=True)
class RelevantSource:
    document_id: str
    page_number: int
    evidence_quotes: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalQuestion:
    id: str
    group_id: str
    question: str
    language: str
    category: str
    answerable: bool
    answer: str | None
    relevant_sources: tuple[RelevantSource, ...]
    evidence_policy: str
    unanswerable_reason: str | None = None


def normalize_evidence(text: str) -> str:
    """Ignore Unicode variants and PDF whitespace, retaining punctuation."""
    return "".join(unicodedata.normalize("NFKC", text).split())


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_questions(path: str | Path) -> tuple[RetrievalQuestion, ...]:
    """Read the annotated JSONL, rejecting ambiguous or malformed labels."""
    questions: list[RetrievalQuestion] = []
    ids: set[str] = set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Question must be an object")
            for key in ("id", "group_id", "question", "language", "category"):
                if not _nonblank(row.get(key)):
                    raise ValueError(f"{key} must be a non-blank string")
            if row["id"] in ids:
                raise ValueError(f"Duplicate question id: {row['id']}")
            if type(row.get("answerable")) is not bool:
                raise ValueError("answerable must be a boolean")
            sources = row.get("relevant_sources")
            if not isinstance(sources, list):
                raise ValueError("relevant_sources must be a list")
            parsed: list[RelevantSource] = []
            seen_sources: set[tuple[str, int]] = set()
            for source in sources:
                if not isinstance(source, dict) or not _nonblank(source.get("document_id")):
                    raise ValueError("Each source needs a document_id")
                page = source.get("page_number")
                if type(page) is not int or page <= 0:
                    raise ValueError("page_number must be a positive integer")
                quotes = source.get("evidence_quotes")
                if not isinstance(quotes, list) or not quotes or not all(_nonblank(q) for q in quotes):
                    raise ValueError("Each source needs non-blank evidence_quotes")
                if len({normalize_evidence(q) for q in quotes}) != len(quotes):
                    raise ValueError("Duplicate evidence quote")
                key = (source["document_id"], page)
                if key in seen_sources:
                    raise ValueError("Duplicate relevant source")
                seen_sources.add(key)
                parsed.append(RelevantSource(key[0], page, tuple(quotes)))
            if row["answerable"]:
                if not parsed or not _nonblank(row.get("answer")) or row.get("evidence_policy") != "all":
                    raise ValueError("Answerable questions need sources, an answer, and evidence_policy=all")
            elif (parsed or row.get("answer") is not None or row.get("evidence_policy") != "none"
                  or not _nonblank(row.get("unanswerable_reason"))):
                raise ValueError("Unanswerable questions need no sources, null answer, policy=none, and a reason")
            questions.append(RetrievalQuestion(
                **{key: row[key] for key in ("id", "group_id", "question", "language", "category", "answerable", "answer", "evidence_policy")},
                relevant_sources=tuple(parsed),
                unanswerable_reason=row.get("unanswerable_reason"),
            ))
            ids.add(row["id"])
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    if not questions:
        raise ValueError("Evaluation dataset cannot be empty")
    group_answerability: dict[str, set[bool]] = defaultdict(set)
    for question in questions:
        group_answerability[question.group_id].add(question.answerable)
    if any(len(values) > 1 for values in group_answerability.values()):
        raise ValueError("A group cannot mix answerable and unanswerable questions")
    return tuple(questions)


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_metadata(dataset: str | Path, metadata_path: str | Path, project_root: str | Path) -> dict:
    """Reject changed annotation/source files instead of evaluating stale labels."""
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata.get("dataset_sha256") != file_sha256(dataset):
        raise ValueError("Dataset hash differs from metadata; review annotations and update metadata")
    sources = metadata.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Metadata must list source files and their hashes")
    for source in sources:
        path = Path(project_root) / source["path"]
        if file_sha256(path) != source["sha256"]:
            raise ValueError(f"Source hash differs from metadata: {path}")
    return metadata


def _quote_matches(source: RelevantSource, quote: str, result: SearchResult) -> bool:
    chunk = result.chunk
    return (
        (chunk.document_id, chunk.page_number) == (source.document_id, source.page_number)
        and normalize_evidence(quote) in normalize_evidence(chunk.text)
    )


def score_results(question: RetrievalQuestion, results: Sequence[SearchResult], ks: Sequence[int]) -> dict:
    """K counts ranked chunks; page recall deduplicates document/page pairs.

    Quotes must occur within a single chunk on the correct page. We never
    concatenate unrelated chunks to manufacture a matching passage.
    """
    if not question.answerable:
        return {"metrics": None, "missing_evidence": {}}
    relevant_pages = {(s.document_id, s.page_number) for s in question.relevant_sources}
    quotes = [(source, quote) for source in question.relevant_sources for quote in source.evidence_quotes]
    metrics: dict[str, float] = {}
    missing: dict[str, list[dict]] = {}
    for k in ks:
        top = results[:k]
        pages = {(r.chunk.document_id, r.chunk.page_number) for r in top}
        unmatched = [
            {"document_id": source.document_id, "page_number": source.page_number, "quote": quote}
            for source, quote in quotes
            if not any(_quote_matches(source, quote, result) for result in top)
        ]
        matched = len(quotes) - len(unmatched)
        metrics.update({
            f"page_hit@{k}": float(bool(pages & relevant_pages)),
            f"page_recall@{k}": len(pages & relevant_pages) / len(relevant_pages),
            f"evidence_hit@{k}": float(matched > 0),
            f"evidence_coverage@{k}": matched / len(quotes),
            f"complete_evidence@{k}": float(not unmatched),
        })
        missing[str(k)] = unmatched
    cutoff = max(ks)
    for kind in ("page", "evidence"):
        first = next((rank for rank, result in enumerate(results[:cutoff], 1) if (
            (result.chunk.document_id, result.chunk.page_number) in relevant_pages
            if kind == "page" else any(_quote_matches(s, q, result) for s, q in quotes)
        )), None)
        metrics[f"{kind}_mrr@{cutoff}"] = 1 / first if first else 0.0
    return {"metrics": metrics, "missing_evidence": missing}


def _average(rows: Sequence[dict]) -> dict[str, float]:
    return {key: mean(row[key] for row in rows) for key in rows[0]} if rows else {}


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_latency(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("Latency samples cannot be empty")
    return {"mean": mean(values), "p50": _percentile(values, 0.5),
            "p95": _percentile(values, 0.95), "max": max(values)}


def evaluate_retrieval(
    questions: Sequence[RetrievalQuestion],
    retrieve: Callable[[str, int], Sequence[SearchResult]],
    *,
    ks: Sequence[int] = (1, 3, 5),
    warmup: bool = True,
    clock: Callable[[], float] = perf_counter,
) -> dict:
    """Evaluate a retriever once per question, excluding warmup from timings."""
    if not questions:
        raise ValueError("Evaluation dataset cannot be empty")
    if not ks or any(type(k) is not int or k <= 0 for k in ks):
        raise ValueError("ks must contain positive integers")
    ks = tuple(sorted(set(ks)))
    if warmup:
        retrieve(questions[0].question, max(ks))
    rows = []
    for question in questions:
        start = clock()
        results = list(retrieve(question.question, max(ks)))
        latency = (clock() - start) * 1000
        if len(results) > max(ks):
            raise ValueError("Retriever returned more than requested top_k")
        if len({r.chunk.chunk_id for r in results}) != len(results):
            raise ValueError("Retriever returned duplicate chunk IDs")
        if any(not math.isfinite(r.score) for r in results):
            raise ValueError("Retriever returned non-finite scores")
        rows.append({
            **asdict(question), "latency_ms": latency,
            "returned_count": len(results),
            "results": [asdict(result) for result in results],
            **score_results(question, results, ks),
        })
    answerable = [row for row in rows if row["answerable"]]
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in answerable:
        groups[row["group_id"]].append(row["metrics"])
    latency = [row["latency_ms"] for row in rows]
    by_language = {}
    by_category = {}
    for field, target in (("language", by_language), ("category", by_category)):
        for value in sorted({row[field] for row in answerable}):
            subset = [row["metrics"] for row in answerable if row[field] == value]
            target[value] = {"question_count": len(subset), "metrics": _average(subset)}
    return {
        "schema_version": 1,
        "settings": {"ks": list(ks), "warmup_queries": int(warmup),
                     "timing_scope": "query embedding + scoring + ranking; excludes loading, warmup, metrics and serialization",
                     "evidence_matching": "NFKC + remove whitespace; within one chunk on the annotated document/page"},
        "summary": {
            "question_count": len(rows), "answerable_count": len(answerable),
            "unanswerable_count": len(rows) - len(answerable),
            "answerable_group_count": len(groups),
            "metrics": _average([row["metrics"] for row in answerable]),
            "group_macro_metrics": _average([_average(group) for group in groups.values()]),
            "by_language": by_language, "by_category": by_category,
            "latency_all_queries_ms": summarize_latency(latency),
        },
        "unanswerable": {"question_ids": [row["id"] for row in rows if not row["answerable"]],
                         "rejection_metrics": None, "reason": "Retriever has no abstention mechanism; inspect returned chunks separately"},
        "failures": [{"id": row["id"], "question": row["question"],
                      "incomplete_at_k": [k for k in ks if row["metrics"][f"complete_evidence@{k}"] < 1]}
                     for row in answerable if any(row["metrics"][f"complete_evidence@{k}"] < 1 for k in ks)],
        "questions": rows,
    }


def write_evaluation_report(report: dict, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return destination
