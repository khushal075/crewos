"""Inference-only workflow; retries may repeat generation, never external writes."""

from typing import Dict, Any
import json
from crewos.core.config import settings
from crewos.core.tenant import tenant_context, tenant_queue
from crewos.infrastructure.celery_app import celery_app
from crewos.services.crew_runner import CrewRunner
from crewos.infrastructure.logging import get_logger
from crewos.utils.retry import is_retryable, retry_delay

logger = get_logger(__name__)


@celery_app.task(
    name="crewos.workers.crew_task.run_crew",
    bind=True,
    max_retries=settings.LLM_MAX_RETRIES,
)
def run_crew(self, tenant_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    tenant_queue(tenant_id)
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("Payload must be an object")
    payload = dict(payload)
    payload.setdefault("agent_type", "research")
    input_data = payload.get("input", {})
    message = input_data.get("message") if isinstance(input_data, dict) else None
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Message is required")
    logger.info({"event": "crew_task_started", "tenant_id": tenant_id, "task_id": self.request.id})
    try:
        with tenant_context(tenant_id):
            result = CrewRunner.run(tenant_id=tenant_id, payload=payload)
        logger.info(
            {"event": "crew_task_completed", "tenant_id": tenant_id, "task_id": self.request.id}
        )
        return result
    except Exception as error:
        if is_retryable(error) and self.request.retries < self.max_retries:
            delay = retry_delay(self.request.retries, error)
            if delay is not None:
                logger.warning(
                    {
                        "event": "crew_task_retry",
                        "tenant_id": tenant_id,
                        "error_type": type(error).__name__,
                        "delay": delay,
                    }
                )
                raise self.retry(exc=error, countdown=delay)
        logger.error(
            {
                "event": "crew_task_failed",
                "tenant_id": tenant_id,
                "error_type": type(error).__name__,
            }
        )
        raise
