"""Executable tool declarations; audit and authorization stay with the application gateway."""
from dataclasses import dataclass
from collections.abc import Callable

from .messages import copy_json


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict
    execute: Callable


class ToolRegistry:
    def __init__(self, tools=(), *, unknown=None):
        self._tools = {}
        self._unknown = unknown
        for tool in tools:
            self.register(tool)

    def register(self, tool):
        if not isinstance(tool, AgentTool) or not tool.name or tool.name in self._tools:
            raise ValueError("Invalid or duplicate tool")
        self._tools[tool.name] = AgentTool(tool.name, tool.description, copy_json(tool.parameters), tool.execute)

    def specifications(self):
        return [{"name": tool.name, "description": tool.description, "parameters": copy_json(tool.parameters)}
                for tool in self._tools.values()]

    def execute(self, name, arguments, **context):
        if name not in self._tools:
            if self._unknown:
                return self._unknown(name, copy_json(arguments), **context)
            raise ValueError("Tool is not registered")
        return self._tools[name].execute(copy_json(arguments), **context)
