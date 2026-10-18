"""Search saved chunks using Dense, BM25, or Hybrid retrieval."""

import argparse
import os
from pathlib import Path

from paper_assistant.embedding import (
    SentenceTransformerEmbedder,
    load_dense_index,
    search_dense_index,
)
from paper_assistant.lexical_retrieval import build_bm25_index, search_bm25_index, search_hybrid_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument(
        "--index",
        type=Path,
        default=Path("data/index/dense"),
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--device", default=None)
    parser.add_argument("--method", choices=["dense", "bm25", "hybrid"], default="dense")
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rrf-constant", type=float, default=60)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    index = load_dense_index(args.index)
    if args.method == "bm25":
        results = search_bm25_index(build_bm25_index(index.chunks), args.query, top_k=args.top_k)
    else:
        embedder = SentenceTransformerEmbedder(index.model_name, device=args.device)
        if args.method == "hybrid":
            results = search_hybrid_index(index, build_bm25_index(index.chunks), args.query, embedder,
                                          top_k=args.top_k, candidate_k=args.candidate_k, rrf_constant=args.rrf_constant)
        else:
            results = search_dense_index(index, args.query, embedder, top_k=args.top_k)

    print(f"Method: {args.method}; Query: {args.query}\n")
    if not results:
        print("No lexical matches. This is not an evidence-based abstention decision.")
    for result in results:
        preview = result.chunk.text[:350].replace("\n", " ")
        print(
            f"#{result.rank} score={result.score:.4f} "
            f"file={result.chunk.filename} "
            f"page={result.chunk.page_number} "
            f"chunk={result.chunk.chunk_index}"
        )
        print(preview)
        print()


if __name__ == "__main__":
    main()

