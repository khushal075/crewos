"""Real SQLite verifies durable idempotency and atomic job/document outcomes."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
from uuid import uuid4

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    DocumentInput,
    StoredDocumentVersion,
    DocumentSubmissionFacade,
    DocumentJobConflict,
    DocumentJobNotFound,
    DocumentNotFound,
    DocumentStorageCorruption,
    build_sqlite_document_store,
    process_document_job,
)


def context(
    tenant="tenant_a",
    permissions=("document.create", "document.update", "document.job.read", "document.job.retry"),
):
    return ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions))


def source(text=b"Check delivery logs."):
    return DocumentIngestionRequest(content=text, title="CRM runbook")


def submit(store, *, key="request-1", tenant="tenant_a", request=None, operation="create"):
    return store.submit_job(
        tenant_id=tenant,
        request=request or source(),
        operation=operation,
        options=ChunkingOptions(),
        idempotency_key=key,
    )


def snapshot(claim):
    text = claim.request.content.decode()
    return StoredDocumentVersion(
        tenant_id=claim.job.tenant_id,
        document_id=claim.job.document_id,
        version=1 if claim.job.operation == "create" else claim.request.expected_version + 1,
        document=DocumentInput(text=text, title=claim.request.title),
        chunking=build_character_chunking().chunk(ChunkingRequest(text=text)),
    )


@pytest.fixture
def store(tmp_path):
    return build_sqlite_document_store(tmp_path / "docs.sqlite3")


def test_completed_submission_replays_after_reopen_without_duplicate_document(store):
    submission = submit(store)
    result = process_document_job(store=store, job_id=str(submission.job.job_id), context=context())
    assert result.state == "succeeded"
    reopened = build_sqlite_document_store(store.path)
    replay = submit(reopened)
    assert replay.replayed
    assert replay.job.job_id == result.job_id
    assert replay.job.document_id == result.document_id
    assert (
        process_document_job(store=reopened, job_id=str(result.job_id), context=context()).attempts
        == 1
    )
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0] == 1
        assert db.execute("SELECT payload FROM document_jobs").fetchone()[0] is None


def test_key_conflicts_on_changed_content_or_operation_but_is_tenant_scoped(store):
    original = submit(store)
    with pytest.raises(DocumentJobConflict):
        submit(store, request=source(b"Changed"))
    with pytest.raises(DocumentJobConflict):
        submit(
            store,
            operation="update",
            request=DocumentUpdateRequest(
                **source().model_dump(), document_id=uuid4(), expected_version=1
            ),
        )
    other = submit(store, tenant="tenant_b")
    assert other.job.job_id != original.job.job_id
    with pytest.raises(DocumentJobNotFound):
        store.get_job(str(original.job.job_id), "tenant_b")


def test_concurrent_submissions_share_one_registered_job(store):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(store), range(2)))
    assert results[0].job.job_id == results[1].job.job_id
    assert sorted(r.replayed for r in results) == [False, True]


def test_active_attempt_blocks_duplicate_and_expired_attempt_is_fenced(store):
    job = submit(store).job
    first = store.claim_job(str(job.job_id), "tenant_a")
    with pytest.raises(DocumentJobConflict):
        store.claim_job(str(job.job_id), "tenant_a")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE document_jobs SET lease_until=?", (time.time() - 1,))
    second = store.claim_job(str(job.job_id), "tenant_a")
    with pytest.raises(DocumentJobConflict):
        store.commit_job(first, snapshot(first))
    store.fail_job(first, "ProcessingFailed")
    assert store.get_job(str(job.job_id), "tenant_a").state == "running"
    store.commit_job(second, snapshot(second))
    assert store.get_job(str(job.job_id), "tenant_a").attempts == 2


def test_document_insert_and_success_receipt_rollback_together(store):
    job = submit(store).job
    claim = store.claim_job(str(job.job_id), "tenant_a")
    with sqlite3.connect(store.path) as db:
        db.execute(
            """CREATE TRIGGER fail_success AFTER UPDATE OF state ON document_jobs
            WHEN NEW.state='succeeded' BEGIN SELECT RAISE(FAIL,'injected failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_job(claim, snapshot(claim))
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=job.document_id)
    assert store.get_job(str(job.job_id), "tenant_a").state == "running"
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fail_success")
    store.commit_job(claim, snapshot(claim))
    assert store.get_job(str(job.job_id), "tenant_a").state == "succeeded"


