"""Invocation-local log correlation, never authentication or authorization."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Iterator


@dataclass(frozen=True)
class ToolLogContext:
    agent_run_id: str
    tool_call_id: str
    step: int


_current: ContextVar[ToolLogContext | None] = ContextVar("tool_log_context", default=None)


def get_tool_log_context() -> ToolLogContext | None:
    return _current.get()


@contextmanager
def tool_log_context(context: ToolLogContext) -> Iterator[None]:
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)
