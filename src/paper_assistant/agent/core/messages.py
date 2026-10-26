"""Canonical transcript and lifecycle events, independent of any provider wire format."""
import json
from collections.abc import Callable
from typing import Literal, NotRequired, TypeAlias, TypedDict


class ToolCall(TypedDict):
    id: str
    name: str
    arguments: dict


class UserMessage(TypedDict):
    role: Literal["user"]
    text: str
    message_id: NotRequired[str]
    data: NotRequired[dict]


class AssistantMessage(TypedDict):
    role: Literal["assistant"]
    text: NotRequired[str]
    tool_calls: list[ToolCall]
    message_id: NotRequired[str]


class ToolResultMessage(TypedDict):
    role: Literal["tool"]
    tool_call_id: str
    name: str
    result: dict
    is_error: bool
    message_id: NotRequired[str]


AgentMessage: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage


def copy_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


class AgentSession:
    def __init__(self, messages: list[AgentMessage], events: list, *, now: Callable):
        completed_exchanges(messages)
        self.messages, self.events, self.now = messages, events, now
        self._listeners = []

    def subscribe(self, listener):
        self._listeners.append(listener)
        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)
        return unsubscribe

    def emit(self, event_type, *, turn=0, **data):
        event = {"sequence": len(self.events) + 1, "type": event_type, "turn": turn,
                 "timestamp": self.now().isoformat(), "data": copy_json(data)}
        self.events.append(event)
        for listener in tuple(self._listeners):
            try:
                listener(copy_json(event))
            except Exception:
                # An observer must not change execution or trigger a retry.
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return event

    def append(self, message, *, turn=0):
        message = copy_json(message)
        if message.get("role") not in ("user", "assistant", "tool"):
            raise ValueError("Invalid agent message role")
        message["message_id"] = f"message-{len(self.messages) + 1}"
        self.emit("message_start", turn=turn, message_id=message["message_id"], role=message["role"])
        self.messages.append(message)
        self.emit("message_end", turn=turn, message_id=message["message_id"], role=message["role"])
        return message

    def assistant(self, call_id, name, arguments, *, turn=0, text=""):
        if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
            raise ValueError("Invalid tool call ID")
        if any(call["id"] == call_id for message in self.messages for call in message.get("tool_calls", [])):
            raise ValueError("Duplicate tool call ID")
        return self.append({"role": "assistant", "text": text,
                            "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}]}, turn=turn)

    def tool_result(self, call_id, result, *, turn=0):
        calls = [call for message in self.messages for call in message.get("tool_calls", []) if call["id"] == call_id]
        if len(calls) != 1:
            raise ValueError("Tool result requires one matching assistant call")
        if any(message.get("role") == "tool" and message.get("tool_call_id") == call_id for message in self.messages):
            raise ValueError("Duplicate tool result")
        return self.append({"role": "tool", "tool_call_id": call_id, "name": calls[0]["name"],
                            "result": result, "is_error": not result.get("success", True)}, turn=turn)

    def has_result(self, call_id):
        return any(message.get("role") == "tool" and message.get("tool_call_id") == call_id for message in self.messages)


def completed_exchanges(messages):
    """Select complete call/result pairs; never expose an unmatched tool result."""
    calls = {}
    completed = []
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                if call["id"] in calls:
                    raise ValueError("Duplicate assistant tool call")
                calls[call["id"]] = call
        elif message.get("role") == "tool":
            identifier = message.get("tool_call_id")
            if identifier not in calls or any(call["id"] == identifier for call, _ in completed):
                raise ValueError("Unmatched or duplicate tool result")
            completed.append((calls[identifier], message))
    return completed
