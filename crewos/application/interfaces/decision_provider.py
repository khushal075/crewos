"""Boundary between the agent runtime and provider-specific decision generation."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from crewos.application.dtos.decision_request import DecisionMessage, ToolSchema
from crewos.application.dtos.model_decision import ModelDecision


class DecisionProviderInterface(ABC):
    @abstractmethod
    def decide(
        self,
        *,
        messages: Sequence[DecisionMessage],
        tools: Sequence[ToolSchema],
        timeout_seconds: float,
    ) -> ModelDecision:
        """Return one validated proposal; do not execute tools or grant access.

        timeout_seconds is the positive remaining run allowance; adapters must
        honor it without increasing their configured per-call timeout.
        Adapters translate provider formats and validate the returned decision.
        Transport errors, refusals, and malformed responses must not be turned
        into fabricated final answers. The runtime owns execution and budgets.
        """
        raise NotImplementedError
