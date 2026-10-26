"""Build or resume a task-scoped knowledge base from validated local PDF caches."""

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from paper_assistant.literature.fetcher import DEFAULT_PAPER_CACHE, PaperFetcher, load_search_candidates
from paper_assistant.agent.workspace import DEFAULT_RESEARCH_ROOT, ResearchWorkspace, WorkspaceConfig, WorkspaceError


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--search-results", required=True, type=Path)
    parser.add_argument("--arxiv-ids", required=True, nargs="+")
    parser.add_argument("--root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--paper-cache", type=Path, default=DEFAULT_PAPER_CACHE)
    parser.add_argument("--model", default=WorkspaceConfig.model_name)
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--overlap-tokens", type=int, default=64)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--parse-timeout", type=float, default=45)
    parser.add_argument("--target-papers", type=int, default=3)
    parser.add_argument("--question", action="append", help="Research questions, repeatable; fixed for this task ID")
    parser.add_argument("--local-models-only", action="store_true")
    parser.add_argument("--query", help="Optional retrieval smoke check after ingestion; does not call LLM")
    parser.add_argument("--method", choices=("dense", "bm25", "hybrid"), default="hybrid")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", action="store_true", help="Print task status/counts; --output still saves the full result")
    args = parser.parse_args()
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        task, candidates = load_search_candidates(args.search_results)
        task = replace(task, target_papers=args.target_papers,
                       research_questions=tuple(args.question) if args.question else task.research_questions)
        if not set(args.arxiv_ids) <= {row.arxiv_id for row in candidates}:
            parser.error("Requested IDs must be registered in the search report")
        config = WorkspaceConfig(args.model, args.max_tokens, args.overlap_tokens, args.max_pages,
                                 parse_timeout_seconds=args.parse_timeout)
        workspace = ResearchWorkspace(args.task_id, task, candidates, root=args.root,
            fetcher=PaperFetcher(cache_directory=args.paper_cache), config=config)
        events = [workspace.ingest_paper(identifier) for identifier in dict.fromkeys(args.arxiv_ids)]
        payload = {"task_id": args.task_id, "status": "ready" if all(e["status"] == "fulltext_ready" for e in events) else "partial_or_failed",
                   "events": events, "documents": workspace.list_documents(),
                   "workspace_manifest": str(workspace.manifest_path),
                   "index_directory": str(workspace.index_directory) if workspace.index_directory else None,
                   "llm_calls": 0, "pdf_network_requests": workspace.fetcher.network_requests}
        if args.query:
            payload["retrieval"] = workspace.search_documents(args.query, method=args.method)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except WorkspaceError as error:
        print(json.dumps({"status": "error", "code": error.code, "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from None
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.summary:
        printed = {key: payload[key] for key in ("task_id", "status", "events", "workspace_manifest", "index_directory", "llm_calls", "pdf_network_requests")}
        printed["documents"] = [{key: row[key] for key in ("arxiv_id", "version", "status", "page_count", "blank_pages", "last_error")} for row in payload["documents"]]
        if "retrieval" in payload:
            printed["retrieved_chunk_ids"] = [row["chunk"]["chunk_id"] for row in payload["retrieval"]["results"]]
    else:
        printed = payload
    print(json.dumps(printed, ensure_ascii=False, indent=2))
    if payload["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
