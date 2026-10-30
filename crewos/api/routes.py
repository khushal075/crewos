# crewos/api/routes.py

from fastapi import HTTPException, APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from celery.result import AsyncResult
from uuid import uuid4
from redis.exceptions import RedisError
from crewos.infrastructure.task_ownership import get_task_store, TaskOwnershipStore

from crewos.core.config import settings
from crewos.infrastructure.logging import get_logger
from crewos.infrastructure.celery_app import celery_app
from crewos.services.crew_runner import CrewRunner
from crewos.workers.crew_task import run_crew
from crewos.core.tenant import get_tenant_id, get_tool_execution_context, tenant_queue
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.api.schemas import (
    CrewRunRequest,
    CrewRunResponse,
    CrewSyncRunResponse,
    TaskStatusResponse,
)

logger = get_logger(__name__)
router = APIRouter()


# =========================
# Health Endpoint
# =========================
@router.get("/health")
def health():
    return {"status": "ok"}


# =========================
# Enqueue Crew Task (Async)
# =========================
@router.post("/domain/run", response_model=CrewRunResponse)
def run_crew_api(
    payload: CrewRunRequest,
    context: ToolExecutionContext = Depends(get_tool_execution_context),
    task_store: TaskOwnershipStore = Depends(get_task_store),
):
    """
    Enqueue a Crew task for a tenant.
    Routes task to the tenant-specific Celery queue so dedicated
    per-tenant workers in Kubernetes pick it up — not the default queue.

    Queue format: crewai.<tenant_id>
    Start a matching worker with: worker --tenant-id <tenant_id>
    """
    tenant_id = context.tenant_id
    queue_name = tenant_queue(tenant_id)

    logger.info(
        {
            "event": "crew_task_enqueue_requested",
            "tenant_id": tenant_id,
            "agent_type": payload.agent_type,
            "queue": queue_name,
        }
    )
    try:
        task_id = str(uuid4())
        # Register before dispatch. Ambiguous publish failures retain ownership;
        # they must not accidentally expose a task that was actually published.
        task_store.register(task_id, tenant_id)
        run_crew.apply_async(
            args=[tenant_id, payload.model_dump()],
            task_id=task_id,
            queue=queue_name,
            expires=settings.TASK_QUEUE_TTL,
            retry=False,
        )

        logger.info(
            {
                "event": "crew_task_dispatched",
                "tenant_id": tenant_id,
                "task_id": task_id,
                "queue": queue_name,
            }
        )

        return CrewRunResponse(task_id=task_id, status="Queued")

    except RedisError:
        raise HTTPException(503, "Task storage unavailable")
    except Exception as e:
        logger.error(
            {
                "event": "crew_task_enqueue_failed",
                "tenant_id": tenant_id,
                "error_type": type(e).__name__,
            }
        )
        raise HTTPException(status_code=500, detail="Failed to enqueue task")


# =========================
# Synchronous Crew Run (Testing / Debugging)
# =========================
@router.post("/domain/run-sync", response_model=CrewSyncRunResponse)
def run_crew_sync_api(
    payload: CrewRunRequest,
    context: ToolExecutionContext = Depends(get_tool_execution_context),
):
    """
    Runs a Crew synchronously (blocking call).
    Bypasses Celery entirely — useful for local testing and debugging
    without needing Redis or a running worker.
    """
    tenant_id = context.tenant_id
    try:
        logger.info(
            {
                "event": "crew_task_sync_started",
                "tenant_id": tenant_id,
                "agent_type": payload.agent_type,
            }
        )

        result = CrewRunner.run(tenant_id=tenant_id, payload=payload.model_dump())

        logger.info(
            {
                "event": "crew_task_sync_completed",
                "tenant_id": tenant_id,
                "agent_type": payload.agent_type,
            }
        )

        return CrewSyncRunResponse(
            task_id="sync_run",
            status="Completed",
            output=result["output"],
        )

    except ValueError as ve:
        logger.warning(
            {"event": "crew_task_sync_validation_failed", "tenant_id": tenant_id, "error": str(ve)}
        )
        raise HTTPException(status_code=400, detail=str(ve))

    except Exception as e:
        logger.error(
            {
                "event": "crew_task_sync_failed",
                "tenant_id": tenant_id,
                "error_type": type(e).__name__,
            }
        )
        raise HTTPException(status_code=500, detail="Internal Server Error")


# =========================
# Get Crew Task Status
# =========================
@router.get("/domain/status/{task_id}", response_model=TaskStatusResponse)
def get_task_status(
    task_id: str,
    tenant_id: str = Depends(get_tenant_id),
    task_store: TaskOwnershipStore = Depends(get_task_store),
):
    """
    Retrieve the status of a Crew task.
    Returns current state and result/error if completed.
    """
    try:
        task_store.require_owner(task_id, tenant_id)
        task_result = AsyncResult(task_id, app=celery_app)
        state = task_result.state
    except RedisError:
        raise HTTPException(503, "Task storage unavailable")

    logger.info(
        {
            "event": "task_status_requested",
            "task_id": task_id,
            "tenant_id": tenant_id,
            "state": state,
        }
    )

    response_data = {"task_id": task_id, "status": state, "result": None}

    try:
        if state == "SUCCESS":
            response_data["result"] = jsonable_encoder(task_result.result)

        elif state == "FAILURE":
            error_obj = task_result.result
            response_data["result"] = {
                "error_type": type(error_obj).__name__,
                "error_message": "Task execution failed",
            }

        elif state in ["PENDING", "STARTED", "RETRY"]:
            response_data["result"] = None

    except Exception as e:
        logger.error(
            {
                "event": "task_status_serialization_failed",
                "task_id": task_id,
                "tenant_id": tenant_id,
                "error_type": type(e).__name__,
            }
        )
        raise HTTPException(500, "Task result unavailable")

    return TaskStatusResponse(**response_data)
