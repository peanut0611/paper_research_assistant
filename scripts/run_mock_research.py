"""Exercise the runner with an explicit historical mock policy, never an LLM."""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from paper_assistant.agent.runner import AgentRunner, MockPolicy
from paper_assistant.agent.state import AgentState, RunnerConfig
from paper_assistant.agent.tools import ResearchTools
from paper_assistant.literature.search import PaperCandidate
from paper_assistant.agent.task import ResearchTask
from paper_assistant.agent.workspace import DEFAULT_RESEARCH_ROOT, ResearchWorkspace, WorkspaceConfig


def smoke_policy(arxiv_id):
    """Fixed candidate exercises state transitions, not relevance selection."""
    def choose(view):
        rows = view["candidates"]
        candidate = next((row for row in rows if row["arxiv_id"] == arxiv_id), None)
        if not view["queries"] and candidate is None:
            return {"kind": "tool", "tool": "search_literature", "arguments": {
                "query": view["binding"]["task"]["topic"], "max_results": 3}, "purpose": "查询固定历史窗口的元数据"}
        if candidate is None:
            return {"kind": "finish", "reason": "insufficient_evidence", "summary": "固定冒烟候选未出现；保留已取得的元数据。"}
        prior = [row for row in view["observations"] if row["tool"] in ("fetch_paper", "ingest_paper") and not row["result"]["success"]]
        if prior:
            return {"kind": "finish", "reason": "insufficient_evidence", "summary": "冒烟候选获取或入库失败，未生成研究结论。"}
        if candidate["source_sha256"] is None:
            return {"kind": "tool", "tool": "fetch_paper", "arguments": {"arxiv_id": arxiv_id},
                    "purpose": "获取固定候选版本", "updates": {"selections": [
                        {"arxiv_id": arxiv_id, "selected": True, "reason": "固定历史冒烟候选，不代表模型相关性筛选"}],
                        "gaps": ["全文尚未入库"]}}
        if candidate["status"] != "fulltext_ready":
            return {"kind": "tool", "tool": "ingest_paper", "arguments": {"arxiv_id": arxiv_id}, "purpose": "建立独立任务索引"}
        if not view["evidence"]:
            if any(row["tool"] == "search_documents" for row in view["observations"]):
                return {"kind": "finish", "reason": "insufficient_evidence", "summary": "检索未返回正文证据。"}
            return {"kind": "tool", "tool": "search_documents", "arguments": {
                "query": "What is RAGTruth?", "method": "hybrid", "top_k": 3,
                "document_ids": [candidate["document_id"]]}, "purpose": "验证检索证据回传"}
        if not any(row["tool"] == "verify_citations" for row in view["observations"]):
            identifier, evidence = next(iter(view["evidence"].items()))
            return {"kind": "tool", "tool": "verify_citations", "arguments": {
                "answer": f"工具冒烟原文摘录 [{identifier}]", "insufficient_evidence": False,
                "citations": [{"evidence_id": identifier, "quote": evidence["text"][:120]}]},
                    "purpose": "核对实际检索片段的编号和原文", "updates": {"gaps": []}}
        return {"kind": "finish", "reason": "sufficient_evidence", "summary": "模拟策略已完成工具和引用检查；未生成研究报告或评价论文结论。"}
    return MockPolicy(choose, policy_id=f"fixed-historical-smoke-v1:{arxiv_id}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--topic")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--paper-id", default="2401.00396")
    parser.add_argument("--root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--steps", type=int, help="Checkpoint after this many decisions without resetting cumulative steps")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--local-models-only", action="store_true")
    args = parser.parse_args()
    if args.local_models_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        # The task ID is never a path supplied by the policy.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.task_id):
            parser.error("Invalid task ID")
        directory = args.root / args.task_id
        candidates, config = (), RunnerConfig()
        workspace_config = WorkspaceConfig()
        if args.resume:
            if any((args.topic, args.start, args.end)):
                parser.error("Resume uses the saved task; omit --topic/--start/--end")
            state = AgentState.load(directory / "runner_state.json")
            task = ResearchTask.from_dict(state.binding["task"])
            config = RunnerConfig(**state.binding["config"])
            manifest = json.loads((directory / "workspace.json").read_bytes())
            workspace_config = WorkspaceConfig(**manifest["config"])
            if (directory / "tool_trace.json").exists():
                checkpoint = ResearchTools.read_checkpoint(directory / "tool_trace.json")["checkpoint"]
                candidates = tuple(PaperCandidate.from_dict(row) for row in checkpoint["registered_candidates"])
        else:
            if not all((args.topic, args.start, args.end)):
                parser.error("New runs require --topic/--start/--end")
            task = ResearchTask(args.topic, ("What is RAGTruth?",), datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
        workspace = ResearchWorkspace(args.task_id, task, candidates, root=args.root, config=workspace_config)
        runtime = ResearchTools(workspace, offline=args.offline, resume=args.resume and (directory / "tool_trace.json").exists())
        runner = AgentRunner(runtime, smoke_policy(args.paper_id), config=config, resume=args.resume)
        result = runner.run(max_steps_per_run=args.steps)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"mode": "mock_policy_not_llm_agent", "task_id": args.task_id, "status": result["status"],
        "stop_reason": result["stop_reason"], "steps": result["steps"], "tool_calls": result["tool_limits"]["tool_calls"],
        "simulated_usage": result["usage"], "llm_calls": 0, "state": str(runner.path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
