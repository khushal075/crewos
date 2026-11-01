"""Public authorized ingestion entry point and safe lifecycle logging."""

from collections.abc import Callable
import logging
from time import monotonic
from uuid import uuid4

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.ingestion.contracts import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    DocumentReadRequest,
    DocumentDeleteRequest,
    DocumentDeleted,
    StoredDocumentVersion,
)
from crewos.ingestion.document_processing_coordinator import DocumentProcessingCoordinator


class DocumentIngestionFacade:
    def __init__(
        self,
        *,
        coordinator: DocumentProcessingCoordinator,
        executor: PolicyEnforcedExecutor,
        logger: logging.Logger | None = None,
    ):
        self._coordinator = coordinator
        self._executor = executor
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def ingest(
        self, *, request: DocumentIngestionRequest, context: ToolExecutionContext
    ) -> StoredDocumentVersion:
        """Require caller-established authenticated context; check document.create.

        Each successful call creates a new document/version 1. Submission
        deduplication, background scheduling, and transport are separate.
        """
        return self._executor.execute(
            context=context,
            action="document.create",
            operation=lambda: self._record_lifecycle(
                tenant_id=context.tenant_id,
                event_prefix="document_ingestion",
                operation=lambda: self._coordinator.process(
                    request=request, tenant_id=context.tenant_id
                ),
            ),
        )

    def update(
        self, *, request: DocumentUpdateRequest, context: ToolExecutionContext
    ) -> StoredDocumentVersion:
        """Require document.update; preserve identity and append a new snapshot."""
        return self._executor.execute(
            context=context,
            action="document.update",
            operation=lambda: self._record_lifecycle(
                tenant_id=context.tenant_id,
                event_prefix="document_update",
                operation=lambda: self._coordinator.update(
                    request=request, tenant_id=context.tenant_id
                ),
            ),
        )

    def read(
        self, *, request: DocumentReadRequest, context: ToolExecutionContext
    ) -> StoredDocumentVersion:
        return self._executor.execute(
            context=context,
            action="document.read",
            operation=lambda: self._record_lifecycle(
                tenant_id=context.tenant_id,
                event_prefix="document_read",
                operation=lambda: self._coordinator.read(
                    request=request, tenant_id=context.tenant_id
                ),
            ),
        )

    def delete(
        self, *, request: DocumentDeleteRequest, context: ToolExecutionContext
    ) -> DocumentDeleted:
        return self._executor.execute(
            context=context,
            action="document.delete",
            operation=lambda: self._record_lifecycle(
                tenant_id=context.tenant_id,
                event_prefix="document_delete",
                operation=lambda: self._coordinator.delete(
                    request=request, tenant_id=context.tenant_id
                ),
            ),
        )

    def _record_lifecycle(
        self,
        *,
        tenant_id: str,
        event_prefix: str,
        operation: Callable[[], StoredDocumentVersion | DocumentDeleted],
    ) -> StoredDocumentVersion | DocumentDeleted:
        fields = {"tenant_id": tenant_id, "operation_id": str(uuid4())}
        started = monotonic()
        self._logger.info({"event": f"{event_prefix}_started", **fields})
        try:
            saved = operation()
        except Exception as error:
            self._logger.error(
                {
                    "event": f"{event_prefix}_failed",
                    **fields,
                    "error_type": type(error).__name__,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                }
            )
            raise
        self._logger.info(
            {
                "event": f"{event_prefix}_succeeded",
                **fields,
                "document_id": str(saved.document_id),
                "version": saved.version if isinstance(saved, StoredDocumentVersion) else None,
                "chunk_count": (
                    len(saved.chunking.chunks) if isinstance(saved, StoredDocumentVersion) else None
                ),
                "duration_ms": round((monotonic() - started) * 1000, 3),
            }
        )
        return saved
