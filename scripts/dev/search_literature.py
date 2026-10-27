"""Search arXiv metadata in an explicit first-submission time window; no PDF or LLM calls."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from paper_assistant.tools.arxiv.search import (
    DEFAULT_CACHE, SORT_FIELDS, ArxivSearchClient, LiteratureSearchError, LiteratureSearchSession,
)
from paper_assistant.research.task import ResearchTask


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Plain text, preferably English; double quotes group phrases")
    parser.add_argument("--start", required=True, help="Inclusive ISO 8601 timestamp with UTC offset")
    parser.add_argument("--end", required=True, help="Exclusive ISO 8601 timestamp with UTC offset")
    parser.add_argument("--max-results", type=int, default=5, help="First round: at most 5 entries")
    parser.add_argument("--sort-by", choices=SORT_FIELDS, default="submittedDate")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--cache-directory", type=Path, default=DEFAULT_CACHE / "snapshots")
    parser.add_argument("--offline", action="store_true", help="Replay only an exact cached request, even if expired")
    parser.add_argument("--summary", action="store_true", help="Print a compact result; --output still saves the full JSON")
    parser.add_argument("--output", type=Path, help="Optionally save normalized candidates and provenance as JSON")
    args = parser.parse_args()
    try:
        task = ResearchTask(args.query, (args.query,), datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
        session = LiteratureSearchSession(task, ArxivSearchClient(cache_directory=args.cache_directory))
        result = session.search(args.query, max_results=args.max_results, sort_by=args.sort_by,
                                offline=args.offline, timeout=args.timeout)
        payload = {**result.to_dict(), "discovery_usage": session.history,
                   "llm_calls": 0, "pdf_fetch_attempts": 0}
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except LiteratureSearchError as error:
        print(json.dumps({"status": "error", "code": error.code, "http_status": error.status,
                          "message": str(error), "llm_calls": 0, "pdf_fetch_attempts": 0}), flush=True)
        raise SystemExit(1) from None
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.summary:
        payload = {"status": "success", "candidates": [
            {"arxiv_id": c.arxiv_id, "version": c.version, "title": c.title,
             "submitted_at": c.submitted_at.isoformat(), "abstract_url": c.abstract_url} for c in result.candidates
        ], "returned_candidates": len(result.candidates), "observed_candidates": len(result.observed_candidates),
            "total_results": result.total_results, "from_cache": result.from_cache,
            "stale_cache": result.stale_cache, "snapshot_origin": result.snapshot_origin,
            "snapshot_path": result.snapshot_path, "output": str(args.output) if args.output else None,
            "llm_calls": 0, "pdf_fetch_attempts": 0}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
