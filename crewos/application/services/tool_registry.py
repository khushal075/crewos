"""Explicit tool registrations; lookup does not execute or authorize tools."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolDefinition:
    """Trusted application configuration, not a model-supplied registration."""

    name: str
    description: str
    argument_schema: type[BaseModel]
    handler: Callable[..., BaseModel]


class UnknownToolError(LookupError):
    """The requested name has no registered implementation."""


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name or tool.name != tool.name.strip():
            raise ValueError("Tool name must be nonempty with no surrounding whitespace")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(f"Unknown tool: {name}") from None
