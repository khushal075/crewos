"""Internal tenant scope, bounded polling and executable CLI integration."""

import json
import os
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.document_cleanup_result import CleanupOutcome, DocumentCleanupResult
from crewos.core import document_cleanup_setup
from crewos.core.config import Settings, settings
from crewos.ingestion import build_sqlite_document_store
from crewos.workers import document_cleanup_worker as worker
from tests.test_sqlite_document_store import snapshot


def test_default_scope_denies_before_source_or_target_access(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOCUMENT_CLEANUP_TENANTS", frozenset())
    source = Mock()
    monkeypatch.setattr(document_cleanup_setup, "get_document_store", source)
    with pytest.raises(PermissionError):
        document_cleanup_setup.run_cleanup_pass(tenant_id="tenant_a")
    source.assert_not_called()


def test_idle_pass_does_not_create_vector_database(monkeypatch, tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.sqlite3")
    monkeypatch.setattr(document_cleanup_setup, "get_document_store", lambda: source)
    monkeypatch.setattr(settings, "DOCUMENT_CLEANUP_TENANTS", frozenset({"tenant_a"}))
    monkeypatch.setattr(settings, "EMBEDDING_DB_PATH", str(tmp_path / "not-created.sqlite3"))
    outcomes = document_cleanup_setup.run_cleanup_pass(tenant_id="tenant_a")
    assert [item.outcome for item in outcomes] == [CleanupOutcome.IDLE]
    assert not (tmp_path / "not-created.sqlite3").exists()


def test_pass_is_bounded_and_does_not_require_active_user_token(monkeypatch, tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.sqlite3")
    for _ in range(3):
        document = source.create_version(snapshot())
        source.delete(tenant_id=document.tenant_id, document_id=document.document_id)
    monkeypatch.setattr(document_cleanup_setup, "get_document_store", lambda: source)
    monkeypatch.setattr(settings, "DOCUMENT_CLEANUP_TENANTS", frozenset({"tenant_a"}))
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {})
    monkeypatch.setattr(settings, "DOCUMENT_CLEANUP_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "EMBEDDING_DB_PATH", str(tmp_path / "vectors.sqlite3"))
    outcomes = document_cleanup_setup.run_cleanup_pass(tenant_id="tenant_a")
    assert len(outcomes) == 2
    assert all(item.outcome is CleanupOutcome.COMPLETED for item in outcomes)
    with sqlite3.connect(source.path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM document_cleanup_outbox WHERE state='pending'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"DOCUMENT_CLEANUP_TENANTS": ["a/b"]},
        {"DOCUMENT_CLEANUP_BATCH_SIZE": 0},
        {"DOCUMENT_CLEANUP_LEASE_SECONDS": 1},
        {"DOCUMENT_CLEANUP_POLL_SECONDS": 0},
        {"DOCUMENT_CLEANUP_RETRY_BASE_SECONDS": 100, "DOCUMENT_CLEANUP_RETRY_MAX_SECONDS": 10},
    ],
)
def test_invalid_worker_settings_are_rejected(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


def test_cli_once_prints_safe_outcome_summary(monkeypatch, capsys):
    monkeypatch.setattr(worker, "logger", Mock())
    monkeypatch.setattr(worker, "require_cleanup_tenant", lambda tenant: tenant)
    monkeypatch.setattr(
        worker,
        "run_cleanup_pass",
        lambda **kwargs: (DocumentCleanupResult(outcome=CleanupOutcome.IDLE),),
    )
    assert worker.main(["--tenant-id", "tenant_a", "--once"]) == 0
    assert json.loads(capsys.readouterr().out) == {"tenant_id": "tenant_a", "outcomes": {"idle": 1}}


def test_cli_denial_never_runs_pass(monkeypatch):
    run = Mock()
    monkeypatch.setattr(worker, "run_cleanup_pass", run)

    def denied(tenant):
        raise PermissionError("private marker")

    monkeypatch.setattr(worker, "require_cleanup_tenant", denied)
    logger = Mock()
    monkeypatch.setattr(worker, "logger", logger)
    assert worker.main(["--tenant-id", "tenant_a", "--once"]) == 1
    run.assert_not_called()
    assert "private marker" not in str(logger.mock_calls)


def test_cli_once_failure_does_not_log_exception_body(monkeypatch):
    monkeypatch.setattr(worker, "require_cleanup_tenant", lambda tenant: tenant)
    monkeypatch.setattr(worker, "run_cleanup_pass", Mock(side_effect=OSError("private marker")))
    logger = Mock()
    monkeypatch.setattr(worker, "logger", logger)
    assert worker.main(["--tenant-id", "tenant_a", "--once"]) == 1
    assert "private marker" not in str(logger.mock_calls)


def test_continuous_mode_recovers_poll_failure_and_stops_cleanly(monkeypatch):
    monkeypatch.setattr(worker, "logger", Mock())
    monkeypatch.setattr(worker, "require_cleanup_tenant", lambda tenant: tenant)
    run = Mock(
        side_effect=[OSError("offline"), (DocumentCleanupResult(outcome=CleanupOutcome.IDLE),)]
    )
    monkeypatch.setattr(worker, "run_cleanup_pass", run)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(worker, "sleep", sleep)
    assert worker.main(["--tenant-id", "tenant_a"]) == 0
    assert run.call_count == 2 and len(sleeps) == 2


def test_real_cli_process_consumes_persisted_deletion_event(tmp_path):
    source_path, cache_path = tmp_path / "source.sqlite3", tmp_path / "vectors.sqlite3"
    from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
    from crewos.retrieval.embedding_storage_contracts import ChunkEmbeddingWriteBatch
    from crewos.retrieval.search_contracts import RetrievedPassage
    from tests.test_embedding_storage_contracts import record

    source = build_sqlite_document_store(source_path)
    document = source.create_version(snapshot())
    cache = SQLiteChunkEmbeddingStore(cache_path)
    embedding = record(
        passage=RetrievedPassage(
            document_id=document.document_id,
            document_version=document.version,
            title=document.document.title,
            chunk=document.chunking.chunks[0],
        )
    )
    cache.put(ChunkEmbeddingWriteBatch(records=(embedding,)))
    source.delete(tenant_id=document.tenant_id, document_id=document.document_id)
    environment = dict(os.environ)
    environment.update(
        DOCUMENT_DB_PATH=str(source_path),
        EMBEDDING_DB_PATH=str(cache_path),
        DOCUMENT_CLEANUP_TENANTS=json.dumps(["tenant_a"]),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "crewos.workers.document_cleanup_worker",
            "--tenant-id",
            "tenant_a",
            "--once",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["outcomes"] == {"completed": 1, "idle": 1}
    with sqlite3.connect(source_path) as db:
        assert db.execute("SELECT state FROM document_cleanup_outbox").fetchone()[0] == "completed"
    with sqlite3.connect(cache_path) as db:
        assert db.execute("SELECT document_id FROM deleted_embedding_documents").fetchone()[
            0
        ] == str(document.document_id)


def test_once_returns_nonzero_when_retry_was_scheduled(monkeypatch, capsys):
    from uuid import uuid4

    monkeypatch.setattr(worker, "logger", Mock())
    monkeypatch.setattr(worker, "require_cleanup_tenant", lambda tenant: tenant)
    outcome = DocumentCleanupResult(
        outcome=CleanupOutcome.RETRY_SCHEDULED, event_id=uuid4(), attempt=1
    )
    monkeypatch.setattr(worker, "run_cleanup_pass", lambda **kwargs: (outcome,))
    assert worker.main(["--tenant-id", "tenant_a", "--once"]) == 1
    assert json.loads(capsys.readouterr().out)["outcomes"] == {"retry_scheduled": 1}


def test_continuous_mode_stops_if_service_scope_is_revoked(monkeypatch):
    monkeypatch.setattr(worker, "logger", Mock())
    monkeypatch.setattr(worker, "require_cleanup_tenant", lambda tenant: tenant)
    monkeypatch.setattr(worker, "run_cleanup_pass", Mock(side_effect=PermissionError("revoked")))
    sleep = Mock()
    monkeypatch.setattr(worker, "sleep", sleep)
    assert worker.main(["--tenant-id", "tenant_a"]) == 1
    sleep.assert_not_called()
