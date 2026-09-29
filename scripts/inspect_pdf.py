"""Run the PDF parser and print a short preview for manual inspection."""

import argparse
from pathlib import Path

from paper_assistant.pdf_parser import parse_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Path to the PDF file")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Show text before cleanup",
    )
    args = parser.parse_args()

    pages = parse_pdf(args.pdf, clean=not args.raw)
    mode = "raw" if args.raw else "cleaned"
    print(f"Extracted {len(pages)} pages from {args.pdf.name} ({mode})\n")

    for page in pages:
        preview = " ".join(page.text.split())
        print(f"--- Page {page.page_number} ---")
        print(preview or "[No extractable text]")
        print()


if __name__ == "__main__":
    main()
