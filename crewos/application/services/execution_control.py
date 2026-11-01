"""Run-local cancellation, durable checkpoints, and estimated resource admission."""

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import time
import threading

from crewos.application.interfaces.run_store import RunCancelled


class AgentDeadlineExceeded(TimeoutError):
    pass


class ResourceBudgetExceeded(RuntimeError):
    pass


class ExecutionControl:
    def __init__(
        self,
        *,
        store=None,
        record=None,
        max_tokens=100000,
        max_cost_usd=None,
        input_usd_per_million=None,
        output_usd_per_million=None,
        max_recoveries=1,
    ):
        self.store, self.record = store, record
        self.state = dict(record["checkpoint"]) if record else {}
        self.max_tokens = max_tokens
        self.max_cost = Decimal(str(max_cost_usd)) if max_cost_usd is not None else None
        self.input_rate = (
            Decimal(str(input_usd_per_million)) if input_usd_per_million is not None else None
        )
        self.output_rate = (
            Decimal(str(output_usd_per_million)) if output_usd_per_million is not None else None
        )
        if self.max_cost is not None and (self.input_rate is None or self.output_rate is None):
            raise ValueError("Cost admission requires configured input and output rates")
        if (
            type(max_tokens) is not int
            or max_tokens < 1
            or type(max_recoveries) is not int
            or max_recoveries < 0
        ):
            raise ValueError("Invalid execution budget")
        for amount in (self.max_cost, self.input_rate, self.output_rate):
            if amount is not None and (not amount.is_finite() or amount < 0):
                raise ValueError("Cost values must be finite and nonnegative")
        if (self.input_rate is None) != (self.output_rate is None):
            raise ValueError("Both token price rates must be configured together")
        self.max_recoveries = max_recoveries
        self._tool_deadline = None
        self._heartbeat_error = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    def guard(self):
        if self._heartbeat_error is not None:
            raise self._heartbeat_error
        if self.store:
            remaining = self.store.guard(
                self.record["id"], self.record["tenant"], self.record["lease_token"]
            )
            if remaining <= 0:
                raise AgentDeadlineExceeded("Original run deadline exceeded")
            return remaining
        return float("inf")

    def start_heartbeat(self):
        if not self.store:
            return

        def renew():
            while not self._heartbeat_stop.wait(self.store.lease_seconds / 3):
                try:
                    remaining = self.store.guard(
                        self.record["id"], self.record["tenant"], self.record["lease_token"]
                    )
                    if remaining <= 0:
                        break
                except Exception as error:
                    self._heartbeat_error = error
                    break

        self._heartbeat_thread = threading.Thread(target=renew, name="agent-lease", daemon=True)
        self._heartbeat_thread.start()

    def close(self):
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)

    def persist(self, **updates):
        self.state.update(updates)
        if self.store:
            self.store.checkpoint(
                self.record["id"], self.record["tenant"], self.record["lease_token"], self.state
            )

    def reserve(self, input_tokens, output_tokens):
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError("Token reservation must be nonnegative integers")
        self.guard()
        used = self.state.get("reserved_tokens", 0) + input_tokens + output_tokens
        cost = Decimal(self.state.get("reserved_cost", "0"))
        if self.input_rate is not None and self.output_rate is not None:
            cost += (
                Decimal(input_tokens) * self.input_rate + Decimal(output_tokens) * self.output_rate
            ) / 1000000
        if used > self.max_tokens or (self.max_cost is not None and cost > self.max_cost):
            raise ResourceBudgetExceeded("Estimated token or cost admission budget exceeded")
        self.persist(
            reserved_tokens=used,
            reserved_cost=str(cost),
            pricing_configured=self.input_rate is not None and self.output_rate is not None,
        )

    def reconcile(self, input_tokens, output_tokens, reserved_input, reserved_output):
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            return
        extra_input, extra_output = max(0, input_tokens - reserved_input), max(
            0, output_tokens - reserved_output
        )
        used = self.state.get("reserved_tokens", 0) + extra_input + extra_output
        cost = Decimal(self.state.get("reserved_cost", "0"))
        if self.input_rate is not None and self.output_rate is not None:
            cost += (
                Decimal(extra_input) * self.input_rate + Decimal(extra_output) * self.output_rate
            ) / 1000000
        self.persist(
            reserved_tokens=used,
            reserved_cost=str(cost),
            reported_input_tokens=self.state.get("reported_input_tokens", 0) + input_tokens,
            reported_output_tokens=self.state.get("reported_output_tokens", 0) + output_tokens,
        )
        if used > self.max_tokens or (self.max_cost is not None and cost > self.max_cost):
            raise ResourceBudgetExceeded("Reported usage exceeded admitted budget")

    def recover(self):
        used = self.state.get("recoveries", 0)
        if used >= self.max_recoveries:
            return False
        self.persist(recoveries=used + 1)
        return True

    @contextmanager
    def tool_budget(self, remaining):
        previous = self._tool_deadline
        self._tool_deadline = time.monotonic() + remaining
        try:
            yield
        finally:
            self._tool_deadline = previous

    def tool_remaining(self):
        remaining = self.guard()
        if self._tool_deadline is not None:
            remaining = min(remaining, self._tool_deadline - time.monotonic())
        if remaining <= 0:
            raise AgentDeadlineExceeded("Tool execution budget exhausted")
        return remaining


_current = ContextVar("execution_control", default=None)


def get_execution_control():
    return _current.get()


@contextmanager
def execution_scope(control):
    token = _current.set(control)
    try:
        yield
    finally:
        _current.reset(token)
