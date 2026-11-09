"""Assemble an agent loop without performing inference or task reads."""

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel

from crewos.application.services.fallback_decision_provider import FallbackDecisionProvider
from crewos.third_party.llm.fallback_configuration import eligible_provider_failure
from crewos.application.dtos.model_decision import AgentAnswerMode
from crewos.application.interfaces.decision_provider import DecisionProviderInterface
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

    def adapter(chat_model):
        return JsonDecisionAdapter(
            chat_model,
            answer_mode=answer_mode,
            max_call_timeout_seconds=settings.LLM_TIMEOUT,
            max_output_tokens=settings.AGENT_MAX_OUTPUT_TOKENS,
        )

    provider: DecisionProviderInterface = adapter(model)
    fallback = settings.AGENT_FALLBACK_MODEL
    if fallback is not None:

        class SecondaryDecisionProvider(DecisionProviderInterface):
            def decide(self, *, messages, tools, timeout_seconds):
                with httpx.Client(follow_redirects=False, trust_env=False) as client:
                    secondary = ChatOpenAI(
                        base_url=fallback.base_url,
                        api_key=fallback.api_key.get_secret_value(),
                        model=fallback.model,
                        max_retries=0,
                        temperature=0,
                        http_client=client,
                    )
                    return adapter(secondary).decide(
                        messages=messages, tools=tools, timeout_seconds=timeout_seconds
                    )

        rates = (
            (fallback.input_usd_per_million, fallback.output_usd_per_million)
            if fallback.input_usd_per_million is not None
            and fallback.output_usd_per_million is not None
            else None
        )
        provider = FallbackDecisionProvider(
            provider,
            SecondaryDecisionProvider(),
            eligible=eligible_provider_failure,
            primary_timeout=fallback.primary_timeout_seconds,
            secondary_rates=rates,
            logger=logger,
        )
    return AgentLoop(
        provider=provider,
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
