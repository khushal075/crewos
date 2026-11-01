"""Sequential decision/tool loop with an explicit decision-count budget."""

from collections.abc import Sequence
import json
import logging
from math import isfinite
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import UUID4, TypeAdapter

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision, ModelDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.decision_provider import DecisionProviderInterface
from crewos.application.interfaces.agent_metrics import AgentMetricsInterface, Operation, Outcome
from crewos.application.services.tool_dispatcher import ToolDispatcher
from crewos.application.services.tool_log_context import ToolLogContext, tool_log_context


class DecisionLimitExceeded(RuntimeError):
    """The configured decision budget ended before a final answer was returned."""


class AgentDeadlineExceeded(TimeoutError):
    """The cooperative elapsed-time budget ended; in-flight work is not undone."""


class AgentLoop:
    def __init__(
        self,
        *,
        provider: DecisionProviderInterface,
        dispatcher: ToolDispatcher,
        max_decisions: int = 5,
        max_duration_seconds: float = 120.0,
        logger: logging.Logger | None = None,
        metrics: AgentMetricsInterface | None = None,
    ):
        if type(max_decisions) is not int or max_decisions < 1:
            raise ValueError("max_decisions must be a positive integer")
        if (
            isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, (int, float))
            or not isfinite(max_duration_seconds)
            or max_duration_seconds <= 0
        ):
            raise ValueError("max_duration_seconds must be finite and positive")
        self._max_duration_seconds = max_duration_seconds
        self._provider = provider
        self._dispatcher = dispatcher
        self._max_decisions = max_decisions
        self._metrics = metrics
        self._decision_adapter = TypeAdapter(ModelDecision)
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def run(
        self,
        *,
        messages: Sequence[DecisionMessage],
        context: ToolExecutionContext,
        agent_run_id: UUID | str | None = None,
    ) -> FinalAnswerDecision:
        """Caller establishes message provenance and trusted execution context.

        Tool results are observations, never system instructions. This text-level
        history requires provider adaptation; it is not a native tool protocol.
        Failures propagate and no successful answer is fabricated.
        """
        run_id = str(
            uuid4() if agent_run_id is None else TypeAdapter(UUID4).validate_python(agent_run_id)
        )
        started = perf_counter()
        step = 0
        tool_calls = 0
        tools_completed = 0
        phase = "setup"
        phase_started = started
        tool_call_id = None

        def emit(event: str, *, level: int = logging.INFO, **fields):
            self._logger.log(
                level,
                {
                    "event": event,
                    "agent_run_id": run_id,
                    "tenant_id": context.tenant_id,
                    **fields,
                },
            )

        def measure(operation: Operation, outcome: Outcome, since: float):
            if self._metrics is None:
                return
            try:
                self._metrics.observe(
                    operation=operation,
                    outcome=outcome,
                    duration_seconds=max(0.0, perf_counter() - since),
                )
            except Exception as error:
                emit(
                    "agent_metrics_recording_failed",
                    level=logging.WARNING,
                    operation=operation,
                    error_type=type(error).__name__,
                )

        def check_deadline():
            nonlocal phase
            if perf_counter() - started >= self._max_duration_seconds:
                phase = "deadline"
                raise AgentDeadlineExceeded("Agent run deadline exceeded")

        emit(
            "agent_run_started",
            max_decisions=self._max_decisions,
            max_duration_seconds=self._max_duration_seconds,
        )
        try:
            history = list(messages)
            tool_schemas = self._dispatcher.tool_schemas()
            registered_names = {tool.name for tool in tool_schemas}
            for next_step in range(1, self._max_decisions + 1):
                check_deadline()
                step = next_step
                phase = "model"
                phase_started = perf_counter()
                tool_call_id = None
                emit("agent_model_call_started", step=step)
                decision = self._decision_adapter.validate_python(
                    self._provider.decide(messages=tuple(history), tools=tool_schemas)
                )
                measure("model", "success", phase_started)
                emit(
                    "agent_model_call_completed",
                    step=step,
                    duration_ms=round((perf_counter() - phase_started) * 1000, 3),
                    decision_type=decision.type,
                )
                check_deadline()
                if isinstance(decision, FinalAnswerDecision):
                    measure("run", "success", started)
                    emit(
                        "agent_run_completed",
                        decisions=step,
                        tool_calls=tool_calls,
                        tools_completed=tools_completed,
                        reason="final_answer",
                        duration_ms=round((perf_counter() - started) * 1000, 3),
                    )
                    return decision

                phase = "tool"
                phase_started = perf_counter()
                tool_call_id = str(uuid4())
                tool_calls += 1
                # Unknown model-supplied names may contain sensitive text.
                safe_name = decision.name if decision.name in registered_names else "[unregistered]"
                emit(
                    "agent_tool_call_started", step=step, tool_call_id=tool_call_id, tool=safe_name
                )
                check_deadline()
                with tool_log_context(
                    ToolLogContext(
                        agent_run_id=run_id,
                        tool_call_id=tool_call_id,
                        step=step,
                    )
                ):
                    result = self._dispatcher.dispatch(
                        name=decision.name,
                        arguments=decision.arguments,
                        context=context,
                    )
                tools_completed += 1
                measure("tool", "success", phase_started)
                emit(
                    "agent_tool_call_completed",
                    step=step,
                    tool_call_id=tool_call_id,
                    tool=safe_name,
                    duration_ms=round((perf_counter() - phase_started) * 1000, 3),
                )
                check_deadline()
                phase = "observation"
                phase_started = perf_counter()
                history.append(
                    DecisionMessage(role="assistant", content=decision.model_dump_json())
                )
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

            check_deadline()
            phase = "decision_limit"
            raise DecisionLimitExceeded("Decision limit reached before a final answer")
        except Exception as error:
            stop_reason: Outcome = (
                "deadline"
                if isinstance(error, AgentDeadlineExceeded)
                else "decision_limit" if isinstance(error, DecisionLimitExceeded) else "error"
            )
            if phase in {"model", "tool"}:
                measure(phase, "error", phase_started)
            measure(
                "run",
                stop_reason,
                started,
            )
            emit(
                "agent_run_failed",
                level=(
                    logging.WARNING
                    if stop_reason in {"deadline", "decision_limit"}
                    else logging.ERROR
                ),
                step=step,
                phase=phase,
                error_type=type(error).__name__,
                reason=(stop_reason if stop_reason != "error" else "execution_error"),
                decisions=step,
                tool_calls=tool_calls,
                tools_completed=tools_completed,
                tool_call_id=tool_call_id,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
            raise
