"""Authorized durable submission/status boundary, independent of queue transport."""

import logging

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions
from crewos.ingestion.document_jobs import (
    DocumentJobStoreInterface,
    DocumentSubmission,
    DocumentJob,
)
from crewos.ingestion.contracts import DocumentIngestionRequest, DocumentUpdateRequest


class DocumentSubmissionFacade:
    def __init__(
        self,
        *,
        store: DocumentJobStoreInterface,
        executor: PolicyEnforcedExecutor,
        options: ChunkingOptions | None = None,
    ):
        self._store, self._executor = store, executor
        self._logger = logging.getLogger(__name__)
        self._options = options if options is not None else ChunkingOptions()

    def submit_create(
        self,
        *,
        request: DocumentIngestionRequest,
        context: ToolExecutionContext,
        idempotency_key: str,
    ) -> DocumentSubmission:
        return self._submit(request, context, idempotency_key, "create")

    def submit_update(
        self, *, request: DocumentUpdateRequest, context: ToolExecutionContext, idempotency_key: str
    ) -> DocumentSubmission:
        return self._submit(request, context, idempotency_key, "update")

    def _submit(self, request, context, key, operation):
        def register():
            submission = self._store.submit_job(
                tenant_id=context.tenant_id,
                request=request,
                operation=operation,
                options=self._options,
                idempotency_key=key,
            )
            self._logger.info(
                {
                    "event": "document_submission_registered",
                    "job_id": str(submission.job.job_id),
                    "tenant_id": context.tenant_id,
                    "operation": operation,
                    "replayed": submission.replayed,
                    "state": submission.job.state,
                }
            )
            return submission

        return self._executor.execute(
            context=context, action=f"document.{operation}", operation=register
        )

    def status(self, *, job_id: str, context: ToolExecutionContext) -> DocumentJob:
        return self._executor.execute(
            context=context,
            action="document.job.read",
            operation=lambda: self._store.get_job(job_id, context.tenant_id),
        )

    def retry(self, *, job_id: str, context: ToolExecutionContext) -> DocumentJob:
        def retry():
            job = self._store.get_job(job_id, context.tenant_id)
            return self._executor.execute(
                context=context,
                action=f"document.{job.operation}",
                operation=lambda: self._store.retry_job(job_id, context.tenant_id),
            )

        return self._executor.execute(context=context, action="document.job.retry", operation=retry)
