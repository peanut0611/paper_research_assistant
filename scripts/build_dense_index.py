"""Ingest papers, chunk them, embed them, and save a dense index."""

import argparse
from pathlib import Path

from paper_assistant.chunking import chunk_pages_by_tokens
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
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--overlap-tokens", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default=None)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    report = ingest_pdf_directory(args.directory, recursive=args.recursive)
    embedder = SentenceTransformerEmbedder(
        args.model,
        batch_size=args.batch_size,
        device=args.device,
        show_progress_bar=True,
    )
    if args.max_tokens > embedder.max_sequence_length:
        parser.error("--max-tokens exceeds the embedding model input limit")
    chunks = chunk_pages_by_tokens(
        report.pages, embedder.tokenizer,
        max_tokens=args.max_tokens, overlap_tokens=args.overlap_tokens,
        document_prefix=embedder.document_prefix,
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
