"""Authenticated document lifecycle and durable sync/background submissions."""

import sqlite3
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import UUID4, ValidationError

from crewos.api.document_schemas import DocumentCreateBody, DocumentUpdateBody
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEvaluationError
from crewos.chunking import ChunkingLimitExceeded
from crewos.core import document_setup
from crewos.core.config import settings
from crewos.core.tenant import get_tool_execution_context, tenant_queue
from crewos.core.document_setup import (
    get_document_store as _get_document_store,
    get_document_executor,
)
from crewos.infrastructure.logging import get_logger
from crewos.ingestion import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    DocumentReadRequest,
    DocumentDeleteRequest,
    DocumentSubmissionFacade,
    DocumentSubmission,
    DocumentJob,
    StoredDocumentVersion,
    DocumentNotFound,
    DocumentVersionConflict,
    DocumentStorageCorruption,
    DocumentJobConflict,
    DocumentJobNotFound,
    DocumentParseError,
    build_plain_text_ingestion_facade,
)
from crewos.workers.document_task import run_document_job

logger = get_logger(__name__)


class DocumentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Default validation details can echo source text or authority claims.
                raise HTTPException(422, "Invalid document request") from None

        return safe_handler


router = APIRouter(prefix="/documents", route_class=DocumentRoute)


def document_call(operation):
    try:
        return operation()
    except (PolicyDeniedError, PermissionError):
        raise HTTPException(403, "Document operation denied") from None
    except PolicyEvaluationError:
        raise HTTPException(503, "Document authorization unavailable") from None
    except (DocumentNotFound, DocumentJobNotFound):
        raise HTTPException(404, "Document or job unavailable") from None
    except (DocumentVersionConflict, DocumentJobConflict):
        raise HTTPException(409, "Document operation conflicts with existing state") from None
    except (DocumentParseError, ChunkingLimitExceeded, ValidationError):
        raise HTTPException(422, "Invalid document input or resource limits") from None
    except (sqlite3.Error, DocumentStorageCorruption):
        raise HTTPException(503, "Document storage unavailable") from None
    except HTTPException:
        raise
    except Exception as error:
        logger.error({"event": "document_api_failed", "error_type": type(error).__name__})
        raise HTTPException(500, "Document operation failed") from None


def get_document_store():
    return document_call(_get_document_store)


def publish_job(store, job):
    if job.state not in {"queued", "running"}:
        return
    try:
        run_document_job.apply_async(
            args=[{"job_id": str(job.job_id), "tenant_id": job.tenant_id}],
            task_id=str(job.job_id),
            queue=tenant_queue(job.tenant_id),
            expires=settings.TASK_QUEUE_TTL,
            retry=False,
        )
        store.mark_job_published(str(job.job_id), job.tenant_id)
        logger.info(
            {
                "event": "document_job_published",
                "job_id": str(job.job_id),
                "tenant_id": job.tenant_id,
                "queue": tenant_queue(job.tenant_id),
            }
        )
    except Exception as error:
        logger.error(
            {
                "event": "document_publication_unconfirmed",
                "job_id": str(job.job_id),
                "error_type": type(error).__name__,
            }
        )
        raise HTTPException(
            503, "Publication unconfirmed; retry with the same idempotency key"
        ) from None


def perform_submission(store, submission, background, response):
    if background:
        publish_job(store, submission.job)
        job = store.get_job(str(submission.job.job_id), submission.job.tenant_id)
    else:
        job = document_setup.execute_document_job(
            str(submission.job.job_id), submission.job.tenant_id
        )
    response.status_code = 202 if job.state in {"queued", "running"} else 200
    if job.state == "failed" and not background:
        response.status_code = {
            "PermissionDenied": 403,
            "NotFound": 404,
            "VersionConflict": 409,
            "InvalidDocument": 422,
            "ResourceLimit": 422,
            "AttemptLimit": 409,
            "DocumentDeleted": 409,
        }.get(job.error_code, 500)
    return DocumentSubmission(job=job, replayed=submission.replayed)


@router.post("", response_model=DocumentSubmission)
def create_document(
    payload: DocumentCreateBody,
    response: Response,
    background: bool = False,
    context=Depends(get_tool_execution_context),
    store=Depends(get_document_store),
    executor=Depends(get_document_executor),
    idempotency_key: str = Header(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
):
    def submit():
        facade = DocumentSubmissionFacade(store=store, executor=executor)
        submission = facade.submit_create(
            request=DocumentIngestionRequest(
                content=payload.text.encode("utf-8"),
                title=payload.title,
                source_ref=payload.source_ref,
            ),
            context=context,
            idempotency_key=idempotency_key,
        )
        return perform_submission(store, submission, background, response)

    return document_call(submit)


@router.put("/{document_id}", response_model=DocumentSubmission)
def update_document(
    document_id: UUID4,
    payload: DocumentUpdateBody,
    response: Response,
    background: bool = False,
    context=Depends(get_tool_execution_context),
    store=Depends(get_document_store),
    executor=Depends(get_document_executor),
    idempotency_key: str = Header(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
):
    def submit():
        facade = DocumentSubmissionFacade(store=store, executor=executor)
        submission = facade.submit_update(
            request=DocumentUpdateRequest(
                document_id=document_id,
                expected_version=payload.expected_version,
                content=payload.text.encode("utf-8"),
                title=payload.title,
                source_ref=payload.source_ref,
            ),
            context=context,
            idempotency_key=idempotency_key,
        )
        return perform_submission(store, submission, background, response)

    return document_call(submit)


@router.get("/jobs/{job_id}", response_model=DocumentJob)
def document_job(
    job_id: UUID4,
    context=Depends(get_tool_execution_context),
    store=Depends(get_document_store),
    executor=Depends(get_document_executor),
):
    return document_call(
        lambda: DocumentSubmissionFacade(store=store, executor=executor).status(
            job_id=str(job_id), context=context
        )
    )


@router.post("/jobs/{job_id}/retry", response_model=DocumentJob, status_code=202)
def retry_document_job(
    job_id: UUID4,
    context=Depends(get_tool_execution_context),
    store=Depends(get_document_store),
    executor=Depends(get_document_executor),
):
    def retry():
        job = DocumentSubmissionFacade(store=store, executor=executor).retry(
            job_id=str(job_id), context=context
        )
        publish_job(store, job)
        return store.get_job(str(job_id), context.tenant_id)

    return document_call(retry)


@router.get("/{document_id}", response_model=StoredDocumentVersion)
def read_document(
    document_id: UUID4,
    version: int | None = None,
    context=Depends(get_tool_execution_context),
    store=Depends(get_document_store),
    executor=Depends(get_document_executor),
):
    return document_call(
        lambda: build_plain_text_ingestion_facade(store=store, executor=executor).read(
            request=DocumentReadRequest(document_id=document_id, version=version), context=context
        )
    )


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: UUID4,
    context=Depends(get_tool_execution_context),
    store=Depends(get_document_store),
    executor=Depends(get_document_executor),
):
    document_call(
        lambda: build_plain_text_ingestion_facade(store=store, executor=executor).delete(
            request=DocumentDeleteRequest(document_id=document_id), context=context
        )
    )
    return Response(status_code=204)
