"""Source-to-cache cleanup with real SQLite, retry recovery and safe outcomes."""

import logging
import sqlite3
import subprocess
import sys
from time import time
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.application.dtos.document_cleanup_result import CleanupOutcome, DocumentCleanupResult
from crewos.application.use_cases.process_document_cleanup import DocumentCleanupProcessor
from crewos.ingestion import build_sqlite_document_store
from crewos.ingestion.document_cleanup_outbox import CleanupOutboxCorruption
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import EmbeddingWriteFenced
from crewos.retrieval.embedding_storage_contracts import (
    ChunkEmbeddingWriteBatch,
    StoredChunkEmbedding,
)
from crewos.retrieval.search_contracts import RetrievedPassage
from tests.test_embedding_storage_contracts import space
from tests.test_sqlite_document_store import snapshot
from tests.test_document_cleanup_outbox import row


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = build_sqlite_document_store(tmp_path / "source.sqlite3")
    cache = SQLiteChunkEmbeddingStore(tmp_path / "vectors.sqlite3")
    document = source.create_version(snapshot())
    record = StoredChunkEmbedding(
        tenant_id=document.tenant_id,
        space=space(),
        vector=(1, 0),
        passage=RetrievedPassage(
            document_id=document.document_id,
            document_version=document.version,
            title=document.document.title,
            chunk=document.chunking.chunks[0],
        ),
    )
    cache.put(ChunkEmbeddingWriteBatch(records=(record,)))
    clock = [time() + 10]
    monkeypatch.setattr(
        "crewos.ingestion.adapters.sqlite_document_cleanup_outbox.time", lambda: clock[0]
    )
    return source, cache, document, record, clock


def processor(source, cache, **options):
    return DocumentCleanupProcessor(outbox=source, target_factory=lambda: cache, **options)


def read(cache, record):
    return cache.read_document(
        tenant_id=record.tenant_id,
        document_id=record.passage.document_id,
        version=record.passage.document_version,
        space=record.space,
        max_records=128,
        max_snapshot_bytes=1000000,
    )


def delete(source, document):
    source.delete(tenant_id=document.tenant_id, document_id=document.document_id)


def test_success_cleans_vectors_fences_writers_and_acknowledges(setup):
    source, cache, document, record, _ = setup
    delete(source, document)
    result = processor(source, cache).process_one(tenant_id="tenant_a")
    assert result.outcome is CleanupOutcome.COMPLETED
    assert row(source)["state"] == "completed"
    assert read(cache, record) == ()
    with pytest.raises(EmbeddingWriteFenced):
        cache.put(ChunkEmbeddingWriteBatch(records=(record,)))
    assert processor(source, cache).process_one(tenant_id="tenant_a").outcome is CleanupOutcome.IDLE


def test_idle_does_not_open_target(setup):
    source, _, _, _, _ = setup
    factory = Mock()
    result = DocumentCleanupProcessor(outbox=source, target_factory=factory).process_one(
        tenant_id="tenant_a"
    )
    assert result.outcome is CleanupOutcome.IDLE
    factory.assert_not_called()


def test_target_failure_persists_retry_then_recovers(setup):
    source, cache, document, record, clock = setup
    delete(source, document)
    target = Mock()
    target.delete_document.side_effect = OSError("private exception body")
    first = processor(source, target).process_one(tenant_id="tenant_a")
    assert first.outcome is CleanupOutcome.RETRY_SCHEDULED
    assert row(source)["state"] == "pending"
    assert row(source)["last_error_code"] == "storage_unavailable"
    assert read(cache, record) == (record,)
    clock[0] += 10
    recovered = processor(source, cache).process_one(tenant_id="tenant_a")
    assert recovered.outcome is CleanupOutcome.COMPLETED and recovered.attempt == 2


def test_backoff_is_capped_and_repeated_failures_warn_without_raw_content(setup, caplog):
    source, _, document, _, clock = setup
    delete(source, document)
    target = Mock()
    target.delete_document.side_effect = ValueError("private_source_marker")
    worker = processor(
        source, target, retry_base_seconds=10, retry_max_seconds=20, alert_after_attempts=2
    )
    for expected in (10, 20, 20):
        with caplog.at_level(logging.INFO):
            result = worker.process_one(tenant_id="tenant_a")
        assert result.outcome is CleanupOutcome.RETRY_SCHEDULED
        assert row(source)["next_attempt_at"] == clock[0] + expected
        clock[0] += expected
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert "private_source_marker" not in caplog.text
    assert "lease_token" not in caplog.text


def test_ack_failure_never_reports_completion_and_expired_lease_replays(setup):
    source, cache, document, record, clock = setup
    delete(source, document)
    outbox = Mock(wraps=source)
    outbox.complete.side_effect = OSError("ack failed")
    with pytest.raises(OSError):
        processor(outbox, cache).process_one(tenant_id="tenant_a")
    assert read(cache, record) == ()
    assert row(source)["state"] == "processing"
    clock[0] += 31
    result = processor(source, cache).process_one(tenant_id="tenant_a")
    assert result.outcome is CleanupOutcome.COMPLETED and result.attempt == 2


