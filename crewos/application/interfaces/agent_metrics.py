"""Low-cardinality measurements independent of a metrics backend."""

from typing import Literal, Protocol

Operation = Literal["run", "model", "tool"]
Outcome = Literal["success", "error", "decision_limit", "deadline", "cancelled", "budget"]


class AgentMetricsInterface(Protocol):
    def observe(self, *, operation: Operation, outcome: Outcome, duration_seconds: float) -> None:
        """Record one completed/failed operation and its elapsed time."""
        ...
