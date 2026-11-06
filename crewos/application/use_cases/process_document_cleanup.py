"""Deliver one durable deletion event to idempotent vector-cache cleanup."""

from collections.abc import Callable
import logging
from math import isfinite
from time import monotonic

from pydantic import ValidationError

from crewos.application.dtos.document_cleanup_result import CleanupOutcome, DocumentCleanupResult
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.ingestion import (
    CleanupFailureCode,
    DocumentCleanupClaim,
    DocumentDeletedEvent,
    DocumentVersionCreatedEvent,
    CleanupLeaseLost,
    CleanupOutboxCorruption,
    DocumentCleanupOutboxInterface,
)
from crewos.retrieval.chunk_embedding_store import ChunkEmbeddingStoreInterface


class DocumentCleanupProcessor:
    def __init__(
        self,
        *,
        outbox: DocumentCleanupOutboxInterface,
        target_factory: Callable[[], ChunkEmbeddingStoreInterface],
        lease_seconds: float = 30,
        retry_base_seconds: float = 10,
        retry_max_seconds: float = 300,
        alert_after_attempts: int = 5,
        logger: logging.Logger | None = None,
    ):
        if (
            type(lease_seconds) not in (int, float)
            or lease_seconds <= 0
            or lease_seconds > 300
            or not isfinite(lease_seconds)
        ):
            raise ValueError("Invalid cleanup lease duration")
        if (
            any(
                type(value) not in (int, float) or value < 0 or value > 86400 or not isfinite(value)
                for value in (retry_base_seconds, retry_max_seconds)
            )
            or retry_base_seconds > retry_max_seconds
        ):
            raise ValueError("Invalid cleanup retry bounds")
        if type(alert_after_attempts) is not int or alert_after_attempts < 1:
            raise ValueError("Invalid cleanup alert threshold")
        self._outbox, self._target_factory = outbox, target_factory
        self._lease, self._base, self._maximum = (
            lease_seconds,
            retry_base_seconds,
            retry_max_seconds,
        )
        self._alert_after = alert_after_attempts
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def process_one(self, *, tenant_id: str) -> DocumentCleanupResult:
        """Internal use case: runtime must authorize the configured service tenant."""
        tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
        claimed = self._outbox.claim_next(tenant_id=tenant_id, lease_seconds=self._lease)
        if claimed is None:
            return DocumentCleanupResult(outcome=CleanupOutcome.IDLE)
        try:
            if not isinstance(claimed, DocumentCleanupClaim):
                raise TypeError("Expected cleanup claim")
            claim = DocumentCleanupClaim.model_validate_json(claimed.model_dump_json())
        except (ValidationError, TypeError):
            raise CleanupOutboxCorruption("Invalid cleanup claim") from None
        if claim.event.tenant_id != tenant_id:
            raise CleanupOutboxCorruption("Cleanup claim returned another tenant")
        started = monotonic()
        fields = {
            "tenant_id": tenant_id,
            "event_id": str(claim.event.event_id),
            "attempt": claim.attempt,
            "event_type": claim.event.event_type,
        }

        def result(outcome):
            self._logger.info(
                {
                    "event": "document_cleanup_processed",
                    **fields,
                    "outcome": outcome.value,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                }
            )
            return DocumentCleanupResult(
                outcome=outcome, event_id=claim.event.event_id, attempt=claim.attempt
            )

        try:
            target = self._target_factory()
            if isinstance(claim.event, DocumentDeletedEvent):
                target.delete_document(tenant_id=tenant_id, document_id=claim.event.document_id)
            elif isinstance(claim.event, DocumentVersionCreatedEvent):
                target.retain_from_version(
                    tenant_id=tenant_id,
                    document_id=claim.event.document_id,
                    min_version=claim.event.document_version,
                )
            else:
                raise CleanupOutboxCorruption("Unsupported cleanup event")
        except Exception as error:
            code = (
                CleanupFailureCode.STORAGE_UNAVAILABLE
                if isinstance(error, OSError)
                else CleanupFailureCode.CLEANUP_FAILED
            )
            # Cap the exponent independently of unbounded delivery attempts.
            delay = min(self._maximum, self._base * 2 ** min(claim.attempt - 1, 16))
            self._logger.log(
                logging.WARNING if claim.attempt >= self._alert_after else logging.INFO,
                {
                    "event": "document_cleanup_target_failed",
                    **fields,
                    "error_type": type(error).__name__,
                    "error_code": code.value,
                    "retry_delay_seconds": delay,
                },
            )
            try:
                self._outbox.retry(
                    tenant_id=tenant_id,
                    event_id=claim.event.event_id,
                    lease_token=claim.lease_token,
                    delay_seconds=delay,
                    error_code=code,
                )
            except CleanupLeaseLost:
                return result(CleanupOutcome.LEASE_LOST)
            # If retry persistence fails, propagate; the lease can still expire/recover.
            return result(CleanupOutcome.RETRY_SCHEDULED)
        try:
            self._outbox.complete(
                tenant_id=tenant_id, event_id=claim.event.event_id, lease_token=claim.lease_token
            )
        except CleanupLeaseLost:
            return result(CleanupOutcome.LEASE_LOST)
        # Other acknowledgement failures propagate without falsely reporting success.
        return result(CleanupOutcome.COMPLETED)
