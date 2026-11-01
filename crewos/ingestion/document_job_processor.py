"""Execute a claimed job through the facade and atomically save its outcome."""

from pydantic import ValidationError
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.chunking import ChunkingLimitExceeded
from crewos.ingestion.bootstrap import build_plain_text_ingestion_facade
from crewos.ingestion.document_parser import DocumentParseError
from crewos.ingestion.document_store import DocumentNotFound, DocumentVersionConflict
from crewos.ingestion.document_jobs import DocumentJobConflict


class _ClaimedDocumentStore:
    def __init__(self, store, claim):
        self._store, self._claim = store, claim

    def get(self, **kwargs):
        return self._store.get(**kwargs)

    def delete(self, **kwargs):
        return self._store.delete(**kwargs)

    def create_version(self, record):
        return self._store.commit_job(self._claim, record)


def process_document_job(*, store, job_id: str, context):
    claim = store.claim_job(job_id, context.tenant_id)
    if claim is None:
        return store.get_job(job_id, context.tenant_id)
    facade = build_plain_text_ingestion_facade(
        store=_ClaimedDocumentStore(store, claim),
        executor=PolicyEnforcedExecutor(PermissionPolicyService()),
        chunking_options=claim.options,
    )
    try:
        if claim.job.operation == "create":
            facade.ingest(request=claim.request, context=context)
        else:
            facade.update(request=claim.request, context=context)
    except Exception as error:
        code = (
            "PermissionDenied"
            if isinstance(error, PolicyDeniedError)
            else (
                "VersionConflict"
                if isinstance(error, DocumentVersionConflict)
                else (
                    "NotFound"
                    if isinstance(error, DocumentNotFound)
                    else (
                        "InvalidDocument"
                        if isinstance(error, (DocumentParseError, ValidationError))
                        else (
                            "ResourceLimit"
                            if isinstance(error, ChunkingLimitExceeded)
                            else (
                                "AttemptLost"
                                if isinstance(error, DocumentJobConflict)
                                else "ProcessingFailed"
                            )
                        )
                    )
                )
            )
        )
        store.fail_job(claim, code)
    return store.get_job(job_id, context.tenant_id)
