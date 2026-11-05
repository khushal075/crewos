"""Real SQLite persistence, isolation, rollback, admission and corruption checks."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import (
    EmbeddingStorageCorruption,
    EmbeddingStorageLimitExceeded,
    EmbeddingWriteConflict,
    EmbeddingWriteFenced,
)
from crewos.retrieval.embedding_storage_contracts import ChunkEmbeddingWriteBatch
from tests.test_embedding_storage_contracts import record, space


def put(store, *records):
    store.put(ChunkEmbeddingWriteBatch(records=records))


def read(store, item, **changes):
    return store.read_document(
        **{
            "tenant_id": item.tenant_id,
            "document_id": item.passage.document_id,
            "version": item.passage.document_version,
            "space": item.space,
            "max_records": 128,
            "max_snapshot_bytes": 1_000_000,
            **changes,
        }
    )


@pytest.fixture
def store(tmp_path):
    return SQLiteChunkEmbeddingStore(tmp_path / "vectors.sqlite3")


def test_persistence_replay_and_private_file_permissions(store):
    item = record()
    put(store, item)
    put(store, item)
    reopened = SQLiteChunkEmbeddingStore(store.path)
    assert read(reopened, item) == (item,)
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_tenant_version_and_configuration_scope(store):
    first = record()
    other = record(tenant_id="tenant_b", passage=first.passage)
    put(store, first)
    put(store, other)
    assert read(store, first) == (first,)
    assert read(store, other) == (other,)
    assert read(store, first, tenant_id="tenant_c") == ()
    assert read(store, first, version=2) == ()
    assert read(store, first, space=space(model_revision="different")) == ()


def test_conflicting_replay_rolls_back_entire_batch(store):
    existing = record()
    put(store, existing)
    new = record()
    conflict = record(passage=existing.passage, vector=(0, 1))
    with pytest.raises(EmbeddingWriteConflict):
        put(store, new, conflict)
    assert read(store, new) == ()
    assert read(store, existing) == (existing,)


def test_concurrent_replays_are_atomic_and_idempotent(store):
    item = record()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: put(store, item), range(2)))
    assert read(store, item) == (item,)


def test_delete_is_idempotent_and_does_not_cross_tenants(store):
    item = record()
    other = record(tenant_id="tenant_b", passage=item.passage)
    put(store, item)
    put(store, other)
    store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    assert read(store, item) == ()
    assert read(store, other) == (other,)


def test_byte_admission_precedes_decode_and_is_scoped(store, monkeypatch):
    item = record()
    put(store, item)
    monkeypatch.setattr(store, "_decode", lambda row: pytest.fail("must admit before decoding"))
    with pytest.raises(EmbeddingStorageLimitExceeded):
        read(store, item, max_snapshot_bytes=1)
    assert read(store, item, tenant_id="other", max_snapshot_bytes=1) == ()


def test_count_admission_and_chunk_order(store):
    first = record()
    passage = first.passage.model_dump()
    passage["chunk"]["index"] = 1
    second = record(passage=passage)
    put(store, second, first)
    assert read(store, first) == (first, second)
    with pytest.raises(EmbeddingStorageLimitExceeded):
        read(store, first, max_records=1)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE chunk_embeddings SET snapshot='{}'",
        "UPDATE chunk_embeddings SET chunk_index=10",
    ],
)
def test_corruption_is_not_disguised_as_missing(store, mutation):
    item = record()
    put(store, item)
    with sqlite3.connect(store.path) as db:
        db.execute(mutation)
    with pytest.raises(EmbeddingStorageCorruption):
        read(store, item)


def test_revalidates_constructed_records_at_write_boundary(store):
    item = record().model_copy(update={"vector": (float("nan"), 0.0)})
    batch = ChunkEmbeddingWriteBatch.model_construct(records=(item,))
    with pytest.raises(ValidationError):
        store.put(batch)


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "a/b"},
        {"version": True},
        {"version": 2**64},
        {"max_records": 0},
        {"max_snapshot_bytes": True},
        {"document_id": "not-uuid"},
    ],
)
def test_invalid_read_identity_and_limits_fail(store, changes):
    with pytest.raises((ValueError, TypeError)):
        read(store, record(), **changes)


@pytest.mark.parametrize("path", ["", ":memory:"])
def test_rejects_non_file_storage(path):
    with pytest.raises(ValueError):
        SQLiteChunkEmbeddingStore(path)


@pytest.mark.parametrize("timeout", [0, True, float("inf"), 31])
def test_rejects_invalid_lock_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        SQLiteChunkEmbeddingStore(tmp_path / "v.sqlite3", lock_timeout_seconds=timeout)


def test_refuses_unrelated_database(tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(ValueError, match="unrelated"):
        SQLiteChunkEmbeddingStore(path)


def test_deletion_fence_survives_restart_and_blocks_replay(store):
    item = record()
    put(store, item)
    store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    reopened = SQLiteChunkEmbeddingStore(store.path)
    with pytest.raises(EmbeddingWriteFenced):
        put(reopened, item)
    assert read(reopened, item) == ()


def test_delete_before_first_write_blocks_late_writer(store):
    item = record()
    store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    with pytest.raises(EmbeddingWriteFenced):
        put(store, item)


def test_deletion_fences_all_versions_and_spaces_but_not_other_tenants(store):
    item = record()
    store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    new_passage = item.passage.model_dump()
    new_passage["document_version"] = 2
    for candidate in (
        record(passage=new_passage),
        record(passage=item.passage, space=space(model_revision="new")),
    ):
        with pytest.raises(EmbeddingWriteFenced):
            put(store, candidate)
    other = record(tenant_id="tenant_b", passage=item.passage)
    put(store, other)
    assert read(store, other) == (other,)


def test_fenced_record_rolls_back_other_writes_in_batch(store):
    deleted = record()
    store.delete_document(tenant_id=deleted.tenant_id, document_id=deleted.passage.document_id)
    new = record()
    with pytest.raises(EmbeddingWriteFenced):
        put(store, new, deleted)
    assert read(store, new) == ()


def test_concurrent_delete_and_put_cannot_leave_resurrected_vectors(store):
    from threading import Barrier

    item = record()
    other_connection = SQLiteChunkEmbeddingStore(store.path)
    barrier = Barrier(2)

    def write():
        barrier.wait(timeout=5)
        try:
            put(other_connection, item)
        except EmbeddingWriteFenced:
            pass

    def delete():
        barrier.wait(timeout=5)
        store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write), pool.submit(delete)]
        for future in futures:
            future.result(timeout=10)
    assert read(store, item) == ()
    with pytest.raises(EmbeddingWriteFenced):
        put(store, item)


def test_failed_delete_rolls_back_marker_and_vector_removal(store):
    item = record()
    put(store, item)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON chunk_embeddings BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    put(store, item)  # No deletion marker may survive the failed transaction.
    assert read(store, item) == (item,)


def test_schema_one_migration_preserves_existing_vectors_and_enables_fences(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    item = record()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE chunk_embeddings (tenant_id TEXT NOT NULL, space_key TEXT NOT NULL, document_id TEXT NOT NULL, version INTEGER NOT NULL, chunk_index INTEGER NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(tenant_id, space_key, document_id, version, chunk_index))"
        )
        db.execute(
            "INSERT INTO chunk_embeddings VALUES(?,?,?,?,?,?)",
            (
                item.tenant_id,
                item.space.key,
                str(item.passage.document_id),
                item.passage.document_version,
                item.passage.chunk.index,
                item.model_dump_json(),
            ),
        )
        db.execute(f"PRAGMA application_id={SQLiteChunkEmbeddingStore.APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
    migrated = SQLiteChunkEmbeddingStore(path)
    assert read(migrated, item) == (item,)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    migrated.delete_document(tenant_id=item.tenant_id, document_id=item.passage.document_id)
    with pytest.raises(EmbeddingWriteFenced):
        put(SQLiteChunkEmbeddingStore(path), item)
