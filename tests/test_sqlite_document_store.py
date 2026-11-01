"""Real SQLite persistence, tenant scope, transactions, and competing appends."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import stat
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import (
    DocumentInput,
    DocumentNotFound,
    DocumentStorageCorruption,
    DocumentVersionConflict,
    StoredDocumentVersion,
    build_sqlite_document_store,
)


def snapshot(*, tenant="tenant_a", document_id=None, version=1, text="Check logs before retrying."):
    document = DocumentInput(title="CRM recovery", text=text, source_ref="runbook:crm")
    return StoredDocumentVersion(
        document_id=document_id or uuid4(),
        tenant_id=tenant,
        version=version,
        document=document,
        chunking=build_character_chunking().chunk(
            ChunkingRequest(text=text, options=ChunkingOptions(chunk_size=10, overlap=2))
        ),
    )


@pytest.fixture
def path(tmp_path):
    return tmp_path / "private" / "documents.sqlite3"


def test_snapshot_survives_reopening_database_with_all_chunks_and_metadata(path):
    record = snapshot()
    assert build_sqlite_document_store(path).create_version(record) == record
    reopened = build_sqlite_document_store(path)
    assert reopened.get(tenant_id=record.tenant_id, document_id=record.document_id) == record
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_exact_and_latest_versions_preserve_earlier_snapshots(path):
    store = build_sqlite_document_store(path)
    first = snapshot()
    second = snapshot(document_id=first.document_id, version=2, text="New recovery instructions.")
    store.create_version(first)
    store.create_version(second)
    assert store.get(tenant_id="tenant_a", document_id=first.document_id, version=1) == first
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == second
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=first.document_id, version=3)


def test_tenant_scope_covers_reads_writes_and_deletes_for_same_document_id(path):
    store = build_sqlite_document_store(path)
    first = snapshot()
    store.create_version(first)
    with pytest.raises(DocumentNotFound, match="Document unavailable"):
        store.get(tenant_id="tenant_b", document_id=first.document_id)
    with pytest.raises(DocumentNotFound):
        store.delete(tenant_id="tenant_b", document_id=first.document_id)
    other = snapshot(
        tenant="tenant_b", document_id=first.document_id, text="Tenant B private runbook"
    )
    store.create_version(other)
    store.delete(tenant_id="tenant_a", document_id=first.document_id)
    assert store.get(tenant_id="tenant_b", document_id=first.document_id) == other


def test_rejects_skipped_first_and_duplicate_versions_without_overwrite(path):
    store = build_sqlite_document_store(path)
    first = snapshot()
    with pytest.raises(DocumentVersionConflict):
        store.create_version(snapshot(document_id=first.document_id, version=2))
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=first.document_id)
    store.create_version(first)
    with pytest.raises(DocumentVersionConflict):
        store.create_version(snapshot(document_id=first.document_id, text="Different input"))
    with pytest.raises(DocumentVersionConflict):
        store.create_version(snapshot(document_id=first.document_id, version=3))
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == first


def test_competing_connections_have_one_winner_for_next_version(path):
    first = snapshot()
    stores = [build_sqlite_document_store(path), build_sqlite_document_store(path)]
    stores[0].create_version(first)
    barrier = Barrier(2)

    def append(index):
        record = snapshot(
            document_id=first.document_id, version=2, text=f"Writer {index} instructions"
        )
        barrier.wait(timeout=5)
        try:
            return stores[index].create_version(record)
        except DocumentVersionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(append, (0, 1)))
    winners = [record for record in outcomes if record is not None]
    assert len(winners) == 1
    assert stores[0].get(tenant_id="tenant_a", document_id=first.document_id) == winners[0]
    assert stores[1].get(tenant_id="tenant_a", document_id=first.document_id, version=1) == first


def test_after_insert_failure_rolls_back_entire_snapshot(path):
    store = build_sqlite_document_store(path)
    first = snapshot()
    store.create_version(first)
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TRIGGER injected_failure AFTER INSERT ON document_versions
                      WHEN NEW.version = 2 BEGIN SELECT RAISE(FAIL, 'injected failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.create_version(snapshot(document_id=first.document_id, version=2))
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == first
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0] == 1
        db.execute("DROP TRIGGER injected_failure")
    second = snapshot(document_id=first.document_id, version=2)
    assert store.create_version(second) == second


def test_deletes_all_versions_and_missing_delete_is_explicit(path):
    store = build_sqlite_document_store(path)
    first = snapshot()
    store.create_version(first)
    store.create_version(snapshot(document_id=first.document_id, version=2))
    store.delete(tenant_id="tenant_a", document_id=first.document_id)
    for version in (None, 1, 2):
        with pytest.raises(DocumentNotFound):
            store.get(tenant_id="tenant_a", document_id=first.document_id, version=version)
    with pytest.raises(DocumentNotFound):
        store.delete(tenant_id="tenant_a", document_id=first.document_id)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0] == 0


def test_deep_validation_rejects_bypassed_models_before_writing(path):
    store = build_sqlite_document_store(path)
    record = snapshot()
    changed = record.document.model_copy(update={"text": "Forged source"})
    with pytest.raises(ValidationError):
        store.create_version(record.model_copy(update={"document": changed}))
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=record.document_id)


@pytest.mark.parametrize(
    "corruption", ["invalid_json", "wrong_tenant", "wrong_id", "wrong_version", "wrong_text"]
)
def test_corrupt_records_raise_generic_storage_error(path, corruption):
    store = build_sqlite_document_store(path)
    record = snapshot()
    store.create_version(record)
    data = record.model_dump(mode="json")
    if corruption == "wrong_tenant":
        data["tenant_id"] = "tenant_b"
    elif corruption == "wrong_id":
        data["document_id"] = str(uuid4())
    elif corruption == "wrong_version":
        data["version"] = 2
    elif corruption == "wrong_text":
        data["document"]["text"] = "private-corrupt-content"
    encoded = "private-corrupt-content" if corruption == "invalid_json" else json.dumps(data)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE document_versions SET snapshot=?", (encoded,))
    with pytest.raises(DocumentStorageCorruption) as error:
        store.get(tenant_id="tenant_a", document_id=record.document_id)
    assert "private-corrupt-content" not in str(error.value)


@pytest.mark.parametrize("version", [0, -1, True, "1", 2**63])
def test_rejects_invalid_read_versions(path, version):
    with pytest.raises(ValueError):
        build_sqlite_document_store(path).get(
            tenant_id="tenant_a", document_id=uuid4(), version=version
        )


@pytest.mark.parametrize("tenant", ["tenant/a", "", "x' OR 1=1 --", 123])
def test_rejects_invalid_tenant_identity(path, tenant):
    with pytest.raises(ValueError):
        build_sqlite_document_store(path).get(tenant_id=tenant, document_id=uuid4())


def test_requires_typed_document_identity(path):
    with pytest.raises(TypeError):
        build_sqlite_document_store(path).get(tenant_id="tenant_a", document_id="not-a-uuid")


@pytest.mark.parametrize("timeout", [0, -1, True, "5", float("nan"), float("inf")])
def test_rejects_invalid_lock_timeout(path, timeout):
    with pytest.raises(ValueError):
        build_sqlite_document_store(path, lock_timeout_seconds=timeout)


@pytest.mark.parametrize("path", ["", ":memory:"])
def test_requires_file_backed_database(path):
    with pytest.raises(ValueError):
        build_sqlite_document_store(path)


def test_refuses_unrelated_database_without_changing_its_tables(path):
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE other_data (value TEXT)")
    with pytest.raises(ValueError, match="unrelated"):
        build_sqlite_document_store(path)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='document_versions'").fetchone()
            is None
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_refuses_future_schema_version(path):
    build_sqlite_document_store(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="Unsupported"):
        build_sqlite_document_store(path)


def test_lock_contention_fails_instead_of_silently_losing_a_write(path):
    store = build_sqlite_document_store(path, lock_timeout_seconds=0.01)
    record = snapshot()
    with sqlite3.connect(path) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.create_version(record)
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=record.document_id)
