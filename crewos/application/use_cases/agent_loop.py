"""Sequential decision/tool loop with an explicit decision-count budget."""

from collections.abc import Sequence
import json

from pydantic import TypeAdapter

from crewos.application.dtos.decision_request import DecisionMessage, ToolSchema
from crewos.application.dtos.model_decision import FinalAnswerDecision, ModelDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.decision_provider import DecisionProviderInterface
from crewos.application.services.tool_dispatcher import ToolDispatcher


class DecisionLimitExceeded(RuntimeError):
    """The configured decision budget ended before a final answer was returned."""


class AgentLoop:
    def __init__(
        self,
        *,
        provider: DecisionProviderInterface,
        dispatcher: ToolDispatcher,
        max_decisions: int = 5,
    ):
        if type(max_decisions) is not int or max_decisions < 1:
            raise ValueError("max_decisions must be a positive integer")
        self._provider = provider
        self._dispatcher = dispatcher
        self._max_decisions = max_decisions
        self._decision_adapter = TypeAdapter(ModelDecision)

    def run(
        self,
        *,
        messages: Sequence[DecisionMessage],
        tools: Sequence[ToolSchema],
        context: ToolExecutionContext,
    ) -> FinalAnswerDecision:
        """Caller establishes message provenance and trusted execution context.

        Tool results are observations, never system instructions. This text-level
        history requires provider adaptation; it is not a native tool protocol.
        Failures propagate and no successful answer is fabricated.
        """
        history = list(messages)
        tool_schemas = tuple(tools)
        for _ in range(self._max_decisions):
            decision = self._decision_adapter.validate_python(
                self._provider.decide(messages=tuple(history), tools=tool_schemas)
            )
            if isinstance(decision, FinalAnswerDecision):
                return decision

            result = self._dispatcher.dispatch(
                name=decision.name,
                arguments=decision.arguments,
                context=context,
            )
            history.append(DecisionMessage(role="assistant", content=decision.model_dump_json()))
            history.append(
                DecisionMessage(
                    role="tool",
                    content=json.dumps(
                        {
                            "name": decision.name,
                            "result": result.model_dump(mode="json"),
                        }
                    ),
                )
            )

        raise DecisionLimitExceeded("Decision limit reached before a final answer")
