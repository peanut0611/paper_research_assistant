"""Ingest a directory of PDFs and export page-level JSONL records."""

import argparse
from pathlib import Path

from paper_assistant.corpus_ingestion import (
    ingest_pdf_directory,
    write_pages_jsonl,
)


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
        default=Path("data/extracted/pages.jsonl"),
        help="JSONL destination (default: data/extracted/pages.jsonl)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Also scan nested directories",
    )
    args = parser.parse_args()

    report = ingest_pdf_directory(args.directory, recursive=args.recursive)
    output_path = write_pages_jsonl(report.pages, args.output)

    print(f"Processed PDFs: {len(report.processed_files)}")
    print(f"Extracted pages: {len(report.pages)}")
    print(f"Blank pages: {len(report.blank_pages)}")
    print(f"Failed PDFs: {len(report.failures)}")
    print(f"Output: {output_path}")

    for failure in report.failures:
        print(f"FAILED {failure.filename}: {failure.error}")

    if not report.processed_files and report.failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

