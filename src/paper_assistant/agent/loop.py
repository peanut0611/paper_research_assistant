"""One sequential agent loop; applications supply persistence, budgets and finish rules."""
from dataclasses import dataclass
from threading import Event, Lock, RLock
from typing import Protocol

from paper_assistant.ai.types import ModelReply, ModelUsage


@dataclass(frozen=True)
class PreparedTurn:
    context: dict
    reservation: object


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    arguments: dict
    context: dict


class LoopHost(Protocol):
    def prepare_request(self): ...
    def model_response(self, reply, prepared): ...
    def model_error(self, error, prepared): ...
    def tool_result(self, invocation, result): ...
    def checkpoint(self): ...
    def snapshot(self): ...
    def is_done(self): ...
    def stop(self): ...


class AgentLoop:
    def __init__(self, model, tools, host: LoopHost, session):
        self.model, self.tools, self.host, self.session = model, tools, host, session
        self._abort = Event()
        self._step_lock, self._run_lock = RLock(), Lock()

    def subscribe(self, listener):
        return self.session.subscribe(listener)

    def request_stop(self):
        """Cooperative: no cancellation or replay of an in-flight side effect."""
        self._abort.set()

    def step(self):
        with self._step_lock:
            return self._step()

    def _step(self):
        prepared = self.host.prepare_request()
        if not isinstance(prepared, PreparedTurn):
            return prepared
        try:
            reply = self.model.decide(prepared.context)
            if not isinstance(reply, ModelReply) or not isinstance(reply.usage, ModelUsage):
                raise ValueError("Missing model usage")
        except Exception as error:
            result = self.host.model_error(error, prepared)
        else:
            result = self.host.model_response(reply, prepared)
            if isinstance(result, ToolInvocation):
                invocation = result
                observation = self.tools.execute(invocation.name, invocation.arguments, **invocation.context)
                result = self.host.tool_result(invocation, observation)
        self.session.emit("turn_end", turn=self.host.snapshot()["steps"])
        self.host.checkpoint()
        return self.host.snapshot()

    def run(self, *, max_steps=None, stop_requested=None):
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("Agent loop is already running")
        try:
            return self._run(max_steps=max_steps, stop_requested=stop_requested)
        finally:
            self._run_lock.release()

    def _run(self, *, max_steps=None, stop_requested=None):
        if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
            raise ValueError("max_steps must be a positive integer")
        if self.host.is_done():
            return self.host.snapshot()
        self.session.emit("agent_start")
        count = 0
        while not self.host.is_done() and (max_steps is None or count < max_steps):
            if self._abort.is_set() or (stop_requested and stop_requested()):
                self.host.stop()
                break
            self.step()
            count += 1
        self.session.emit("agent_end" if self.host.is_done() else "agent_pause")
        self.host.checkpoint()
        return self.host.snapshot()