def test_retry_persistence_failure_leaves_claim_recoverable(setup):
    source, _, document, _, _ = setup
    delete(source, document)
    target = Mock()
    target.delete_document.side_effect = OSError("target failed")
    outbox = Mock(wraps=source)
    outbox.retry.side_effect = OSError("retry failed")
    with pytest.raises(OSError):
        processor(outbox, target).process_one(tenant_id="tenant_a")
    assert row(source)["state"] == "processing"
    outbox.complete.assert_not_called()


@pytest.mark.parametrize("target_fails", [False, True])
def test_replaced_lease_cannot_ack_or_reschedule_new_owner(setup, target_fails):
    source, cache, document, _, clock = setup
    delete(source, document)
    newer = []

    def run(**kwargs):
        cache.delete_document(**kwargs)
        clock[0] += 31
        newer.append(source.claim_next(tenant_id="tenant_a", lease_seconds=30))
        if target_fails:
            raise OSError("failed after losing lease")

    target = Mock()
    target.delete_document.side_effect = run
    result = processor(source, target).process_one(tenant_id="tenant_a")
    assert result.outcome is CleanupOutcome.LEASE_LOST
    assert row(source)["lease_token"] == str(newer[0].lease_token)
    assert row(source)["state"] == "processing" and row(source)["attempts"] == 2


def test_wrong_tenant_claim_never_reaches_target(setup):
    source, _, document, _, _ = setup
    delete(source, document)
    claimed = source.claim_next(tenant_id="tenant_a", lease_seconds=30)
    outbox = Mock()
    outbox.claim_next.return_value = claimed
    factory = Mock()
    with pytest.raises(CleanupOutboxCorruption):
        DocumentCleanupProcessor(outbox=outbox, target_factory=factory).process_one(
            tenant_id="tenant_b"
        )
    factory.assert_not_called()
    outbox.complete.assert_not_called()
    outbox.retry.assert_not_called()


def test_invalid_claim_type_fails_without_target_access():
    outbox = Mock()
    outbox.claim_next.return_value = {}
    factory = Mock()
    with pytest.raises(CleanupOutboxCorruption):
        DocumentCleanupProcessor(outbox=outbox, target_factory=factory).process_one(
            tenant_id="tenant_a"
        )
    factory.assert_not_called()


def test_other_tenant_vectors_are_preserved(setup):
    source, cache, document, record, _ = setup
    other = record.model_copy(update={"tenant_id": "tenant_b"})
    cache.put(ChunkEmbeddingWriteBatch(records=(other,)))
    delete(source, document)
    processor(source, cache).process_one(tenant_id="tenant_a")
    assert read(cache, other) == (other,)


def test_crash_after_target_cleanup_before_ack_replays_safely(setup):
    source, cache, document, record, clock = setup
    delete(source, document)
    script = """
import os,sys
from crewos.ingestion import build_sqlite_document_store
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.application.use_cases.process_document_cleanup import DocumentCleanupProcessor
source = build_sqlite_document_store(sys.argv[1])
def crash(**kwargs): os._exit(23)
source.complete = crash
DocumentCleanupProcessor(outbox=source, target_factory=lambda: SQLiteChunkEmbeddingStore(sys.argv[2])).process_one(tenant_id='tenant_a')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(source.path), str(cache.path)],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 23, result.stderr.decode()
    assert row(source)["state"] == "processing" and read(cache, record) == ()
    clock[0] = row(source)["lease_until"] + 1
    recovered = processor(source, cache).process_one(tenant_id="tenant_a")
    assert recovered.outcome is CleanupOutcome.COMPLETED and recovered.attempt == 2


@pytest.mark.parametrize(
    "options",
    [
        {"lease_seconds": 0},
        {"lease_seconds": True},
        {"lease_seconds": float("inf")},
        {"retry_base_seconds": 20, "retry_max_seconds": 10},
        {"retry_max_seconds": float("nan")},
        {"alert_after_attempts": 0},
    ],
)
def test_processor_configuration_is_validated(options):
    with pytest.raises(ValueError):
        DocumentCleanupProcessor(outbox=Mock(), target_factory=Mock(), **options)


def test_result_contract_excludes_invalid_or_sensitive_fields():
    DocumentCleanupResult(outcome=CleanupOutcome.IDLE)
    with pytest.raises(ValidationError):
        DocumentCleanupResult(outcome=CleanupOutcome.COMPLETED)
    with pytest.raises(ValidationError):
        DocumentCleanupResult(outcome=CleanupOutcome.IDLE, event_id=uuid4())
    with pytest.raises(ValidationError):
        DocumentCleanupResult(outcome=CleanupOutcome.IDLE, lease_token=uuid4())