def test_submission_status_and_retry_permissions_are_separate(store):
    facade = DocumentSubmissionFacade(
        store=store, executor=PolicyEnforcedExecutor(PermissionPolicyService())
    )
    with pytest.raises(PolicyDeniedError):
        facade.submit_create(
            request=source(), context=context(permissions=()), idempotency_key="denied"
        )
    job = facade.submit_create(request=source(), context=context(), idempotency_key="allowed").job
    with pytest.raises(PolicyDeniedError):
        facade.status(job_id=str(job.job_id), context=context(permissions=("document.create",)))
    claim = store.claim_job(str(job.job_id), "tenant_a")
    store.fail_job(claim, "ProcessingFailed")
    with pytest.raises(PolicyDeniedError):
        facade.retry(job_id=str(job.job_id), context=context(permissions=("document.job.retry",)))
    assert facade.retry(job_id=str(job.job_id), context=context()).state == "queued"


def test_revoked_execution_grant_fails_durably_and_explicit_retry_is_bounded(tmp_path):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3", job_max_attempts=2)
    job = submit(store).job
    facade = DocumentSubmissionFacade(
        store=store, executor=PolicyEnforcedExecutor(PermissionPolicyService())
    )
    for attempt in (1, 2):
        result = process_document_job(
            store=store, job_id=str(job.job_id), context=context(permissions=())
        )
        assert result.state == "failed" and result.error_code == "PermissionDenied"
        assert result.attempts == attempt
        if attempt == 1:
            facade.retry(job_id=str(job.job_id), context=context())
    with pytest.raises(DocumentJobConflict):
        facade.retry(job_id=str(job.job_id), context=context())
    assert (
        process_document_job(store=store, job_id=str(job.job_id), context=context()).state
        == "failed"
    )


def test_delete_fences_pending_updates_and_replay_does_not_restore_document(store):
    job = submit(store).job
    result = process_document_job(store=store, job_id=str(job.job_id), context=context())
    edit = DocumentUpdateRequest(
        **source(b"Replacement").model_dump(), document_id=job.document_id, expected_version=1
    )
    update_job = submit(store, key="edit", operation="update", request=edit).job
    claim = store.claim_job(str(update_job.job_id), "tenant_a")
    store.delete(tenant_id="tenant_a", document_id=result.document_id)
    with pytest.raises(DocumentJobConflict):
        store.commit_job(claim, snapshot(claim))
    assert store.get_job(str(update_job.job_id), "tenant_a").error_code == "DocumentDeleted"
    assert not submit(store).job.result_available
    assert (
        process_document_job(store=store, job_id=str(job.job_id), context=context()).state
        == "succeeded"
    )
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=job.document_id)
    with pytest.raises(DocumentJobConflict):
        store.retry_job(str(update_job.job_id), "tenant_a")
    with sqlite3.connect(store.path) as db:
        assert all(row[0] is None for row in db.execute("SELECT payload FROM document_jobs"))


def test_version_one_database_migrates_without_losing_snapshots(store):
    job = submit(store).job
    result = process_document_job(store=store, job_id=str(job.job_id), context=context())
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE document_jobs")
        db.execute("PRAGMA user_version=1")
    reopened = build_sqlite_document_store(store.path)
    assert reopened.get(tenant_id="tenant_a", document_id=result.document_id).version == 1
    assert submit(reopened).job.state == "queued"


def test_corrupt_payload_is_not_executed_or_echoed(store):
    job = submit(store).job
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE document_jobs SET payload='private-corruption'")
    with pytest.raises(DocumentStorageCorruption) as error:
        store.claim_job(str(job.job_id), "tenant_a")
    assert "private-corruption" not in str(error.value)


@pytest.mark.parametrize("key", ["", "x" * 129, "with spaces", "é"])
def test_invalid_idempotency_keys_are_rejected(store, key):
    with pytest.raises(ValueError):
        submit(store, key=key)


def test_expired_last_attempt_exhausts_without_running_again(tmp_path):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3", job_max_attempts=1)
    job = submit(store).job
    store.claim_job(str(job.job_id), "tenant_a")
    assert store.get_job(str(job.job_id), "tenant_a").lease_active
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE document_jobs SET lease_until=?", (time.time() - 1,))
    assert not store.get_job(str(job.job_id), "tenant_a").lease_active
    assert store.claim_job(str(job.job_id), "tenant_a") is None
    assert store.get_job(str(job.job_id), "tenant_a").error_code == "AttemptLimit"
    with pytest.raises(DocumentJobConflict):
        store.retry_job(str(job.job_id), "tenant_a")
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=job.document_id)


def test_active_job_cannot_be_retried_but_queued_retry_is_idempotent(store):
    job = submit(store).job
    assert store.retry_job(str(job.job_id), "tenant_a").state == "queued"
    store.claim_job(str(job.job_id), "tenant_a")
    with pytest.raises(DocumentJobConflict):
        store.retry_job(str(job.job_id), "tenant_a")
