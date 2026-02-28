# crewos/api/routes.py

from fastapi import HTTPException, APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from celery.result import AsyncResult

from crewos.core.config import settings
from crewos.infrastructure.logging import get_logger
from crewos.infrastructure.celery_app import celery_app
from crewos.services.crew_runner import CrewRunner
from crewos.workers.crew_task import run_crew
from crewos.core.tenant import get_tenant_id
from crewos.api.schemas import CrewRunRequest, CrewRunResponse, TaskStatusResponse

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
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Enqueue a Crew task for a tenant.
    Routes task to the tenant-specific Celery queue so dedicated
    per-tenant workers in Kubernetes pick it up — not the default queue.

    Queue format: crewai.<tenant_id>
    Worker must be started with: -Q crewai.<tenant_id>
    """
    queue_name = f"{settings.CELERY_QUEUE_NAME}.{tenant_id}"

    logger.info({
        "event": "crew_task_enqueued",
        "tenant_id": tenant_id,
        "agent_type": payload.agent_type,
        "queue": queue_name
    })
    print(f"tenant_id: {tenant_id}")
    try:
        task = run_crew.apply_async(
            args=[tenant_id, payload.model_dump()],
            queue=f"{settings.CELERY_QUEUE_NAME}.{tenant_id}"
        )

        logger.info({
            "event": "crew_task_dispatched",
            "tenant_id": tenant_id,
            "task_id": task.id,
            "queue": queue_name
        })

        return CrewRunResponse(task_id=task.id, status="Queued")

    except Exception as e:
        logger.error({
            "event": "crew_task_enqueue_failed",
            "tenant_id": tenant_id,
            "error": str(e)
        }, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to enqueue task")


# =========================
# Synchronous Crew Run (Testing / Debugging)
# =========================
@router.post("/domain/run-sync", response_model=CrewRunResponse)
def run_crew_sync_api(
    payload: CrewRunRequest,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Runs a Crew synchronously (blocking call).
    Bypasses Celery entirely — useful for local testing and debugging
    without needing Redis or a running worker.
    """
    try:
        logger.info({
            "event": "crew_task_sync_started",
            "tenant_id": tenant_id,
            "agent_type": payload.agent_type
        })

        result = CrewRunner.run(
            tenant_id=tenant_id,
            payload=payload.model_dump()
        )

        logger.info({
            "event": "crew_task_sync_completed",
            "tenant_id": tenant_id,
            "agent_type": payload.agent_type,
            "output_summary": str(result.get("output"))[:200]
        })

        return CrewRunResponse(
            task_id="sync_run",
            status="Completed",
            **result
        )

    except ValueError as ve:
        logger.warning({
            "event": "crew_task_sync_validation_failed",
            "tenant_id": tenant_id,
            "error": str(ve)
        })
        raise HTTPException(status_code=400, detail=str(ve))

    except Exception as e:
        logger.error({
            "event": "crew_task_sync_failed",
            "tenant_id": tenant_id,
            "error": str(e)
        }, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


# =========================
# Get Crew Task Status
# =========================
@router.get("/domain/status/{task_id}", response_model=TaskStatusResponse)
def get_task_status(
    task_id: str,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Retrieve the status of a Crew task.
    Returns current state and result/error if completed.
    """
    task_result = AsyncResult(task_id, app=celery_app)
    state = task_result.state

    logger.info({
        "event": "task_status_requested",
        "task_id": task_id,
        "tenant_id": tenant_id,
        "state": state
    })

    response_data = {
        "task_id": task_id,
        "status": state,
        "result": None
    }

    try:
        if state == "SUCCESS":
            response_data["result"] = jsonable_encoder(task_result.result)

        elif state == "FAILURE":
            error_obj = task_result.result
            response_data["result"] = {
                "error_type": type(error_obj).__name__,
                "error_message": str(error_obj)
            }

        elif state in ["PENDING", "STARTED", "RETRY"]:
            response_data["result"] = None

    except Exception as e:
        logger.error({
            "event": "task_status_serialization_failed",
            "task_id": task_id,
            "tenant_id": tenant_id,
            "error": str(e)
        }, exc_info=True)
        response_data["result"] = str(task_result.result)

    return TaskStatusResponse(**response_data)