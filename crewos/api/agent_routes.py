"""Authenticated submission of model-driven agent jobs."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Header
import sqlite3
import time
from redis.exceptions import RedisError

from crewos.api.agent_schemas import AgentRunAccepted, AgentRunRequest
from crewos.application.dtos.agent_run_job import AgentRunJob
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import (
    PolicyDeniedError,
    PolicyEnforcedExecutor,
    PolicyEvaluationError,
)
from crewos.core.config import settings
from crewos.core.tenant import get_tool_execution_context, tenant_queue
from crewos.infrastructure.logging import get_logger
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from crewos.workers.agent_task import run_agent
from crewos.infrastructure.run_store import get_run_store
from crewos.application.interfaces.run_store import RunNotFound, RunConflict

router = APIRouter()
logger = get_logger(__name__)


def get_agent_submission_executor() -> PolicyEnforcedExecutor:
    return PolicyEnforcedExecutor(PermissionPolicyService(), logger=logger)


@router.post("/agents/runs", response_model=AgentRunAccepted, status_code=202)
def submit_agent_run(
    payload: AgentRunRequest,
    context: ToolExecutionContext = Depends(get_tool_execution_context),
    task_store: TaskOwnershipStore = Depends(get_task_store),
    executor: PolicyEnforcedExecutor = Depends(get_agent_submission_executor),
    run_store=Depends(get_run_store),
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
):
    def enqueue() -> AgentRunAccepted:
        if payload.max_decisions > settings.AGENT_MAX_DECISIONS:
            raise HTTPException(422, "Decision budget exceeds server policy")
        job = AgentRunJob(
            agent_run_id=uuid4(),
            tenant_id=context.tenant_id,
            message=payload.message,
            max_decisions=payload.max_decisions,
            answer_mode=payload.answer_mode,
        )
        record = run_store.create(
            job.model_dump(mode="json"),
            duration_seconds=settings.AGENT_MAX_DURATION,
            idempotency_key=idempotency_key,
        )
        replayed = record["id"] != str(job.agent_run_id)
        if replayed and (record["published"] or record["state"] != "queued"):
            return AgentRunAccepted(
                agent_run_id=record["id"], status=record["state"], replayed=True
            )
        job = AgentRunJob.model_validate(record["job"])
        run_id = str(job.agent_run_id)
        queue = tenant_queue(context.tenant_id)
        # Durable identity allows safe repeat publication after an uncertain send.
        try:
            task_store.require_owner(run_id, context.tenant_id)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            task_store.register(run_id, context.tenant_id)
        run_agent.apply_async(
            args=[job.model_dump(mode="json")],
            task_id=run_id,
            queue=queue,
            expires=settings.TASK_QUEUE_TTL,
            retry=False,
        )
        run_store.mark_published(run_id, context.tenant_id)
        logger.info(
            {
                "event": "agent_job_submitted",
                "agent_run_id": run_id,
                "tenant_id": context.tenant_id,
                "queue": queue,
            }
        )
        return AgentRunAccepted(agent_run_id=job.agent_run_id, replayed=replayed)

    try:
        return executor.execute(context=context, action="agent_run.create", operation=enqueue)
    except PolicyDeniedError:
        raise HTTPException(403, "Agent run creation denied") from None
    except PolicyEvaluationError:
        raise HTTPException(503, "Agent authorization unavailable") from None
    except HTTPException:
        raise
    except RunConflict:
        raise HTTPException(409, "Agent submission conflicts with existing run") from None
    except (RedisError, sqlite3.Error):
        raise HTTPException(503, "Task storage unavailable") from None
    except Exception as error:
        logger.error({"event": "agent_submission_failed", "error_type": type(error).__name__})
        raise HTTPException(500, "Failed to submit agent run") from None


def run_operation(executor, context, action, operation):
    try:
        return executor.execute(context=context, action=action, operation=operation)
    except PolicyDeniedError:
        raise HTTPException(403, "Agent operation denied") from None
    except PolicyEvaluationError:
        raise HTTPException(503, "Agent authorization unavailable") from None
    except RunNotFound:
        raise HTTPException(404, "Agent run unavailable") from None
    except RunConflict:
        raise HTTPException(409, "Agent run cannot perform this transition") from None
    except (sqlite3.Error, RedisError):
        raise HTTPException(503, "Agent storage unavailable") from None
    except HTTPException:
        raise
    except Exception as error:
        logger.error({"event": "agent_operation_failed", "error_type": type(error).__name__})
        raise HTTPException(500, "Agent operation failed") from None


def public_record(record):
    return {
        "agent_run_id": record["id"],
        "state": record["state"],
        "created": record["created"],
        "updated": record["updated"],
        "attempts": record["attempts"],
        "publication_confirmed": bool(record["published"]),
        "attempt_history": record.get("attempt_history", []),
        "cancel_requested": record["cancel_requested"],
        "deadline": record["deadline"],
        "lease_active": record["state"] == "running" and record["lease_until"] > time.time(),
        "result": record["result"],
        "error_code": record["error"],
        "decisions": record["checkpoint"].get("decisions", 0),
        "approval_ids": record["checkpoint"].get("approval_ids", []),
        "reserved_tokens": record["checkpoint"].get("reserved_tokens", 0),
        "reported_input_tokens": record["checkpoint"].get("reported_input_tokens"),
        "reported_output_tokens": record["checkpoint"].get("reported_output_tokens"),
        "estimated_cost_usd": (
            record["checkpoint"].get("reserved_cost")
            if record["checkpoint"].get("pricing_configured")
            else None
        ),
    }


@router.get("/agents/runs/{run_id}")
def read_agent_run(
    run_id: str,
    context=Depends(get_tool_execution_context),
    store=Depends(get_run_store),
    executor=Depends(get_agent_submission_executor),
):
    return run_operation(
        executor,
        context,
        "agent_run.read",
        lambda: public_record(store.get(run_id, context.tenant_id)),
    )


@router.post("/agents/runs/{run_id}/cancel", status_code=202)
def cancel_agent_run(
    run_id: str,
    context=Depends(get_tool_execution_context),
    store=Depends(get_run_store),
    executor=Depends(get_agent_submission_executor),
):
    return run_operation(
        executor,
        context,
        "agent_run.cancel",
        lambda: public_record(store.cancel(run_id, context.tenant_id)),
    )


@router.post("/agents/runs/{run_id}/resume", status_code=202)
def resume_agent_run(
    run_id: str,
    context=Depends(get_tool_execution_context),
    store=Depends(get_run_store),
    task_store=Depends(get_task_store),
    executor=Depends(get_agent_submission_executor),
):
    def resume():
        record = store.resume(run_id, context.tenant_id)
        # Legacy ownership is only a compatibility aid; durable ownership is authoritative.
        try:
            task_store.require_owner(run_id, context.tenant_id)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            task_store.register(run_id, context.tenant_id)
        run_agent.apply_async(
            args=[record["job"]],
            task_id=run_id,
            queue=tenant_queue(context.tenant_id),
            expires=settings.TASK_QUEUE_TTL,
            retry=False,
        )
        store.mark_published(run_id, context.tenant_id)
        return {"agent_run_id": run_id, "state": "queued"}

    return run_operation(executor, context, "agent_run.resume", resume)
