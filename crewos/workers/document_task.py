"""Background transport: IDs only, durable outcomes, no source in the broker."""

from celery.exceptions import Ignore
from pydantic import BaseModel, ConfigDict, Field, UUID4

from crewos.core.document_setup import execute_document_job
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.celery_app import celery_app
from crewos.ingestion import DocumentJobConflict


class DocumentJobDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID4
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")


@celery_app.task(
    name="crewos.workers.document_task.run_document_job",
    bind=True,
    ignore_result=True,
    resultrepr_maxsize=0,
    max_retries=0,
    autoretry_for=(),
    soft_time_limit=120,
    time_limit=150,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_document_job(self, payload):
    delivery = DocumentJobDelivery.model_validate(payload)
    if self.request.id != str(delivery.job_id):
        raise ValueError("Unexpected document delivery ID")
    if (self.request.delivery_info or {}).get("routing_key") != tenant_queue(delivery.tenant_id):
        raise ValueError("Unexpected document tenant queue")
    try:
        result = execute_document_job(str(delivery.job_id), delivery.tenant_id)
    except DocumentJobConflict:
        raise Ignore() from None
    return {"job_id": str(result.job_id), "state": result.state}
