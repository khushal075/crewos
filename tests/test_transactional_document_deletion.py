"""Atomic source deletion/outbox recording, including real process termination."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest

from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.chunking import ChunkingOptions
from crewos.ingestion import (
    DocumentDeleteRequest,
    DocumentIngestionRequest,
    DocumentNotFound,
    DocumentUpdateRequest,
    DocumentVersionConflict,
    build_plain_text_ingestion_facade,
    build_sqlite_document_store,
)
from crewos.ingestion.adapters.sqlite_document_store import SQLiteDocumentStore
from crewos.ingestion.document_cleanup_contracts import DocumentDeletedEvent
from tests.test_document_lifecycle import context
from tests.test_sqlite_document_store import snapshot


def events(store):
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "SELECT * FROM document_cleanup_outbox WHERE event_type='document.deleted' ORDER BY event_id"
        ).fetchall()


@pytest.fixture
def store(tmp_path):
    return build_sqlite_document_store(tmp_path / "documents.sqlite3")


def test_deletion_removes_all_versions_and_records_one_content_free_pending_event(store):
    first = store.create_version(snapshot(text="private_source_marker"))
    store.create_version(snapshot(document_id=first.document_id, version=2))
    store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    (row,) = events(store)
    event = DocumentDeletedEvent.model_validate_json(row["event_payload"])
    assert event.tenant_id == first.tenant_id
    assert event.document_id == first.document_id
    assert str(event.event_id) == row["event_id"]
    assert event.occurred_at.timestamp() == row["occurred_at"] == row["next_attempt_at"]
    assert row["state"] == "pending" and row["attempts"] == 0
    assert row["lease_token"] is None and row["completed_at"] is None
    assert "private_source_marker" not in row["event_payload"]
    for version in (None, 1, 2):
        with pytest.raises(DocumentNotFound):
            store.get(tenant_id=first.tenant_id, document_id=first.document_id, version=version)
    reopened = build_sqlite_document_store(store.path)
    assert events(reopened)[0]["event_payload"] == row["event_payload"]


def test_missing_other_tenant_and_repeat_deletion_do_not_append_events(store):
    first = store.create_version(snapshot())
    for tenant, document in (("tenant_b", first.document_id), ("tenant_a", uuid4())):
        with pytest.raises(DocumentNotFound):
            store.delete(tenant_id=tenant, document_id=document)
    assert events(store) == []
    store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    with pytest.raises(DocumentNotFound):
        store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    assert len(events(store)) == 1


def test_denied_deletion_cannot_record_event(store):
    first = store.create_version(snapshot())
    facade = build_plain_text_ingestion_facade(
        store=store, executor=PolicyEnforcedExecutor(PermissionPolicyService())
    )
    with pytest.raises(PolicyDeniedError):
        facade.delete(
            request=DocumentDeleteRequest(document_id=first.document_id),
            context=context(permissions=()),
        )
    assert events(store) == []
    assert store.get(tenant_id=first.tenant_id, document_id=first.document_id) == first


def test_outbox_failure_rolls_back_source_removal_and_job_fencing(store):
    first = store.create_version(snapshot())
    submission = store.submit_job(
        tenant_id=first.tenant_id,
        operation="update",
        request=DocumentUpdateRequest(
            document_id=first.document_id,
            expected_version=1,
            content=b"private_pending_payload",
            title="Update",
        ),
        options=ChunkingOptions(),
        idempotency_key="pending-update",
    )
    claim = store.claim_job(str(submission.job.job_id), first.tenant_id)
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT state,payload,token,lease_until FROM document_jobs").fetchone()
        db.execute(
            "CREATE TRIGGER reject_outbox BEFORE INSERT ON document_cleanup_outbox BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    assert store.get(tenant_id=first.tenant_id, document_id=first.document_id) == first
    assert events(store) == []
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT state,payload,token,lease_until FROM document_jobs").fetchone()
            == before
        )
        db.execute("DROP TRIGGER reject_outbox")
    store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    assert len(events(store)) == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT state,payload,token,lease_until FROM document_jobs"
        ).fetchone() == ("failed", None, None, None)


def test_failure_after_event_insert_rolls_back_everything(store, monkeypatch):
    first = store.create_version(snapshot())
    original = store._record_document_deleted

    def fail_after_insert(db, **kwargs):
        original(db, **kwargs)
        raise RuntimeError("injected after event insert")

    monkeypatch.setattr(store, "_record_document_deleted", fail_after_insert)
    with pytest.raises(RuntimeError):
        store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    assert events(store) == []
    assert store.get(tenant_id=first.tenant_id, document_id=first.document_id) == first


def test_concurrent_deletions_record_exactly_one_event(store):
    first = store.create_version(snapshot())

    def delete():
        try:
            store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
            return True
        except DocumentNotFound:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: delete(), range(2)))
    assert sorted(outcomes) == [False, True]
    assert len(events(store)) == 1


def test_deleted_identity_cannot_be_reused_but_other_tenants_and_new_ids_can(store):
    first = store.create_version(snapshot())
    store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    reopened = build_sqlite_document_store(store.path)
    with pytest.raises(DocumentVersionConflict, match="cannot be reused"):
        reopened.create_version(first)
    reopened.create_version(snapshot(tenant="tenant_b", document_id=first.document_id))
    replacement = reopened.create_version(snapshot())
    assert replacement.document_id != first.document_id


@pytest.mark.parametrize("old_version", [1, 2])
def test_existing_database_migrates_preserving_sources_and_jobs(store, old_version):
    first = store.create_version(snapshot())
    submission = None
    if old_version == 2:
        submission = store.submit_job(
            tenant_id="tenant_a",
            operation="create",
            request=DocumentIngestionRequest(content=b"pending payload", title="Pending"),
            options=ChunkingOptions(),
            idempotency_key="migration-pending",
        )
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE document_cleanup_outbox")
        if old_version == 1:
            db.execute("DROP TABLE document_jobs")
        db.execute(f"PRAGMA user_version={old_version}")
    migrated = build_sqlite_document_store(store.path)
    assert migrated.get(tenant_id=first.tenant_id, document_id=first.document_id) == first
    assert events(migrated) == []
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM document_jobs").fetchone()[0] == (
            1 if old_version == 2 else 0
        )
    if submission is not None:
        assert migrated.get_job(str(submission.job.job_id), "tenant_a") == submission.job
    migrated.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    assert len(events(migrated)) == 1


def test_migration_failure_rolls_back_schema_and_version(store, monkeypatch):
    first = store.create_version(snapshot())
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE document_cleanup_outbox")
        db.execute("PRAGMA user_version=2")
    original = SQLiteDocumentStore._initialize_cleanup_outbox

    def fail(self, db):
        original(self, db)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(SQLiteDocumentStore, "_initialize_cleanup_outbox", fail)
    with pytest.raises(RuntimeError):
        build_sqlite_document_store(store.path)
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='document_cleanup_outbox'"
            ).fetchone()
            is None
        )


def test_recording_helper_refuses_an_independent_nontransactional_append(store):
    with store._connection() as db:
        with pytest.raises(RuntimeError, match="active source transaction"):
            store._record_document_deleted(db, tenant_id="tenant_a", document_id=uuid4())
    assert events(store) == []


@pytest.mark.parametrize("before_commit", [True, False])
def test_process_crash_preserves_atomic_source_and_event_state(store, before_commit):
    first = store.create_version(snapshot())
    script = """
import os
import sys
from uuid import UUID
from crewos.ingestion import build_sqlite_document_store
store = build_sqlite_document_store(sys.argv[1])
if sys.argv[3] == 'before':
    original = store._record_document_deleted
    def terminate(db, **kwargs):
        original(db, **kwargs)
        os._exit(17)
    store._record_document_deleted = terminate
store.delete(tenant_id='tenant_a', document_id=UUID(sys.argv[2]))
os._exit(18)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(store.path),
            str(first.document_id),
            "before" if before_commit else "after",
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == (17 if before_commit else 18), result.stderr.decode()
    reopened = build_sqlite_document_store(store.path)
    if before_commit:
        assert reopened.get(tenant_id=first.tenant_id, document_id=first.document_id) == first
        assert events(reopened) == []
    else:
        with pytest.raises(DocumentNotFound):
            reopened.get(tenant_id=first.tenant_id, document_id=first.document_id)
        assert len(events(reopened)) == 1
        assert events(reopened)[0]["state"] == "pending"
