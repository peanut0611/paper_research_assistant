"""Fetch one registered arXiv PDF/version, or verify an existing local cache."""

import argparse
import json
import sys
from pathlib import Path

from paper_assistant.tools.arxiv.fetch import DEFAULT_PAPER_CACHE, PaperFetcher, PaperFetchSession, load_search_candidates


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-results", required=True, type=Path, help="Full JSON output of scripts/dev/search_literature.py")
    parser.add_argument("--arxiv-id", required=True, help="Base ID present in the selected search report")
    parser.add_argument("--version", type=int, help="Must match the report's pinned version; omitted uses that version")
    parser.add_argument("--cache-directory", type=Path, default=DEFAULT_PAPER_CACHE)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--offline", action="store_true", help="Validate the local cache only; no network")
    parser.add_argument("--output", type=Path, help="Optional JSON acquisition record")
    args = parser.parse_args()
    try:
        task, candidates = load_search_candidates(args.search_results, allow_injected=args.offline)
        session = PaperFetchSession(task, candidates, PaperFetcher(cache_directory=args.cache_directory))
        result = session.fetch(args.arxiv_id, version=args.version, offline=args.offline, timeout=args.timeout)
        payload = {**result.to_dict(), "search_results": str(args.search_results), "llm_calls": 0,
                   "indexed": False, "downloaded_papers": len(session.downloaded_assets)}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.status != "downloaded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
