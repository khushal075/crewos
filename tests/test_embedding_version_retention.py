"""Version floors preserve current vectors and fence obsolete writes durably."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

import pytest

from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import EmbeddingStorageCorruption, EmbeddingWriteFenced
from tests.test_embedding_storage_contracts import record, space
from tests.test_sqlite_chunk_embedding_store import put, read


@pytest.fixture
def store(tmp_path):
    return SQLiteChunkEmbeddingStore(tmp_path / "vectors.sqlite3")


def version_of(item, version, **changes):
    passage = item.passage.model_dump()
    passage["document_version"] = version
    return record(passage=passage, **changes)


def retain(store, item, minimum):
    store.retain_from_version(
        tenant_id=item.tenant_id, document_id=item.passage.document_id, min_version=minimum
    )


def test_removes_old_versions_across_spaces_and_preserves_current_and_newer(store):
    old = record()
    other_space = version_of(old, 1, space=space(model_revision="other"))
    current, newer = version_of(old, 2), version_of(old, 3)
    put(store, old, other_space, current, newer)
    retain(store, old, 2)
    assert read(store, old) == () and read(store, other_space) == ()
    assert read(store, current) == (current,) and read(store, newer) == (newer,)
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM chunk_embeddings WHERE version<2").fetchone()[0] == 0
        )


def test_floor_survives_restart_and_cannot_move_backwards(store):
    old = record()
    retain(store, old, 3)
    reopened = SQLiteChunkEmbeddingStore(store.path)
    retain(reopened, old, 2)
    retain(reopened, old, 3)
    for stale in (old, version_of(old, 2)):
        with pytest.raises(EmbeddingWriteFenced):
            put(reopened, stale)
    current = version_of(old, 3)
    put(reopened, current)
    assert read(reopened, current) == (current,)


def test_other_tenants_and_documents_are_untouched(store):
    old = record()
    other_tenant = record(tenant_id="tenant_b", passage=old.passage)
    other_document = record()
    put(store, old, other_document)
    put(store, other_tenant)
    retain(store, old, 2)
    assert read(store, other_tenant) == (other_tenant,)
    assert read(store, other_document) == (other_document,)


def test_missing_document_can_be_fenced_before_late_writer_arrives(store):
    old = record()
    retain(store, old, 2)
    with pytest.raises(EmbeddingWriteFenced):
        put(store, old)
    put(store, version_of(old, 2))


def test_obsolete_write_rolls_back_entire_batch(store):
    old, unrelated = record(), record()
    retain(store, old, 2)
    with pytest.raises(EmbeddingWriteFenced):
        put(store, unrelated, old)
    assert read(store, unrelated) == ()


def test_failed_cleanup_rolls_back_floor_advance(store):
    old = record()
    put(store, old)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER reject_old_delete BEFORE DELETE ON chunk_embeddings BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        retain(store, old, 2)
    assert read(store, old) == (old,)
    put(store, old)  # A failed transaction must not fence the old version.


def test_concurrent_old_write_and_cleanup_cannot_resurrect_obsolete_vectors(store):
    old = record()
    barrier = Barrier(2)

    def write():
        barrier.wait(timeout=5)
        try:
            put(store, old)
        except EmbeddingWriteFenced:
            pass

    def cleanup():
        barrier.wait(timeout=5)
        retain(store, old, 2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write), pool.submit(cleanup)]
        for future in futures:
            future.result(timeout=10)
    assert read(store, old) == ()
    with pytest.raises(EmbeddingWriteFenced):
        put(store, old)


def test_concurrent_floor_updates_keep_the_highest_version(store):
    old = record()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda minimum: retain(store, old, minimum), (3, 2)))
    with pytest.raises(EmbeddingWriteFenced):
        put(store, version_of(old, 2))
    put(store, version_of(old, 3))


def test_permanent_deletion_cannot_be_undone_by_retention(store):
    old = record()
    retain(store, old, 2)
    store.delete_document(tenant_id=old.tenant_id, document_id=old.passage.document_id)
    retain(store, old, 1)
    retain(store, old, 5)
    with pytest.raises(EmbeddingWriteFenced):
        put(store, version_of(old, 5))


@pytest.mark.parametrize(
    "changes",
    [
        {"min_version": 0},
        {"min_version": -1},
        {"min_version": True},
        {"min_version": "2"},
        {"min_version": 2**63},
        {"tenant_id": "a/b"},
        {"document_id": "invalid"},
    ],
)
def test_invalid_retention_scope_and_version_fail(store, changes):
    old = record()
    with pytest.raises((ValueError, TypeError)):
        store.retain_from_version(
            **{
                "tenant_id": old.tenant_id,
                "document_id": old.passage.document_id,
                "min_version": 2,
                **changes,
            }
        )


def test_corrupt_floor_is_not_silently_ignored(store):
    old = record()
    retain(store, old, 2)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE embedding_version_floors SET min_version=0")
    for operation in (
        lambda: read(store, old),
        lambda: put(store, old),
        lambda: retain(store, old, 3),
    ):
        with pytest.raises(EmbeddingStorageCorruption):
            operation()


def test_schema_two_migration_preserves_vectors_and_deletion_markers(store):
    live, deleted = record(), record()
    put(store, live, deleted)
    store.delete_document(tenant_id=deleted.tenant_id, document_id=deleted.passage.document_id)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE embedding_version_floors")
        db.execute("PRAGMA user_version=2")
    migrated = SQLiteChunkEmbeddingStore(store.path)
    assert read(migrated, live) == (live,)
    with pytest.raises(EmbeddingWriteFenced):
        put(migrated, deleted)
    retain(migrated, live, 2)
    assert read(migrated, live) == ()
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
