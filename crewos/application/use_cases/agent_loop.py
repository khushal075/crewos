"""Sequential decision/tool loop with an explicit decision-count budget."""

from collections.abc import Sequence
import json
import logging
from math import isfinite
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import UUID4, TypeAdapter, ValidationError
from redis.exceptions import RedisError

from crewos.application.dtos.source_reference import SourceReference
from crewos.application.services.citation_validation import validate_answer_citations
from crewos.application.services.document_answer_validation import validate_document_answer
from crewos.retrieval import RetrievalResult
from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import (
    FinalAnswerDecision,
    ModelDecision,
    DocumentFinalAnswerDecision,
    DocumentModelDecision,
    AgentAnswer,
    AgentAnswerMode,
)
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.decision_provider import DecisionProviderInterface
from crewos.application.interfaces.agent_metrics import AgentMetricsInterface, Operation, Outcome
from crewos.application.services.tool_dispatcher import ToolDispatcher
from crewos.application.services.tool_log_context import ToolLogContext, tool_log_context
from crewos.application.services.execution_control import (
    ExecutionControl,
    execution_scope,
    get_execution_control,
    ResourceBudgetExceeded,
    AgentDeadlineExceeded,
)
from crewos.application.interfaces.run_store import RunCancelled
from crewos.application.services.tool_registry import UnknownToolError
from crewos.application.services.decision_errors import DecisionResponseError, DecisionRefusedError
from crewos.utils.retry import is_retryable


class DecisionLimitExceeded(RuntimeError):
    """The configured decision budget ended before a final answer was returned."""


