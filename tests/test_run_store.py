"""Durable state, ownership, transaction races, leases, and replay fencing."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from uuid import uuid4

import pytest

from crewos.application.interfaces.run_store import (
    RunCancelled,
    RunConflict,
    RunLeaseLost,
    RunNotFound,
    UnsafeReplay,
)
from crewos.infrastructure.run_store import SQLiteRunStore


def job(tenant="tenant_a"):
    return {
        "agent_run_id": str(uuid4()),
        "tenant_id": tenant,
        "message": "Inspect",
        "max_decisions": 5,
    }


@pytest.fixture
def setup(tmp_path):
    store = SQLiteRunStore(str(tmp_path / "runs.sqlite3"))
    payload = job()
    store.create(payload, duration_seconds=120)
    return store, payload


def test_record_survives_reopening_without_result_backend(setup):
    store, payload = setup
    claim = store.claim(payload)
    store.checkpoint(claim["id"], claim["tenant"], claim["lease_token"], {"decisions": 1})
    store.finish(
        claim["id"],
        claim["tenant"],
        claim["lease_token"],
        state="succeeded",
        result={"type": "final_answer", "answer": "Done"},
    )
    reopened = SQLiteRunStore(store.path)
    record = reopened.get(claim["id"], claim["tenant"])
    assert record["state"] == "succeeded"
    assert record["checkpoint"]["decisions"] == 1
    assert reopened.claim(payload)["result"]["answer"] == "Done"
    assert record["attempt_history"][0]["state"] == "succeeded"


def test_missing_and_other_tenant_records_are_not_disclosed(setup):
    store, payload = setup
    for identifier, tenant in [(payload["agent_run_id"], "tenant_b"), (str(uuid4()), "tenant_a")]:
        with pytest.raises(RunNotFound):
            store.get(identifier, tenant)


def test_only_one_concurrent_attempt_can_claim(setup):
    store, payload = setup

    def claim():
        try:
            return store.claim(payload)["lease_token"]
        except RunConflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: claim(), range(4)))
    assert sum(value is not None for value in claims) == 1
    assert store.get(payload["agent_run_id"], "tenant_a")["attempts"] == 1


def test_expired_attempt_cannot_checkpoint_or_finalize_after_takeover(setup):
    store, payload = setup
    old = store.claim(payload)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_runs SET lease_until=0")
    new = store.claim(payload)
    assert new["lease_token"] != old["lease_token"]
    for operation in [
        lambda: store.guard(old["id"], old["tenant"], old["lease_token"]),
        lambda: store.checkpoint(old["id"], old["tenant"], old["lease_token"], {}),
        lambda: store.finish(old["id"], old["tenant"], old["lease_token"], state="succeeded"),
    ]:
        with pytest.raises(RunLeaseLost):
            operation()
    assert store.get(new["id"], new["tenant"])["attempt_history"][0]["state"] == "expired"


def test_job_identity_and_content_are_immutable(setup):
    store, payload = setup
    with pytest.raises(RunConflict):
        store.claim({**payload, "message": "Altered input"})


def test_cancellation_wins_over_late_success(setup):
    store, payload = setup
    claim = store.claim(payload)
    store.cancel(claim["id"], claim["tenant"])
    with pytest.raises(RunCancelled):
        store.guard(claim["id"], claim["tenant"], claim["lease_token"])
    store.finish(
        claim["id"],
        claim["tenant"],
        claim["lease_token"],
        state="succeeded",
        result={"answer": "late"},
    )
    result = store.get(claim["id"], claim["tenant"])
    assert result["state"] == "cancelled"
    assert result["result"] is None
    with pytest.raises(RunConflict):
        store.resume(claim["id"], claim["tenant"])


def test_queued_cancellation_prevents_claim(setup):
    store, payload = setup
    assert store.cancel(payload["agent_run_id"], "tenant_a")["state"] == "cancelled"
    with pytest.raises(RunCancelled):
        store.claim(payload)


def test_failed_read_checkpoint_resumes_without_resetting_budget(setup):
    store, payload = setup
    first = store.claim(payload)
    cp = {"decisions": 2, "reserved_tokens": 900, "pending": {"name": "read"}, "replay_safe": True}
    store.checkpoint(first["id"], first["tenant"], first["lease_token"], cp)
    store.finish(
        first["id"], first["tenant"], first["lease_token"], state="failed", error="TimeoutError"
    )
    with pytest.raises(RunConflict):
        store.claim(payload)
    store.resume(first["id"], first["tenant"])
    second = store.claim(payload)
    assert second["deadline"] == first["deadline"]
    assert second["checkpoint"] == cp
    assert second["attempts"] == 2


def test_pending_write_cannot_be_replayed(setup):
    store, payload = setup
    claim = store.claim(payload)
    store.checkpoint(
        claim["id"],
        claim["tenant"],
        claim["lease_token"],
        {"pending": {"name": "write"}, "replay_safe": False},
    )
    store.finish(claim["id"], claim["tenant"], claim["lease_token"], state="failed")
    with pytest.raises(UnsafeReplay):
        store.resume(claim["id"], claim["tenant"])


def test_attempt_limit_cannot_be_reset_by_resume(setup):
    store, payload = setup
    store.max_attempts = 1
    claim = store.claim(payload)
    store.finish(claim["id"], claim["tenant"], claim["lease_token"], state="failed")
    with pytest.raises(RunConflict):
        store.resume(claim["id"], claim["tenant"])


def test_idempotency_key_reuses_same_submission_and_rejects_changed_input(tmp_path):
    store = SQLiteRunStore(str(tmp_path / "runs.sqlite3"))
    payload = job()
    first = store.create(payload, idempotency_key="key")
    second = store.create({**payload, "agent_run_id": str(uuid4())}, idempotency_key="key")
    assert first["id"] == second["id"]
    with pytest.raises(RunConflict):
        store.create({**job(), "message": "different"}, idempotency_key="key")
    other = store.create(job("tenant_b"), idempotency_key="key")
    assert other["id"] != first["id"]


def test_live_attempt_cannot_be_resumed(setup):
    store, payload = setup
    store.claim(payload)
    with pytest.raises(RunConflict):
        store.resume(payload["agent_run_id"], "tenant_a")


def test_cancelled_crashed_attempt_is_reconciled_after_lease_expiry(setup):
    store, payload = setup
    claim = store.claim(payload)
    store.cancel(claim["id"], claim["tenant"])
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_runs SET lease_until=0")
    assert store.get(claim["id"], claim["tenant"])["state"] == "cancelled"


def test_original_deadline_is_not_extended_by_resume(setup):
    store, payload = setup
    claim = store.claim(payload)
    store.finish(claim["id"], claim["tenant"], claim["lease_token"], state="failed")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_runs SET deadline=0")
    with pytest.raises(RunConflict):
        store.resume(claim["id"], claim["tenant"])


def test_uncertain_write_is_visible_as_needing_review_after_crash(setup):
    store, payload = setup
    claim = store.claim(payload)
    store.checkpoint(
        claim["id"],
        claim["tenant"],
        claim["lease_token"],
        {"pending": {"name": "write"}, "replay_safe": False},
    )
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_runs SET lease_until=0")
    assert store.get(claim["id"], claim["tenant"])["state"] == "needs_review"


def test_crashed_run_past_its_deadline_is_reported_exhausted(setup):
    store, payload = setup
    claim = store.claim(payload)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_runs SET lease_until=0,deadline=0")
    assert store.get(claim["id"], claim["tenant"])["state"] == "exhausted"
