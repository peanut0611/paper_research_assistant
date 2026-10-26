"""Run real OpenAI research decisions with bounded source tools and a task budget."""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from paper_assistant.agent.model import OpenAIDecisionPolicy, OpenAIModelConfig
from paper_assistant.agent.runner import AgentRunner
from paper_assistant.agent.state import AgentState, RunnerConfig, json_bytes
from paper_assistant.agent.tools import ResearchTools
from paper_assistant.literature.search import PaperCandidate
from paper_assistant.literature.fetcher import load_search_candidates
from paper_assistant.agent.task import ResearchLimits, ResearchTask
from paper_assistant.agent.workspace import DEFAULT_RESEARCH_ROOT, ResearchWorkspace, WorkspaceConfig
from paper_assistant.storage import atomic_write_bytes


def summarize(state, directory):
    checked = next((row["result"]["data"] for row in reversed(state["observations"])
                    if row["tool"] == "verify_citations" and row["result"]["success"]), None)
    return {"mode": "openai_research_decisions", "task_id": state["binding"]["task_id"], "status": state["status"],
        "stop_reason": state["stop_reason"], "summary": state["summary"], "steps": state["steps"],
        "usage": state["usage"], "usage_cost_note": "Estimated USD from returned API tokens and recorded standard rates; unknown calls charged at reservation",
        "openai_requests_attempted": sum(bool(row.get("metadata", {}).get("request_submitted")) for row in state["model_events"]),
        "tool_limits": state["tool_limits"], "evidence_answer": checked, "last_error": state["last_error"],
        "state": str(directory / "runner_state.json"), "trace": str(directory / "tool_trace.json"),
        "runtime": "message_agent_loop", "message_count": len(state["messages"]), "agent_event_count": len(state["agent_events"]),
        "report_status": "formal_research_report_not_implemented"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--topic")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--question", action="append")
    parser.add_argument("--target-papers", type=int)
    parser.add_argument("--max-cost-usd", type=float, help="Task budget, default 0.10; must be above report reserve")
    parser.add_argument("--report-reserve-usd", type=float)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-model-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--ignore-env-proxy", action="store_true")
    parser.add_argument("--seed-search-results", type=Path, help="Trusted full arXiv search report; candidates still need model selection")
    parser.add_argument("--root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--offline-sources", action="store_true", help="Only arXiv/PDF caches; OpenAI still requires network and charges tokens")
    parser.add_argument("--local-models-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-on-request", action="store_true", help="Stop before the next step when task stop.request exists")
    parser.add_argument("--steps", type=int, help="Checkpoint after N decisions; cumulative limits and deadline remain")
    args = parser.parse_args()
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    policy = None
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.task_id):
            parser.error("Invalid task ID")
        directory = args.root / args.task_id
        candidates = ()
        workspace_config = WorkspaceConfig()
        model_config = OpenAIModelConfig(max_output_tokens=args.max_output_tokens if args.max_output_tokens is not None else 1200,
                                         trust_env=not args.ignore_env_proxy)
        model_path = directory / "model_config.json"
        if args.resume:
            if any(value is not None for value in (args.topic, args.start, args.end, args.question, args.target_papers,
                args.max_cost_usd, args.report_reserve_usd, args.max_steps, args.max_model_tokens, args.max_output_tokens, args.seed_search_results)):
                parser.error("Resume reuses the saved task/model/budgets; omit scope and budget overrides")
            state = AgentState.load(directory / "runner_state.json")
            task = ResearchTask.from_dict(state.binding["task"])
            runner_config = RunnerConfig(**state.binding["config"])
            saved_model = json.loads(model_path.read_bytes())
            model_config = OpenAIModelConfig(**saved_model)
            if args.ignore_env_proxy and model_config.trust_env:
                parser.error("Resume must retain its original proxy mode")
            manifest = json.loads((directory / "workspace.json").read_bytes())
            workspace_config = WorkspaceConfig(**manifest["config"])
            if (directory / "tool_trace.json").exists():
                checkpoint = ResearchTools.read_checkpoint(directory / "tool_trace.json")["checkpoint"]
                candidates = tuple(PaperCandidate.from_dict(row) for row in checkpoint["registered_candidates"])
            elif args.seed_search_results:
                parser.error("Resume cannot register new candidates")
            else:
                # A first-step decision can be interrupted before any tool journal exists.
                saved_candidates = json.loads((directory / "registered_candidates.json").read_bytes())
                candidates = tuple(PaperCandidate.from_dict(row) for row in saved_candidates)
        else:
            if not all((args.topic, args.start, args.end)):
                parser.error("New tasks require --topic/--start/--end")
            limits = ResearchLimits(max_model_cost_usd=args.max_cost_usd if args.max_cost_usd is not None else 0.10,
                                    report_reserve_usd=args.report_reserve_usd if args.report_reserve_usd is not None else 0.02)
            task = ResearchTask(args.topic, tuple(args.question or [args.topic]), datetime.fromisoformat(args.start),
                datetime.fromisoformat(args.end), target_papers=args.target_papers if args.target_papers is not None else 1, limits=limits)
            runner_config = RunnerConfig(max_steps=args.max_steps if args.max_steps is not None else 12,
                                        max_model_tokens=args.max_model_tokens if args.max_model_tokens is not None else 60000)
            if args.seed_search_results:
                _, candidates = load_search_candidates(args.seed_search_results)
            if (directory / "runner_state.json").exists() or (directory / "tool_trace.json").exists():
                parser.error("Task already has execution state; use --resume or a new task ID")
        # Fail on missing/invalid local key before creating any new task or making an HTTP request.
        policy = OpenAIDecisionPolicy(config=model_config)
        workspace = ResearchWorkspace(args.task_id, task, candidates, root=args.root, config=workspace_config)
        runtime = ResearchTools(workspace, offline=args.offline_sources,
            resume=args.resume and (directory / "tool_trace.json").exists())
        if not args.resume:
            atomic_write_bytes(model_path, json_bytes(asdict(model_config)))
            atomic_write_bytes(directory / "registered_candidates.json", json_bytes([row.to_dict() for row in candidates]))
        runner = AgentRunner(runtime, policy, config=runner_config, resume=args.resume)
        result = runner.run(max_steps_per_run=args.steps,
            stop_requested=(lambda: (directory / "stop.request").is_file()) if args.stop_on_request else None)
        summary = summarize(result, directory)
        atomic_write_bytes(directory / "result.json", json_bytes(summary))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    finally:
        if policy is not None:
            policy.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result["last_error"] or result["stop_reason"] in ("model_estimate_error", "model_error_unknown_usage", "model_usage_exceeded_reservation"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
