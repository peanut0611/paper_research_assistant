"""Evaluate dense retrieval against source-annotated JSONL questions."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from paper_assistant.embedding import SentenceTransformerEmbedder, SearchResult, load_dense_index, search_dense_index
from paper_assistant.evaluation import (
    evaluate_retrieval, file_sha256, load_questions, score_results,
    verify_metadata, write_evaluation_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=Path("eval_data/retrieval_questions.jsonl"))
    parser.add_argument("--metadata", type=Path, help="Default: question file's .meta.json sidecar, if present")
    parser.add_argument("--index", type=Path, default=Path("data/index/dense"))
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/retrieval_report.json"))
    parser.add_argument("--ks", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--device", default=None)
    parser.add_argument("--offline", action="store_true", help="Load the model only from local cache")
    args = parser.parse_args()
    if any(k <= 0 for k in args.ks):
        parser.error("--ks must contain positive integers")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        questions = load_questions(args.questions)
        metadata_path = args.metadata or args.questions.with_suffix(".meta.json")
        metadata = None
        if args.metadata or metadata_path.exists():
            metadata = verify_metadata(args.questions, metadata_path, Path.cwd())
        index = load_dense_index(args.index)
        protected = [args.questions, metadata_path] + [args.index / name for name in ("manifest.json", "chunks.jsonl", "embeddings.npy")]
        if metadata:
            protected.extend(Path(source["path"]) for source in metadata["sources"])
        if args.output.resolve() in {path.resolve() for path in protected}:
            raise ValueError("Report output cannot overwrite annotation, source, or index files")
        if not index.chunks:
            raise ValueError("Cannot evaluate an empty index")
        index_pages = {(chunk.document_id, chunk.page_number) for chunk in index.chunks}
        for question in questions:
            for source in question.relevant_sources:
                if (source.document_id, source.page_number) not in index_pages:
                    raise ValueError(f"{question.id}: annotated source page is absent from the index")
        all_chunks = [SearchResult(rank, 0.0, chunk) for rank, chunk in enumerate(index.chunks, 1)]
        unavailable = []
        for question in questions:
            if question.answerable:
                missing = score_results(question, all_chunks, [len(all_chunks)])["missing_evidence"][str(len(all_chunks))]
                if missing:
                    unavailable.append({"id": question.id, "evidence_not_contained_in_any_chunk": missing})
        embedder = SentenceTransformerEmbedder(index.model_name, device=args.device)
        report = evaluate_retrieval(
            questions, lambda query, k: search_dense_index(index, query, embedder, top_k=k), ks=args.ks,
        )
        report["run"] = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "questions_path": str(args.questions), "questions_sha256": file_sha256(args.questions),
            "annotation_metadata": metadata,
            "index_path": str(args.index), "model_name": index.model_name,
            "dimension": index.dimension, "chunk_count": len(index.chunks),
            "index_manifest": json.loads((args.index / "manifest.json").read_text(encoding="utf-8")),
            "index_file_sha256": {name: file_sha256(args.index / name) for name in ("manifest.json", "chunks.jsonl", "embeddings.npy")},
            "device": embedder.device,
            "offline": args.offline,
        }
        report["diagnostics"] = {
            "evidence_unavailable_in_index": unavailable,
            "notes": [
                "K counts chunks, not unique pages; K greater than the chunk count returns the whole index.",
                "Missing exact evidence can indicate a chunk boundary or an equivalent unannotated passage; review before attributing it to ranking.",
                "The current index manifest does not record build chunking parameters or source hashes; file hashes identify this index snapshot, not its source provenance.",
            ],
        }
        destination = write_evaluation_report(report, args.output)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Evaluation failed: {error}\n")

    summary = report["summary"]
    print(f"Questions: {summary['question_count']} (answerable={summary['answerable_count']}, unanswerable={summary['unanswerable_count']})")
    print(f"Model: {index.model_name}; chunks: {len(index.chunks)}")
    for key, value in summary["metrics"].items():
        print(f"{key}: {value:.4f}")
    print(f"Query latency ms: {summary['latency_all_queries_ms']}")
    if max(args.ks) >= len(index.chunks):
        print("NOTE: largest K covers the entire index; focus on smaller K and evidence coverage.")
    for failure in report["failures"]:
        print(f"INCOMPLETE {failure['id']} at K={failure['incomplete_at_k']}: {failure['question']}")
    print(f"Evidence unavailable in any single indexed chunk: {len(unavailable)} questions")
    print(f"Report: {destination}")


if __name__ == "__main__":
    main()
