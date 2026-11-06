"""Real SQLite leased delivery: ownership, recovery, isolation and retry durability."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
from time import time
from uuid import uuid4

import pytest

from crewos.ingestion import build_sqlite_document_store
from crewos.ingestion.document_cleanup_contracts import CleanupFailureCode
from crewos.ingestion.document_cleanup_outbox import (
    CleanupLeaseLost,
    CleanupOutboxCorruption,
    DocumentCleanupOutboxInterface,
)
from tests.test_sqlite_document_store import snapshot


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = build_sqlite_document_store(tmp_path / "source.sqlite3")
    first = store.create_version(snapshot())
    store.delete(tenant_id=first.tenant_id, document_id=first.document_id)
    clock = [time() + 10]
    monkeypatch.setattr(
        "crewos.ingestion.adapters.sqlite_document_cleanup_outbox.time", lambda: clock[0]
    )
    return store, first, clock


def claim(store, tenant="tenant_a", lease=30):
    outbox: DocumentCleanupOutboxInterface = store
    return outbox.claim_next(tenant_id=tenant, lease_seconds=lease)


def finish(store, claimed, **changes):
    store.complete(
        **{
            "tenant_id": claimed.event.tenant_id,
            "event_id": claimed.event.event_id,
            "lease_token": claimed.lease_token,
            **changes,
        }
    )


def retry(store, claimed, **changes):
    store.retry(
        **{
            "tenant_id": claimed.event.tenant_id,
            "event_id": claimed.event.event_id,
            "lease_token": claimed.lease_token,
            "delay_seconds": 10,
            "error_code": CleanupFailureCode.STORAGE_UNAVAILABLE,
            **changes,
        }
    )


def row(store):
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM document_cleanup_outbox").fetchone())


def test_claim_persists_lease_and_excludes_active_delivery(setup):
    store, first, clock = setup
    claimed = claim(store)
    assert claimed.event.document_id == first.document_id
    assert claimed.attempt == 1
    assert claimed.lease_expires_at.timestamp() == pytest.approx(clock[0] + 30)
    assert row(store)["state"] == "processing"
    assert claim(build_sqlite_document_store(store.path)) is None


def test_completion_is_durable_idempotent_and_never_reclaimed(setup):
    store, _, clock = setup
    claimed = claim(store)
    finish(store, claimed)
    assert row(store)["state"] == "completed"
    assert row(store)["lease_until"] is None
    clock[0] += 1000
    reopened = build_sqlite_document_store(store.path)
    finish(reopened, claimed)
    assert claim(reopened) is None
    with pytest.raises(CleanupLeaseLost):
        finish(reopened, claimed, lease_token=uuid4())
    with pytest.raises(CleanupLeaseLost):
        retry(reopened, claimed)


def test_expired_lease_reclaims_with_new_token_after_restart(setup):
    store, _, clock = setup
    old = claim(store)
    clock[0] = old.lease_expires_at.timestamp()
    reopened = build_sqlite_document_store(store.path)
    new = claim(reopened)
    assert new.event == old.event
    assert new.attempt == 2 and new.lease_token != old.lease_token
    with pytest.raises(CleanupLeaseLost):
        finish(store, old)
    with pytest.raises(CleanupLeaseLost):
        retry(store, old)
    finish(reopened, new)


def test_expired_worker_cannot_acknowledge_before_reclaim(setup):
    store, _, clock = setup
    claimed = claim(store)
    clock[0] = claimed.lease_expires_at.timestamp()
    with pytest.raises(CleanupLeaseLost):
        finish(store, claimed)
    with pytest.raises(CleanupLeaseLost):
        retry(store, claimed)
    assert row(store)["state"] == "processing"


def test_retry_delay_survives_restart_and_preserves_event(setup):
    store, _, clock = setup
    old = claim(store)
    retry(store, old, delay_seconds=20)
    queued = row(store)
    assert queued["state"] == "pending" and queued["lease_token"] is None
    assert queued["last_error_code"] == "storage_unavailable"
    reopened = build_sqlite_document_store(store.path)
    assert claim(reopened) is None
    clock[0] += 20
    new = claim(reopened)
    assert new.event == old.event and new.attempt == 2
    with pytest.raises(CleanupLeaseLost):
        finish(store, old)
    finish(reopened, new)
    assert row(store)["last_error_code"] is None


def test_zero_delay_retry_is_immediately_eligible(setup):
    store, _, _ = setup
    old = claim(store)
    retry(store, old, delay_seconds=0, error_code=CleanupFailureCode.CLEANUP_FAILED)
    assert claim(store).attempt == 2


def test_tenant_scope_applies_before_selection_decoding_and_updates(setup):
    store, _, _ = setup
    assert claim(store, tenant="tenant_b") is None
    claimed = claim(store)
    with pytest.raises(CleanupLeaseLost):
        finish(store, claimed, tenant_id="tenant_b")
    with pytest.raises(CleanupLeaseLost):
        retry(store, claimed, tenant_id="tenant_b")
    assert row(store)["state"] == "processing"
    finish(store, claimed)


def test_two_workers_cannot_claim_the_same_active_event(setup):
    store, _, _ = setup
    other = build_sqlite_document_store(store.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (store, other)))
    assert sum(item is not None for item in outcomes) == 1
    assert row(store)["attempts"] == 1


def test_oldest_eligible_event_is_selected_first(setup):
    store, first, _ = setup
    second = store.create_version(snapshot())
    store.delete(tenant_id=second.tenant_id, document_id=second.document_id)
    assert claim(store).event.document_id == first.document_id
    assert claim(store).event.document_id == second.document_id


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE document_cleanup_outbox SET event_payload='private_corruption'",
        "UPDATE document_cleanup_outbox SET document_id='wrong-identity'",
        "UPDATE document_cleanup_outbox SET next_attempt_at='invalid'",
        "UPDATE document_cleanup_outbox SET state='processing',lease_until=NULL",
    ],
)
def test_corruption_is_not_skipped_or_completed(setup, mutation):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute(mutation)
    assert claim(store, tenant="tenant_b") is None
    with pytest.raises(CleanupOutboxCorruption) as captured:
        claim(store)
    assert "private_corruption" not in str(captured.value)
    assert row(store)["state"] != "completed"


def test_attempt_exhaustion_does_not_drop_event(setup):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE document_cleanup_outbox SET attempts=2147483647")
    with pytest.raises(CleanupOutboxCorruption, match="counter exhausted"):
        claim(store)
    assert row(store)["state"] == "pending"


@pytest.mark.parametrize("value", [0, -1, True, "30", float("nan"), float("inf"), 301, 10**400])
def test_invalid_lease_durations_do_not_mutate_event(setup, value):
    store, _, _ = setup
    with pytest.raises(ValueError):
        claim(store, lease=value)
    assert row(store)["state"] == "pending" and row(store)["attempts"] == 0


@pytest.mark.parametrize("value", [-1, True, "10", float("nan"), float("inf"), 86401, 10**400])
def test_invalid_retry_delays_preserve_active_claim(setup, value):
    store, _, _ = setup
    claimed = claim(store)
    with pytest.raises(ValueError):
        retry(store, claimed, delay_seconds=value)
    assert row(store)["state"] == "processing"


def test_invalid_identity_and_raw_failure_messages_are_rejected(setup):
    store, _, _ = setup
    with pytest.raises(ValueError):
        claim(store, tenant="a/b")
    claimed = claim(store)
    with pytest.raises(ValueError):
        finish(store, claimed, event_id="invalid")
    with pytest.raises(ValueError):
        retry(store, claimed, error_code="private raw failure")
    with pytest.raises(CleanupLeaseLost):
        finish(store, claimed, event_id=uuid4())


def test_retry_write_failure_rolls_back_release_and_schedule(setup):
    store, _, _ = setup
    claimed = claim(store)
    before = row(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER reject_retry BEFORE UPDATE ON document_cleanup_outbox BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        retry(store, claimed)
    assert row(store) == before


def test_equal_time_events_use_stable_event_id_order(setup):
    from crewos.ingestion.document_cleanup_contracts import DocumentDeletedEvent

    store, _, clock = setup
    second = store.create_version(snapshot())
    store.delete(tenant_id=second.tenant_id, document_id=second.document_id)
    timestamp = datetime.fromtimestamp(clock[0] - 1, timezone.utc).timestamp()
    with sqlite3.connect(store.path) as db:
        rows = db.execute("SELECT event_id,event_payload FROM document_cleanup_outbox").fetchall()
        for event_id, payload in rows:
            event = DocumentDeletedEvent.model_validate_json(payload).model_copy(
                update={"occurred_at": datetime.fromtimestamp(timestamp, timezone.utc)}
            )
            db.execute(
                "UPDATE document_cleanup_outbox SET event_payload=?,occurred_at=?,next_attempt_at=? WHERE event_id=?",
                (event.model_dump_json(), timestamp, timestamp, event_id),
            )
    assert [str(claim(store).event.event_id), str(claim(store).event.event_id)] == sorted(
        event_id for event_id, _ in rows
    )


def test_lifecycle_logs_exclude_payload_and_lease_tokens(setup, caplog):
    import logging

    store, _, _ = setup
    with caplog.at_level(logging.INFO):
        old = claim(store)
        retry(store, old, delay_seconds=0)
        new = claim(store)
        finish(store, new)
    assert "document_cleanup_claimed" in caplog.text
    assert "document_cleanup_retry_scheduled" in caplog.text
    assert "document_cleanup_completed" in caplog.text
    assert str(old.lease_token) not in caplog.text
    assert str(new.lease_token) not in caplog.text
    assert "event_payload" not in caplog.text
