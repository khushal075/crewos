"""Internal cleanup runtime; validate configured scope before any database access."""

from crewos.application.dtos.document_cleanup_result import CleanupOutcome
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.use_cases.process_document_cleanup import DocumentCleanupProcessor
from crewos.core.config import settings
from crewos.core.document_setup import get_document_store
from crewos.infrastructure.logging import get_logger
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore

logger = get_logger(__name__)


def require_cleanup_tenant(tenant_id: str) -> str:
    tenant = ToolExecutionContext(tenant_id=tenant_id).tenant_id
    if tenant not in settings.DOCUMENT_CLEANUP_TENANTS:
        raise PermissionError("Tenant is not configured for the cleanup service")
    return tenant


def run_cleanup_pass(*, tenant_id: str):
    tenant = require_cleanup_tenant(tenant_id)
    processor = DocumentCleanupProcessor(
        outbox=get_document_store(),
        target_factory=lambda: SQLiteChunkEmbeddingStore(settings.EMBEDDING_DB_PATH),
        lease_seconds=settings.DOCUMENT_CLEANUP_LEASE_SECONDS,
        retry_base_seconds=settings.DOCUMENT_CLEANUP_RETRY_BASE_SECONDS,
        retry_max_seconds=settings.DOCUMENT_CLEANUP_RETRY_MAX_SECONDS,
        alert_after_attempts=settings.DOCUMENT_CLEANUP_ALERT_AFTER_ATTEMPTS,
        logger=logger,
    )
    outcomes = []
    for _ in range(settings.DOCUMENT_CLEANUP_BATCH_SIZE):
        # Recheck service scope before each new claim.
        require_cleanup_tenant(tenant)
        outcome = processor.process_one(tenant_id=tenant)
        outcomes.append(outcome)
        if outcome.outcome is CleanupOutcome.IDLE:
            break
    return tuple(outcomes)
