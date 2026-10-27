"""Run an explicitly scripted tool sequence, or replay saved observations without actions.

This is a tool integration harness, not an autonomous Agent or model decision.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from paper_assistant.research.toolset import RecordedToolReplay, ResearchTools
from paper_assistant.tools.arxiv.fetch import DEFAULT_PAPER_CACHE, PaperFetcher
from paper_assistant.research.task import ResearchTask
from paper_assistant.research.workspace import DEFAULT_RESEARCH_ROOT, ResearchWorkspace


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calls", type=Path, help="JSONL {tool,arguments,purpose?}; no arbitrary Python or shell")
    mode.add_argument("--replay", type=Path, help="Recorded tool_trace.json; returns observations only")
    parser.add_argument("--task-id")
    parser.add_argument("--topic")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--question", action="append")
    parser.add_argument("--root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--paper-cache", type=Path, default=DEFAULT_PAPER_CACHE)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--local-models-only", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional copy of the sanitized trace")
    args = parser.parse_args()
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        if args.replay:
            if args.replay.stat().st_size > 8_000_000:
                parser.error("Recorded trace is too large")
            trace = json.loads(args.replay.read_bytes())
            replay = RecordedToolReplay(trace)
            results = [replay.call(event["tool"], event["arguments"]) for event in trace["events"]]
            mode_name = "recorded_observation_replay_no_actions"
            journal = str(args.replay)
        else:
            if not all((args.task_id, args.topic, args.start, args.end)):
                parser.error("Execution requires --task-id, --topic, --start and --end")
            if args.calls.stat().st_size > 100000:
                parser.error("Call file is too large")
            calls = [json.loads(line) for line in args.calls.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            if not calls or len(calls) > 24 or any(not isinstance(call, dict) or set(call) - {"tool", "arguments", "purpose"}
                    or not {"tool", "arguments"} <= call.keys() for call in calls):
                parser.error("Provide 1..24 structured tool calls")
            task = ResearchTask(args.topic, tuple(args.question or [args.topic]),
                                datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
            workspace = ResearchWorkspace(args.task_id, task, (), root=args.root,
                                          fetcher=PaperFetcher(cache_directory=args.paper_cache))
            runtime = ResearchTools(workspace, offline=args.offline)
            results = []
            for call in calls:
                result = runtime.call(call["tool"], call["arguments"], purpose=call.get("purpose", ""))
                results.append(result)
                if result["error"] and result["error"]["code"] == "audit_storage_error":
                    break
            trace = runtime.export_trace()
            mode_name, journal = "explicit_script_not_autonomous", str(runtime.journal_path)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"mode": mode_name, "task_id": trace["task_id"], "event_count": len(trace["events"]),
        "journal": journal, "llm_calls": 0,
        "results": [{"event_id": result["event_id"], "tool": event["tool"], "success": result["success"],
                     "error": result["error"]} for event, result in zip(trace["events"], results)]}, ensure_ascii=False, indent=2))
    if any(not result["success"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
