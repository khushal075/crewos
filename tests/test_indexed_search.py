"""Transactional FTS5 search, independent of bounded full-corpus readers."""

import sqlite3
from unittest.mock import Mock
import pytest
from crewos.ingestion import build_sqlite_document_store, DocumentStorageCorruption
from crewos.retrieval import RetrievalRequest, build_indexed_retrieval
from crewos.retrieval.strategies.indexed_bm25 import IndexedBM25Retriever
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor, PolicyDeniedError
from tests.test_keyword_retrieval import save


def search(source, query="unique_credentials", tenant="tenant_a", top_k=5):
    return IndexedBM25Retriever(source).search(
        request=RetrievalRequest(query=query, top_k=top_k), tenant_id=tenant
    )


def test_index_handles_large_corpus_without_calling_scan(tmp_path, monkeypatch):
    source = build_sqlite_document_store(tmp_path / "source.db")
    for i in range(100):
        save(source, f"unrelated filler number {i}")
    expected = save(source, "unique_credentials rotate token")
    monkeypatch.setattr(
        source, "read_latest_documents", Mock(side_effect=AssertionError("No scan"))
    )
    result = search(source)
    assert result.passages[0].document_id == expected.document_id
    assert search(build_sqlite_document_store(source.path)) == result


def test_update_delete_and_tenant_local_statistics(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = save(source, "unique_credentials")
    before = search(source)
    for _ in range(25):
        save(source, "unique_credentials private_other_tenant", tenant="tenant_b")
    assert search(source) == before
    save(source, "replacement_newword", document_id=first.document_id, version=2)
    assert search(source).passages == ()
    assert search(source, "replacement_newword").passages[0].document_version == 2
    source.delete(tenant_id="tenant_a", document_id=first.document_id)
    assert search(source, "replacement_newword").passages == ()
    assert search(source, tenant="tenant_b").passages


def test_fts_query_operators_are_treated_as_literal_terms(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    save(source, "unique_credentials")
    assert search(source, '"unique_credentials" OR * NOT :').passages
    assert search(source, '"*":').passages == ()


def test_index_failure_rolls_back_source_and_events(tmp_path, monkeypatch):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = save(source, "unique_credentials")
    original = source._index_document

    def fail(db, record):
        original(db, record)
        raise RuntimeError("failure after index write")

    monkeypatch.setattr(source, "_index_document", fail)
    with pytest.raises(RuntimeError):
        save(source, "newword", document_id=first.document_id, version=2)
    assert search(source).passages[0].document_version == 1
    assert not search(source, "newword").passages
    with sqlite3.connect(source.path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_cleanup_outbox").fetchone()[0] == 0


def test_index_corruption_fails_closed(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    save(source, "unique_credentials")
    with sqlite3.connect(source.path) as db:
        db.execute(f"UPDATE {source._search_table('tenant_a')} SET text='forged_token'")
    with pytest.raises(DocumentStorageCorruption):
        search(source, "forged_token")


def test_schema_four_migration_backfills_only_latest(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = save(source, "oldterm")
    save(source, "newterm", document_id=first.document_id, version=2)
    with sqlite3.connect(source.path) as db:
        db.execute(f"DROP TABLE {source._search_table('tenant_a')}")
        db.execute("PRAGMA user_version=4")
    migrated = build_sqlite_document_store(source.path)
    assert search(migrated, "oldterm").passages == ()
    assert search(migrated, "newterm").passages[0].document_version == 2
    assert search(build_sqlite_document_store(source.path), "newterm") == search(
        migrated, "newterm"
    )


def test_policy_denial_precedes_index_io():
    index = Mock()
    facade = build_indexed_retrieval(
        index=index, executor=PolicyEnforcedExecutor(PermissionPolicyService())
    )
    with pytest.raises(PolicyDeniedError):
        facade.search(
            request=RetrievalRequest(query="credentials"),
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    index.search_current_chunks.assert_not_called()


def test_background_job_commit_indexes_chunks(tmp_path):
    from tests.test_document_jobs import submit, context, source as request
    from crewos.ingestion import process_document_job

    store = build_sqlite_document_store(tmp_path / "jobs.db")
    submission = submit(store, request=request(b"unique_credentials"))
    result = process_document_job(store=store, job_id=str(submission.job.job_id), context=context())
    assert result.state == "succeeded"
    assert search(store).passages[0].document_id == result.document_id


def test_failed_deletion_restores_index_and_source(tmp_path, monkeypatch):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = save(source, "unique_credentials")
    remove = source._remove_search_document

    def fail(*args):
        remove(*args)
        raise RuntimeError("after index removal")

    monkeypatch.setattr(source, "_remove_search_document", fail)
    with pytest.raises(RuntimeError):
        source.delete(tenant_id="tenant_a", document_id=first.document_id)
    assert search(source).passages[0].document_id == first.document_id


def test_migration_failure_restores_schema_four(tmp_path, monkeypatch):
    from crewos.ingestion.adapters.sqlite_document_store import SQLiteDocumentStore

    source = build_sqlite_document_store(tmp_path / "source.db")
    save(source, "unique_credentials")
    table = source._search_table("tenant_a")
    with sqlite3.connect(source.path) as db:
        db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=4")
    original = SQLiteDocumentStore._backfill_search_index

    def fail(self, db):
        original(self, db)
        raise RuntimeError("migration failure")

    monkeypatch.setattr(SQLiteDocumentStore, "_backfill_search_index", fail)
    with pytest.raises(RuntimeError):
        build_sqlite_document_store(source.path)
    with sqlite3.connect(source.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is None


def test_missing_index_is_an_error_and_rebuild_restores_only_current_scope(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    own = save(source, "unique_credentials")
    other = save(source, "private", tenant="tenant_b")
    with sqlite3.connect(source.path) as db:
        db.execute(f"DROP TABLE {source._search_table('tenant_a')}")
    with pytest.raises(DocumentStorageCorruption, match="index is missing"):
        search(source)
    source.rebuild_search_index(tenant_id="tenant_a")
    assert search(source).passages[0].document_id == own.document_id
    assert search(source, "private", tenant="tenant_b").passages[0].document_id == other.document_id
    source.rebuild_search_index(tenant_id="tenant_a")
    assert len(search(source).passages) == 1


def test_missing_row_mapping_is_not_silently_recreated(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    save(source, "unique_credentials")
    with sqlite3.connect(source.path) as db:
        db.execute("DROP TABLE document_search_rows")
    with pytest.raises(DocumentStorageCorruption, match="mapping is missing"):
        build_sqlite_document_store(source.path)
