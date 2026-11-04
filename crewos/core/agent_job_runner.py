"""Fenced, checkpointed execution of a durable backend-created agent job."""

import time
import sqlite3
from decimal import Decimal

from crewos.application.dtos.source_reference import SourceReference
from crewos.application.services.citation_validation import validate_answer_citations
from crewos.application.dtos.agent_run_job import AgentRunJob
from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.application.interfaces.run_store import RunCancelled, RunLeaseLost
from crewos.application.services.execution_control import ExecutionControl, ResourceBudgetExceeded
from crewos.application.use_cases.agent_loop import AgentDeadlineExceeded, DecisionLimitExceeded
from crewos.core.agent_setup import build_agent_loop
from crewos.core.config import settings
from crewos.core.tenant import get_tool_execution_context, tenant_context
from crewos.infrastructure.run_store import get_run_store
from crewos.infrastructure.logging import get_logger

logger = get_logger("crewos.agent_attempts")
from crewos.infrastructure.task_ownership import get_task_store
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider


def execute_agent_job(payload: object) -> FinalAnswerDecision:
    job = AgentRunJob.model_validate(payload)
    if job.max_decisions > settings.AGENT_MAX_DECISIONS:
        raise ValueError("Decision budget exceeds worker policy")
    store = get_run_store()
    record = store.claim(job.model_dump(mode="json"))
    if record["state"] == "succeeded":
        logger.info(
            {
                "event": "agent_cached_result_returned",
                "agent_run_id": record["id"],
                "tenant_id": record["tenant"],
            }
        )
        return FinalAnswerDecision.model_validate(record["result"])
    token, run_id = record["lease_token"], str(job.agent_run_id)
    logger.info(
        {
            "event": "agent_attempt_claimed",
            "agent_run_id": run_id,
            "tenant_id": job.tenant_id,
            "attempt": record["attempts"],
        }
    )
    limits = record["checkpoint"].get("limits") or {
        "max_tokens": settings.AGENT_MAX_TOKENS,
        "max_cost_usd": (
            str(settings.AGENT_MAX_COST_USD) if settings.AGENT_MAX_COST_USD is not None else None
        ),
        "max_recoveries": settings.AGENT_MAX_RECOVERIES,
    }
    original_cost = limits["max_cost_usd"]
    current_cost = settings.AGENT_MAX_COST_USD
    cost_limit = (
        min(Decimal(original_cost), current_cost)
        if original_cost is not None and current_cost is not None
        else original_cost if original_cost is not None else current_cost
    )
    control = None
    try:
        control = ExecutionControl(
            store=store,
            record=record,
            max_tokens=min(limits["max_tokens"], settings.AGENT_MAX_TOKENS),
            max_cost_usd=cost_limit,
            input_usd_per_million=settings.AGENT_INPUT_USD_PER_MILLION,
            output_usd_per_million=settings.AGENT_OUTPUT_USD_PER_MILLION,
            max_recoveries=min(limits["max_recoveries"], settings.AGENT_MAX_RECOVERIES),
        )
        control.start_heartbeat()
        control.persist(limits=limits)
        if record["checkpoint"].get("final_result"):
            answer = FinalAnswerDecision.model_validate(record["checkpoint"]["final_result"])
            validate_answer_citations(
                answer,
                observed=[
                    SourceReference.model_validate(item)
                    for item in record["checkpoint"].get("observed_sources", [])
                ],
            )
        else:
            remaining = control.guard()
            if remaining <= 0:
                raise AgentDeadlineExceeded("Original run deadline expired")
            context = get_tool_execution_context(tenant_id=job.tenant_id)
            with tenant_context(context.tenant_id):
                model = OllamaLLMProvider().get()
                loop = build_agent_loop(
                    model=model,
                    reader=CeleryTaskStatusReader(get_task_store()),
                    max_decisions=job.max_decisions,
                    max_duration_seconds=remaining,
                )
                answer = loop.run(
                    messages=[DecisionMessage(role="user", content=job.message)],
                    context=context,
                    agent_run_id=job.agent_run_id,
                    control=control,
                )
        store.finish(
            run_id, job.tenant_id, token, state="succeeded", result=answer.model_dump(mode="json")
        )
        if store.get(run_id, job.tenant_id)["state"] == "cancelled":
            raise RunCancelled("Run cancelled")
        return answer
    except Exception as error:
        state = (
            "cancelled"
            if isinstance(error, RunCancelled)
            else (
                "exhausted"
                if isinstance(
                    error, (AgentDeadlineExceeded, DecisionLimitExceeded, ResourceBudgetExceeded)
                )
                else (
                    "needs_review"
                    if control is not None
                    and control.state.get("pending")
                    and not control.state.get("replay_safe")
                    else "failed"
                )
            )
        )
        try:
            store.finish(run_id, job.tenant_id, token, state=state, error=type(error).__name__)
        except (RunLeaseLost, sqlite3.Error) as persistence_error:
            logger.error(
                {
                    "event": "agent_terminal_state_not_written",
                    "agent_run_id": run_id,
                    "error_type": type(persistence_error).__name__,
                }
            )
        raise
    finally:
        if control is not None:
            control.close()
