"""Runtime composition; API and workers share one local document database."""

from functools import lru_cache
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.core.config import settings
from crewos.ingestion import (
    DocumentSubmissionFacade,
    build_plain_text_ingestion_facade,
    build_sqlite_document_store,
    process_document_job,
)
from crewos.infrastructure.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_document_store():
    return build_sqlite_document_store(
        settings.DOCUMENT_DB_PATH,
        job_lease_seconds=settings.DOCUMENT_JOB_LEASE_SECONDS,
        job_max_attempts=settings.DOCUMENT_JOB_MAX_ATTEMPTS,
    )


def get_document_executor():
    return PolicyEnforcedExecutor(PermissionPolicyService(), logger=logger)


def get_document_facade():
    return build_plain_text_ingestion_facade(
        store=get_document_store(), executor=get_document_executor()
    )


def get_document_submission_facade():
    return DocumentSubmissionFacade(store=get_document_store(), executor=get_document_executor())


def execute_document_job(job_id: str, tenant_id: str):
    # Queue messages convey identity, not grants. Re-evaluate active server config.
    token = settings.TENANT_API_KEYS.get(tenant_id)
    permissions = (
        settings.TENANT_PERMISSIONS.get(tenant_id, frozenset())
        if token and token.get_secret_value()
        else frozenset()
    )
    context = ToolExecutionContext(tenant_id=tenant_id, permissions=permissions)
    result = process_document_job(store=get_document_store(), job_id=job_id, context=context)
    logger.info(
        {
            "event": "document_job_observed",
            "job_id": job_id,
            "tenant_id": tenant_id,
            "state": result.state,
            "attempts": result.attempts,
        }
    )
    return result
