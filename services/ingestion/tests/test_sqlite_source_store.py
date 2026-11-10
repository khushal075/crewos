"""Exercise registration and retry behavior against a real SQLite database."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.storage.source_store import SourceRegistrationConflict
from crewos_ingestion.storage.sqlite_source_store import SQLiteSourceStore


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "sources.db"
    return path, SQLiteSourceStore(path)


@pytest.fixture
def submission():
    return SourceSubmission(title="Webhook Recovery", content="Check delivery logs.")


def test_register_persists_version_one_without_source_reference(database, submission):
    path, store = database

    receipt = store.register(tenant_id="tenant_a", submission=submission)

    assert (receipt.version, receipt.status) == (1, "accepted")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "SELECT tenant_id, source_id, version, title, content, source_reference "
            "FROM source_versions"
        ).fetchall() == [
            ("tenant_a", str(receipt.source_id), 1, submission.title, submission.content, None)
        ]


def test_identical_retry_returns_original_receipt_after_reopening(database, submission):
    path, store = database
    args = dict(tenant_id="tenant_a", submission=submission, idempotency_key="upload-42")

    original = store.register(**args)

    assert SQLiteSourceStore(path).register(**args) == original
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)


def test_submissions_without_a_key_create_separate_sources(database, submission):
    _, store = database
    first = store.register(tenant_id="tenant_a", submission=submission)
    second = store.register(tenant_id="tenant_a", submission=submission)

    assert first.source_id != second.source_id


def test_same_key_can_be_used_by_different_tenants(database, submission):
    _, store = database
    args = dict(submission=submission, idempotency_key="upload-42")

    first = store.register(tenant_id="tenant_a", **args)
    second = store.register(tenant_id="tenant_b", **args)

    assert first.source_id != second.source_id
    assert store.register(tenant_id="tenant_a", **args) == first
    assert store.register(tenant_id="tenant_b", **args) == second


@pytest.mark.parametrize(
    "changed_field",
    [{"title": "Another runbook"}, {"content": "Different text"}, {"source_reference": "ref-1"}],
)
def test_conflicting_retry_preserves_original_registration(database, submission, changed_field):
    path, store = database
    args = dict(tenant_id="tenant_a", idempotency_key="upload-42")
    original = store.register(submission=submission, **args)
    changed = SourceSubmission.model_validate({**submission.model_dump(), **changed_field})

    with pytest.raises(SourceRegistrationConflict):
        store.register(submission=changed, **args)

    assert store.register(submission=submission, **args) == original
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)


def test_concurrent_identical_submissions_create_one_source(database, submission):
    path, store = database
    start = Barrier(2, timeout=5)

    def register():
        start.wait()
        return store.register(
            tenant_id="tenant_a", submission=submission, idempotency_key="upload-42"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register)
        second = pool.submit(register)
        assert first.result(timeout=10) == second.result(timeout=10)

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM source_registrations").fetchone() == (1,)


def test_failed_key_write_rolls_back_source_and_allows_retry(database, submission):
    path, store = database
    args = dict(tenant_id="tenant_a", submission=submission, idempotency_key="upload-42")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TRIGGER reject_registration BEFORE INSERT ON source_registrations "
            "BEGIN SELECT RAISE(ABORT, 'forced registration failure'); END;"
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced registration failure"):
        store.register(**args)

    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM source_registrations").fetchone() == (0,)
        db.execute("DROP TRIGGER reject_registration")

    assert store.register(**args).version == 1
