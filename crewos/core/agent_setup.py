"""Assemble an agent loop without performing inference or task reads."""

from langchain_core.language_models.chat_models import BaseChatModel

from crewos.application.dtos.model_decision import AgentAnswerMode
from crewos.application.interfaces.document_answer_reviewer import DocumentAnswerReviewerInterface
from crewos.third_party.llm.document_answer_reviewer import JsonDocumentAnswerReviewer
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.use_cases.agent_loop import AgentLoop
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.core.config import settings
from crewos.infrastructure.logging import get_logger
from crewos.infrastructure.agent_metrics import agent_metrics
from crewos.third_party.llm.json_decision_adapter import JsonDecisionAdapter


logger = get_logger("crewos.agent")


def build_agent_loop(
    *,
    model: BaseChatModel,
    reader: TaskStatusReaderInterface,
    max_decisions: int = 5,
    answer_mode: AgentAnswerMode = "general",
    document_reviewer: DocumentAnswerReviewerInterface | None = None,
    max_duration_seconds: float | None = None,
) -> AgentLoop:
    """Caller supplies configured dependencies and per-run inputs separately.

    Model configuration and authentication are not performed here. The loop
    derives model-visible tool schemas from its dispatcher registrations. Construction does not invoke the model or the reader.
    """
    if max_decisions > settings.AGENT_MAX_DECISIONS:
        raise ValueError("Decision budget exceeds server policy")
    return AgentLoop(
        provider=JsonDecisionAdapter(
            model,
            answer_mode=answer_mode,
            max_call_timeout_seconds=settings.LLM_TIMEOUT,
            max_output_tokens=settings.AGENT_MAX_OUTPUT_TOKENS,
        ),
        dispatcher=build_tool_dispatcher(reader=reader),
        max_decisions=max_decisions,
        answer_mode=answer_mode,
        document_reviewer=(
            document_reviewer
            if document_reviewer is not None
            else (
                JsonDocumentAnswerReviewer(
                    (
                        model.bind(model=settings.DOCUMENT_REVIEW_MODEL)
                        if settings.DOCUMENT_REVIEW_MODEL
                        else model
                    ),
                    max_timeout_seconds=settings.LLM_TIMEOUT,
                )
                if answer_mode == "document"
                else None
            )
        ),
        max_duration_seconds=(
            settings.AGENT_MAX_DURATION if max_duration_seconds is None else max_duration_seconds
        ),
        logger=logger,
        metrics=agent_metrics,
        control_options={
            "max_tokens": settings.AGENT_MAX_TOKENS,
            "max_cost_usd": settings.AGENT_MAX_COST_USD,
            "input_usd_per_million": settings.AGENT_INPUT_USD_PER_MILLION,
            "output_usd_per_million": settings.AGENT_OUTPUT_USD_PER_MILLION,
            "max_recoveries": settings.AGENT_MAX_RECOVERIES,
        },
    )
