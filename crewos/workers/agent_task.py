"""Celery transport adapter for registered agent jobs; no automatic retries."""

from crewos.application.dtos.agent_run_job import AgentRunJob
from crewos.core.agent_job_runner import execute_agent_job
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.celery_app import celery_app
from crewos.infrastructure.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    name="crewos.workers.agent_task.run_agent",
    bind=True,
    max_retries=0,
    autoretry_for=(),
)
def run_agent(self, payload: dict) -> dict:
    job = AgentRunJob.model_validate(payload)
    run_id = str(job.agent_run_id)
    if self.request.id != run_id:
        raise ValueError("Celery delivery ID does not match agent run ID")
    delivery = self.request.delivery_info or {}
    if delivery.get("routing_key") != tenant_queue(job.tenant_id):
        raise ValueError("Agent job was delivered with an unexpected tenant routing key")

    fields = {"agent_run_id": run_id, "tenant_id": job.tenant_id}
    logger.info({"event": "agent_job_received", **fields})
    try:
        result = execute_agent_job(job.model_dump(mode="json"))
        logger.info({"event": "agent_job_completed", **fields})
        return result.model_dump(mode="json")
    except Exception as error:
        logger.error({"event": "agent_job_failed", **fields, "error_type": type(error).__name__})
        raise
