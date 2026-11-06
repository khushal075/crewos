"""Transactional version events drive durable, monotonic vector cleanup."""

import sqlite3

import pytest

from crewos.ingestion import build_sqlite_document_store
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import EmbeddingWriteFenced
from crewos.retrieval.embedding_storage_contracts import ChunkEmbeddingWriteBatch
from tests.test_sqlite_document_store import snapshot
from tests.test_process_document_cleanup import processor, read
from tests.test_embedding_storage_contracts import record


def events(source):
    with sqlite3.connect(source.path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "SELECT * FROM document_cleanup_outbox ORDER BY source_version"
        ).fetchall()


def test_updates_record_events_and_cleanup_preserves_current_and_other_tenants(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    cache = SQLiteChunkEmbeddingStore(tmp_path / "vectors.db")
    first = source.create_version(snapshot())
    assert events(source) == []
    old = record()
    old = old.model_copy(
        update={"passage": old.passage.model_copy(update={"document_id": first.document_id})}
    )
    current = old.model_copy(
        update={"passage": old.passage.model_copy(update={"document_version": 3})}
    )
    other = old.model_copy(update={"tenant_id": "tenant_b"})
    cache.put(ChunkEmbeddingWriteBatch(records=(old, current)))
    cache.put(ChunkEmbeddingWriteBatch(records=(other,)))
    for version in (2, 3):
        source.create_version(snapshot(document_id=first.document_id, version=version))
    assert [e["source_version"] for e in events(source)] == [2, 3]
    # Deliver newest first; a delayed older event must never lower the floor.
    with sqlite3.connect(source.path) as db:
        db.execute("UPDATE document_cleanup_outbox SET next_attempt_at=0 WHERE source_version=3")
        db.execute(
            "UPDATE document_cleanup_outbox SET next_attempt_at=9999999999 WHERE source_version=2"
        )
    worker = processor(source, cache)
    worker.process_one(tenant_id="tenant_a")
    with sqlite3.connect(source.path) as db:
        db.execute("UPDATE document_cleanup_outbox SET next_attempt_at=0 WHERE source_version=2")
    worker.process_one(tenant_id="tenant_a")
    assert read(cache, old) == ()
    assert read(cache, current) == (current,)
    assert read(cache, other) == (other,)
    with pytest.raises(EmbeddingWriteFenced):
        cache.put(ChunkEmbeddingWriteBatch(records=(old,)))
    source.delete(tenant_id="tenant_a", document_id=first.document_id)
    worker.process_one(tenant_id="tenant_a")
    assert read(cache, current) == ()
    with pytest.raises(EmbeddingWriteFenced):
        cache.put(ChunkEmbeddingWriteBatch(records=(current,)))


def test_event_failure_rolls_back_version_creation(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = source.create_version(snapshot())
    with sqlite3.connect(source.path) as db:
        db.execute(
            "CREATE TRIGGER reject_event BEFORE INSERT ON document_cleanup_outbox BEGIN SELECT RAISE(ABORT,'failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        source.create_version(snapshot(document_id=first.document_id, version=2))
    assert source.get(tenant_id="tenant_a", document_id=first.document_id).version == 1
    assert events(source) == []


def test_legacy_migration_preserves_receipt_and_backfills_latest_once(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    deleted = source.create_version(snapshot())
    source.delete(tenant_id="tenant_a", document_id=deleted.document_id)
    claim = source.claim_next(tenant_id="tenant_a", lease_seconds=30)
    source.complete(
        tenant_id="tenant_a", event_id=claim.event.event_id, lease_token=claim.lease_token
    )
    active = source.create_version(snapshot())
    source.create_version(snapshot(document_id=active.document_id, version=2))
    receipt = dict(events(source)[0])
    with sqlite3.connect(source.path) as db:
        db.execute("ALTER TABLE document_cleanup_outbox RENAME TO modern")
        db.execute(
            "CREATE TABLE document_cleanup_outbox AS SELECT event_id,tenant_id,document_id,event_payload,occurred_at,state,attempts,lease_token,lease_until,next_attempt_at,last_error_code,completed_at FROM modern WHERE source_version=0"
        )
        db.execute("DROP TABLE modern")
        db.execute("PRAGMA user_version=3")
    migrated = build_sqlite_document_store(source.path)
    rows = events(migrated)
    assert len(rows) == 2
    assert dict(rows[0]) == receipt
    assert rows[1]["source_version"] == 2
    assert len(events(build_sqlite_document_store(source.path))) == 2
