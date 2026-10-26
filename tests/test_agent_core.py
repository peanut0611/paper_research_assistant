"""The core can run a non-research tool agent without OpenAI, PDFs or RAG."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from paper_assistant.agent.core.contracts import ModelEstimate, ModelReply
from paper_assistant.agent.core.loop import AgentLoop, PreparedTurn, ToolInvocation
from paper_assistant.agent.core.messages import AgentSession, completed_exchanges, copy_json
from paper_assistant.agent.core.tools import AgentTool, ToolRegistry


class CalculatorModel:
    calls = 0

    def estimate(self, context):
        return ModelEstimate()

    def decide(self, context):
        self.calls += 1
        results = [message for message in context["messages"] if message["role"] == "tool"]
        if results:
            return ModelReply({"text": str(results[-1]["result"]["data"]["value"])})
        return ModelReply({"invoke": "double", "arguments": {"value": 3}})


class CalculatorHost:
    """A separate application implementing the host contract, without research imports."""
    def __init__(self, model):
        self.model, self.steps, self.status = model, 0, "ready"
        self.session = AgentSession([], [], now=lambda: datetime(2026, 10, 25, tzinfo=timezone.utc))
        self.session.append({"role": "user", "text": "Double three"})
        self.answer, self.saved = None, None

    def snapshot(self):
        return {"steps": self.steps, "status": self.status, "answer": self.answer,
                "messages": copy_json(self.session.messages)}

    def checkpoint(self):
        self.saved = self.snapshot()

    def is_done(self):
        return self.status == "stopped"

    def stop(self):
        self.status = "stopped"

    def prepare_request(self):
        self.steps += 1
        self.session.emit("turn_start", turn=self.steps)
        context = self.snapshot()
        return PreparedTurn(context, self.model.estimate(context))

    def model_response(self, reply, prepared):
        if "text" in reply.decision:
            self.answer, self.status = reply.decision["text"], "stopped"
            self.session.append({"role": "assistant", "text": self.answer, "tool_calls": []})
            return self.snapshot()
        call = reply.decision
        self.session.assistant("calculator-call", call["invoke"], call["arguments"], turn=self.steps)
        self.session.emit("tool_execution_start", turn=self.steps, call_id="calculator-call")
        return ToolInvocation(call["invoke"], call["arguments"], {})

    def model_error(self, error, prepared):
        self.status = "stopped"
        return self.snapshot()

    def tool_result(self, invocation, result):
        self.session.emit("tool_execution_end", turn=self.steps, call_id="calculator-call", success=True)
        self.session.tool_result("calculator-call", result, turn=self.steps)
        return self.snapshot()


def calculator():
    model = CalculatorModel()
    host = CalculatorHost(model)
    tools = ToolRegistry([AgentTool("double", "Double an integer", {"type": "object"},
                                   lambda args: {"success": True, "data": {"value": args["value"] * 2}})])
    return AgentLoop(model, tools, host, host.session), host, model


def test_core_runs_nonresearch_model_tool_result_and_final_message():
    loop, host, model = calculator()
    observed = []
    loop.subscribe(lambda event: observed.append(event["type"]))
    result = loop.run()
    assert result["answer"] == "6" and model.calls == 2
    assert [row["role"] for row in result["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert len(completed_exchanges(result["messages"])) == 1
    assert observed[0] == "agent_start" and observed[-1] == "agent_end"
    assert observed.index("tool_execution_start") < observed.index("tool_execution_end")
    assert result == host.saved


def test_core_pause_and_continue_do_not_repeat_completed_tool():
    loop, host, model = calculator()
    first = loop.run(max_steps=1)
    assert first["status"] == "ready" and first["answer"] is None
    assert host.session.events[-1]["type"] == "agent_pause"
    result = loop.run()
    assert result["answer"] == "6" and model.calls == 2
    assert len(completed_exchanges(result["messages"])) == 1


def test_core_cooperative_stop_keeps_current_result_without_followup_model_call():
    loop, host, model = calculator()
    loop.subscribe(lambda event: loop.request_stop() if event["type"] == "tool_execution_end" else None)
    result = loop.run()
    assert model.calls == 1 and result["answer"] is None
    assert len(completed_exchanges(result["messages"])) == 1 and result["status"] == "stopped"


def test_subscribers_cannot_mutate_state_and_failure_does_not_repeat_execution():
    loop, host, model = calculator()
    loop.subscribe(lambda event: event.update(type="mutated"))
    def broken(event):
        raise RuntimeError("An observer failure must not rerun tools")
    loop.subscribe(broken)
    result = loop.run()
    assert result["answer"] == "6" and model.calls == 2
    assert not any(event["type"] == "mutated" for event in host.session.events)


def test_registry_rejects_duplicate_names_and_unknown_tool_without_execution():
    loop, _, _ = calculator()
    with pytest.raises(ValueError, match="duplicate"):
        loop.tools.register(AgentTool("double", "duplicate", {}, lambda args: None))
    with pytest.raises(ValueError, match="not registered"):
        loop.tools.execute("arbitrary-tool", {})


def test_transcript_rejects_orphan_duplicate_results_and_duplicate_calls():
    session = AgentSession([], [], now=lambda: datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="matching"):
        session.tool_result("unknown", {"success": True})
    session.assistant("call", "double", {"value": 3})
    session.tool_result("call", {"success": True})
    with pytest.raises(ValueError, match="Duplicate"):
        session.tool_result("call", {"success": True})
    with pytest.raises(ValueError, match="Duplicate"):
        session.assistant("call", "double", {})


def test_importing_core_does_not_load_research_or_model_dependencies(tmp_path):
    project = Path(__file__).parents[1]
    script = "import sys; from paper_assistant.agent.core.loop import AgentLoop; assert not any(n in sys.modules for n in ('openai','numpy','torch','paper_assistant.agent.tools','paper_assistant.agent.workspace'))"
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(project / "src")}, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
