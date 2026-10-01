"""Ingest papers, create word-window chunks, and export JSONL."""

import argparse
from pathlib import Path
from statistics import mean

from paper_assistant.chunking import chunk_pages, write_chunks_jsonl
from paper_assistant.corpus_ingestion import ingest_pdf_directory


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
    parser.add_argument("--max-words", type=int, default=180)
    parser.add_argument("--overlap-words", type=int, default=36)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    report = ingest_pdf_directory(args.directory, recursive=args.recursive)
    chunks = chunk_pages(
        report.pages,
        max_words=args.max_words,
        overlap_words=args.overlap_words,
    )
    output_path = write_chunks_jsonl(chunks, args.output)

    word_counts = [chunk.word_count for chunk in chunks]
    print(f"Processed PDFs: {len(report.processed_files)}")
    print(f"Extracted pages: {len(report.pages)}")
    print(f"Created chunks: {len(chunks)}")
    if word_counts:
        print(
            "Chunk words: "
            f"min={min(word_counts)}, "
            f"avg={mean(word_counts):.1f}, "
            f"max={max(word_counts)}"
        )
    print(f"Failed PDFs: {len(report.failures)}")
    print(f"Output: {output_path}")

    for failure in report.failures:
        print(f"FAILED {failure.filename}: {failure.error}")


if __name__ == "__main__":
    main()
