"""Search a saved dense chunk index with a natural-language query."""

import argparse
from pathlib import Path

from paper_assistant.embedding import (
    SentenceTransformerEmbedder,
    load_dense_index,
    search_dense_index,
)


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
    args = parser.parse_args()

    index = load_dense_index(args.index)
    embedder = SentenceTransformerEmbedder(index.model_name, device=args.device)
    results = search_dense_index(index, args.query, embedder, top_k=args.top_k)

    print(f"Query: {args.query}\n")
    for result in results:
        preview = result.chunk.text[:350].replace("\n", " ")
        print(
            f"#{result.rank} score={result.score:.4f} "
            f"page={result.chunk.page_number} "
            f"chunk={result.chunk.chunk_index}"
        )
        print(preview)
        print()


if __name__ == "__main__":
    main()

