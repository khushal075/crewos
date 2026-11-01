"""Public authorized ingestion entry point and safe lifecycle logging."""

import logging
from time import monotonic
from uuid import uuid4

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.ingestion.contracts import DocumentIngestionRequest, StoredDocumentVersion
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
        deduplication, updates, background scheduling, and transport are separate.
        """
        return self._executor.execute(
            context=context,
            action="document.create",
            operation=lambda: self._ingest(request=request, tenant_id=context.tenant_id),
        )

    def _ingest(self, *, request, tenant_id):
        fields = {"tenant_id": tenant_id, "operation_id": str(uuid4())}
        started = monotonic()
        self._logger.info({"event": "document_ingestion_started", **fields})
        try:
            saved = self._coordinator.process(request=request, tenant_id=tenant_id)
        except Exception as error:
            self._logger.error(
                {
                    "event": "document_ingestion_failed",
                    **fields,
                    "error_type": type(error).__name__,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                }
            )
            raise
        self._logger.info(
            {
                "event": "document_ingestion_succeeded",
                **fields,
                "document_id": str(saved.document_id),
                "version": saved.version,
                "chunk_count": len(saved.chunking.chunks),
                "duration_ms": round((monotonic() - started) * 1000, 3),
            }
        )
        return saved
