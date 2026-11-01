"""Authenticated submission of model-driven agent jobs."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
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
):
    def enqueue() -> AgentRunAccepted:
        job = AgentRunJob(
            agent_run_id=uuid4(),
            tenant_id=context.tenant_id,
            message=payload.message,
            max_decisions=payload.max_decisions,
        )
        run_id = str(job.agent_run_id)
        queue = tenant_queue(context.tenant_id)
        # Keep ownership after ambiguous publication failure: work may have arrived.
        task_store.register(run_id, context.tenant_id)
        run_agent.apply_async(
            args=[job.model_dump(mode="json")],
            task_id=run_id,
            queue=queue,
            expires=settings.TASK_QUEUE_TTL,
            retry=False,
        )
        logger.info(
            {
                "event": "agent_job_submitted",
                "agent_run_id": run_id,
                "tenant_id": context.tenant_id,
                "queue": queue,
            }
        )
        return AgentRunAccepted(agent_run_id=job.agent_run_id)

    try:
        return executor.execute(context=context, action="agent_run.create", operation=enqueue)
    except PolicyDeniedError:
        raise HTTPException(403, "Agent run creation denied") from None
    except PolicyEvaluationError:
        raise HTTPException(503, "Agent authorization unavailable") from None
    except RedisError:
        raise HTTPException(503, "Task storage unavailable") from None
    except Exception as error:
        logger.error({"event": "agent_submission_failed", "error_type": type(error).__name__})
        raise HTTPException(500, "Failed to submit agent run") from None
