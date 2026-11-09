"""Bounded failover for model decisions only; never replays a tool call."""

from collections.abc import Callable
from decimal import Decimal
from math import isfinite
from time import monotonic

from crewos.application.interfaces.decision_provider import DecisionProviderInterface
from crewos.application.services.execution_control import (
    AgentDeadlineExceeded,
    get_execution_control,
)


class FallbackDecisionProvider(DecisionProviderInterface):
    def __init__(
        self,
        primary,
        secondary,
        *,
        eligible: Callable[[Exception], bool],
        primary_timeout: float,
        secondary_rates: tuple[Decimal, Decimal] | None = None,
        logger=None,
        clock=monotonic,
    ):
        if not isfinite(primary_timeout) or primary_timeout <= 0:
            raise ValueError("Primary attempt timeout must be finite and positive")
        self.primary, self.secondary = primary, secondary
        self.eligible, self.primary_timeout = eligible, primary_timeout
        self.secondary_rates, self.logger, self.clock = secondary_rates, logger, clock

    def decide(self, *, messages, tools, timeout_seconds):
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Decision allowance must be finite and positive")
        deadline = self.clock() + timeout_seconds

        def remaining():
            control = get_execution_control()
            seconds = min(deadline - self.clock(), control.guard() if control else float("inf"))
            if seconds <= 0:
                raise AgentDeadlineExceeded("Decision time budget exhausted")
            return seconds

        try:
            result = self.primary.decide(
                messages=messages,
                tools=tools,
                timeout_seconds=min(remaining(), self.primary_timeout),
            )
        except Exception as error:
            if not self.eligible(error):
                raise
            control = get_execution_control()
            if control and (control.max_cost is not None or control.input_rate is not None):
                # Both adapters reserve through the same run accounting. It may
                # conservatively price the secondary at primary ceiling rates.
                if (
                    self.secondary_rates is None
                    or control.input_rate is None
                    or control.output_rate is None
                    or self.secondary_rates[0] > control.input_rate
                    or self.secondary_rates[1] > control.output_rate
                ):
                    raise
            allowance = remaining()
            if self.logger:
                self.logger.info(
                    {"event": "agent_provider_fallback", "error_type": type(error).__name__}
                )
            result = self.secondary.decide(
                messages=messages, tools=tools, timeout_seconds=allowance
            )
        remaining()
        return result