class AgentLoop:
    def __init__(
        self,
        *,
        provider: DecisionProviderInterface,
        dispatcher: ToolDispatcher,
        max_decisions: int = 5,
        answer_mode: AgentAnswerMode = "general",
        max_duration_seconds: float = 120.0,
        logger: logging.Logger | None = None,
        metrics: AgentMetricsInterface | None = None,
        control_options: dict | None = None,
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
        self._control_options = control_options or {"max_recoveries": 0}
        self._answer_mode = TypeAdapter(AgentAnswerMode).validate_python(answer_mode)
        self._decision_adapter = TypeAdapter(
            DocumentModelDecision if answer_mode == "document" else ModelDecision
        )
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def run(self, *, messages, context, agent_run_id=None, control=None):
        control = control or ExecutionControl(**self._control_options)
        with execution_scope(control):
            return self._run(messages=messages, context=context, agent_run_id=agent_run_id)

    def _run(
        self,
        *,
        messages: Sequence[DecisionMessage],
        context: ToolExecutionContext,
        agent_run_id: UUID | str | None = None,
    ) -> AgentAnswer:
        """Caller establishes message provenance and trusted execution context.

        Tool results are observations, never system instructions. This text-level
        history requires provider adaptation; it is not a native tool protocol.
        Failures propagate and no successful answer is fabricated.
        """
        run_id = str(
            uuid4() if agent_run_id is None else TypeAdapter(UUID4).validate_python(agent_run_id)
        )
        started = perf_counter()
        control = get_execution_control()
        step = control.state.get("decisions", 0)
        tool_calls = control.state.get("tool_calls", 0)
        tools_completed = control.state.get("tools_completed", 0)
        observed_sources = {
            SourceReference.model_validate(item)
            for item in control.state.get("observed_sources", [])
        }
        document_search_completed = control.state.get("document_search_completed", False)
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
                    "attempt": control.record["attempts"] if control.record else 1,
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

        def remaining_seconds() -> float:
            nonlocal phase
            remaining = min(
                self._max_duration_seconds - (perf_counter() - started), control.guard()
            )
            if remaining <= 0:
                phase = "deadline"
                raise AgentDeadlineExceeded("Agent run deadline exceeded")
            return remaining

        def check_deadline():
            remaining_seconds()

        def checkpoint(history, *, pending=None, replay_safe=False):
            control.persist(
                history=[message.model_dump() for message in history],
                decisions=step,
                tool_calls=tool_calls,
                tools_completed=tools_completed,
                pending=pending,
                replay_safe=replay_safe,
                answer_mode=self._answer_mode,
                document_search_completed=document_search_completed,
                observed_sources=[
                    source.model_dump(mode="json")
                    for source in sorted(
                        observed_sources,
                        key=lambda s: (str(s.document_id), s.document_version, s.chunk_index),
                    )
                ],
            )

        emit(
            "agent_run_started",
            answer_mode=self._answer_mode,
            max_decisions=self._max_decisions,
            max_duration_seconds=self._max_duration_seconds,
        )
        try:
            if ("answer_mode" in control.state or "history" in control.state) and control.state.get(
                "answer_mode", "general"
            ) != self._answer_mode:
                raise ValueError("Checkpoint answer mode differs from configured mode")
            history = (
                [DecisionMessage.model_validate(item) for item in control.state["history"]]
                if "history" in control.state
                else list(messages)
            )
            pending = control.state.get("pending")
            tool_schemas = self._dispatcher.tool_schemas()
            registered_names = {tool.name for tool in tool_schemas}
            first_step = step if pending else step + 1
            for next_step in range(max(1, first_step), self._max_decisions + 1):
                check_deadline()
                step = next_step
                tool_call_id = None
                if pending:
                    decision = self._decision_adapter.validate_python(pending)
                    pending = None
                    emit("agent_tool_replay_started", step=step)
                else:
                    phase = "model"
                    phase_started = perf_counter()
                    checkpoint(history)
                    emit("agent_model_call_started", step=step)
                    try:
                        decision = self._decision_adapter.validate_python(
                            self._provider.decide(
                                messages=tuple(history),
                                tools=tool_schemas,
                                timeout_seconds=remaining_seconds(),
                            )
                        )
                    except Exception as error:
                        recoverable = (
                            isinstance(error, (ValidationError, DecisionResponseError))
                            and not isinstance(error, DecisionRefusedError)
                        ) or (
                            is_retryable(error)
                            and not (
                                getattr(error, "response", None) is not None
                                and error.response.headers.get("retry-after")
                            )
                        )
                        if not recoverable or not control.recover():
                            raise
                        measure("model", "error", phase_started)
                        phase = "recovery"
                        emit(
                            "agent_recovery_requested",
                            step=step,
                            source="model",
                            error_type=type(error).__name__,
                        )
                        history.append(
                            DecisionMessage(
                                role="user",
                                content="The previous decision could not be used. Return one valid decision using the provided schema. Do not invent tool results.",
                            )
                        )
                        checkpoint(history)
                        continue
                    measure("model", "success", phase_started)
                    emit(
                        "agent_model_call_completed",
                        step=step,
                        duration_ms=round((perf_counter() - phase_started) * 1000, 3),
                        decision_type=decision.type,
                    )
                check_deadline()
                if isinstance(decision, (FinalAnswerDecision, DocumentFinalAnswerDecision)):
                    phase = "citation_validation"
                    if isinstance(decision, DocumentFinalAnswerDecision):
                        validate_document_answer(
                            decision,
                            observed=observed_sources,
                            search_completed=document_search_completed,
                        )
                    else:
                        validate_answer_citations(decision, observed=observed_sources)
                    control.persist(final_result=decision.model_dump(mode="json"))
                    measure("run", "success", started)
                    emit(
                        "agent_run_completed",
                        decisions=step,
                        tool_calls=tool_calls,
                        tools_completed=tools_completed,
                        reason="final_answer",
                        answer_mode=self._answer_mode,
                        document_outcome=(
                            decision.result.outcome.value
                            if isinstance(decision, DocumentFinalAnswerDecision)
                            else None
                        ),
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
                safe_replay = False
                try:
                    safe_replay = self._dispatcher.replay_safe(decision.name)
                    checkpoint(
                        history, pending=decision.model_dump(mode="json"), replay_safe=safe_replay
                    )
                    with tool_log_context(
                        ToolLogContext(agent_run_id=run_id, tool_call_id=tool_call_id, step=step)
                    ):
                        with control.tool_budget(remaining_seconds()):
                            result = self._dispatcher.dispatch(
                                name=decision.name, arguments=decision.arguments, context=context
                            )
                except Exception as error:
                    recoverable = isinstance(error, (UnknownToolError, ValidationError)) or (
                        safe_replay
                        and isinstance(error, (RedisError, TimeoutError))
                        and not isinstance(error, AgentDeadlineExceeded)
                    )
                    if not recoverable or not control.recover():
                        raise
                    measure("tool", "error", phase_started)
                    phase = "recovery"
                    emit(
                        "agent_recovery_requested",
                        step=step,
                        source="tool",
                        error_type=type(error).__name__,
                    )
                    history.append(
                        DecisionMessage(role="assistant", content=decision.model_dump_json())
                    )
                    history.append(
                        DecisionMessage(
                            role="tool",
                            content=json.dumps(
                                {
                                    "name": safe_name,
                                    "error": "tool_unavailable_or_invalid_arguments",
                                }
                            ),
                        )
                    )
                    checkpoint(history)
                    continue
                if decision.name == "search_documents" and isinstance(result, RetrievalResult):
                    document_search_completed = True
                    observed_sources.update(
                        SourceReference(
                            document_id=hit.document_id,
                            document_version=hit.document_version,
                            chunk_index=hit.chunk.index,
                        )
                        for hit in result.passages
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

                checkpoint(history)

            check_deadline()
            phase = "decision_limit"
            raise DecisionLimitExceeded("Decision limit reached before a final answer")
        except Exception as error:
            stop_reason: Outcome = (
                "cancelled"
                if isinstance(error, RunCancelled)
                else (
                    "budget"
                    if isinstance(error, ResourceBudgetExceeded)
                    else (
                        "deadline"
                        if isinstance(error, AgentDeadlineExceeded)
                        else (
                            "decision_limit"
                            if isinstance(error, DecisionLimitExceeded)
                            else "error"
                        )
                    )
                )
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
                    if stop_reason in {"deadline", "decision_limit", "cancelled", "budget"}
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
