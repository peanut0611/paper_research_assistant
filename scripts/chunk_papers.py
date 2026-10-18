"""Ingest papers, create token-window chunks, and export JSONL."""

import argparse
from pathlib import Path
from statistics import mean

from paper_assistant.chunking import chunk_pages_by_tokens, write_chunks_jsonl
from paper_assistant.corpus_ingestion import ingest_pdf_directory
from paper_assistant.embedding import DEFAULT_MODEL_NAME, SentenceTransformerEmbedder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("paper"),
        help="Directory containing PDFs (default: paper)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/chunks/chunks.jsonl"),
        help="Chunk JSONL destination (default: data/chunks/chunks.jsonl)",
    )
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--overlap-tokens", type=int, default=64)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    report = ingest_pdf_directory(args.directory, recursive=args.recursive)
    embedder = SentenceTransformerEmbedder(args.model)
    if args.max_tokens > embedder.max_sequence_length:
        parser.error("--max-tokens exceeds the embedding model input limit")
    chunks = chunk_pages_by_tokens(
        report.pages, embedder.tokenizer,
        max_tokens=args.max_tokens, overlap_tokens=args.overlap_tokens,
        document_prefix=embedder.document_prefix,
    )
    output_path = write_chunks_jsonl(chunks, args.output)

    token_counts = [chunk.token_count for chunk in chunks]
    print(f"Processed PDFs: {len(report.processed_files)}")
    print(f"Extracted pages: {len(report.pages)}")
    print(f"Created chunks: {len(chunks)}")
    if token_counts:
        print(
            "Chunk tokens (including prefix and special tokens): "
            f"min={min(token_counts)}, "
            f"avg={mean(token_counts):.1f}, "
            f"max={max(token_counts)}"
        )
    print(f"Failed PDFs: {len(report.failures)}")
    print(f"Output: {output_path}")

    for failure in report.failures:
        print(f"FAILED {failure.filename}: {failure.error}")


if __name__ == "__main__":
    main()
