"""Ingest papers, chunk them, embed them, and save a dense index."""

import argparse
from pathlib import Path

from paper_assistant.chunking import chunk_pages
from paper_assistant.corpus_ingestion import ingest_pdf_directory
from paper_assistant.embedding import (
    DEFAULT_MODEL_NAME,
    SentenceTransformerEmbedder,
    build_dense_index,
    save_dense_index,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("paper"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/index/dense"),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--max-words", type=int, default=180)
    parser.add_argument("--overlap-words", type=int, default=36)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default=None)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    report = ingest_pdf_directory(args.directory, recursive=args.recursive)
    chunks = chunk_pages(
        report.pages,
        max_words=args.max_words,
        overlap_words=args.overlap_words,
    )
    embedder = SentenceTransformerEmbedder(
        args.model,
        batch_size=args.batch_size,
        device=args.device,
        show_progress_bar=True,
    )
    index = build_dense_index(chunks, embedder)
    output = save_dense_index(index, args.output)

    print(f"Model: {index.model_name}")
    print(f"Chunks: {len(index.chunks)}")
    print(f"Dimensions: {index.dimension}")
    print(f"Output: {output}")
    print(f"Failed PDFs: {len(report.failures)}")


if __name__ == "__main__":
    main()
