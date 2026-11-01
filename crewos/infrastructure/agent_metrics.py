"""Process-local Prometheus counters and duration histograms."""

from math import isfinite
from pathlib import Path
import os
import tempfile

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from crewos.application.interfaces.agent_metrics import Operation, Outcome


class PrometheusAgentMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self._operations = Counter(
            "crewos_agent_operations_total",
            "Completed or failed agent operations.",
            ["operation", "outcome"],
            registry=self.registry,
        )
        self._duration = Histogram(
            "crewos_agent_operation_duration_seconds",
            "Elapsed agent operation time in seconds.",
            ["operation", "outcome"],
            registry=self.registry,
            buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
        )

    def observe(self, *, operation: Operation, outcome: Outcome, duration_seconds: float) -> None:
        if operation not in {"run", "model", "tool"}:
            raise ValueError("Unsupported metric operation")
        if outcome not in {"success", "error", "decision_limit", "deadline", "cancelled", "budget"}:
            raise ValueError("Unsupported metric outcome")
        if outcome in {"decision_limit", "deadline", "cancelled", "budget"} and operation != "run":
            raise ValueError("Budget outcomes apply only to runs")
        if not isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("Metric duration must be finite and nonnegative")
        self._operations.labels(operation=operation, outcome=outcome).inc()
        self._duration.labels(operation=operation, outcome=outcome).observe(duration_seconds)

    def render(self) -> bytes:
        """Snapshot in Prometheus exposition format; no network server is started."""
        return generate_latest(self.registry)


def write_metrics_snapshot(path: str | Path, metrics: PrometheusAgentMetrics) -> None:
    """Atomically replace a local snapshot; the parent directory must exist."""
    destination = Path(path)
    data = metrics.render()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=".crewos-metrics-",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


agent_metrics = PrometheusAgentMetrics()
