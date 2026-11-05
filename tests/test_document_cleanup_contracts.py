"""Deletion-event data minimization, identity and lease shape validation."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.ingestion.document_cleanup_contracts import (
    CleanupFailureCode,
    DocumentCleanupClaim,
    DocumentDeletedEvent,
)

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def event(**changes):
    return DocumentDeletedEvent.model_validate(
        {
            "event_id": uuid4(),
            "tenant_id": "tenant_a",
            "document_id": uuid4(),
            "occurred_at": NOW,
            **changes,
        }
    )


def claim(**changes):
    return DocumentCleanupClaim.model_validate(
        {
            "event": event(),
            "lease_token": uuid4(),
            "attempt": 1,
            "lease_expires_at": NOW + timedelta(seconds=30),
            **changes,
        }
    )


def test_event_round_trip_preserves_exact_deletion_identity():
    original = event()
    restored = DocumentDeletedEvent.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.event_type == "document.deleted"
    assert restored.tenant_id == "tenant_a"
    assert set(restored.model_dump()) == {
        "schema_version",
        "event_type",
        "event_id",
        "tenant_id",
        "document_id",
        "occurred_at",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": "invalid"},
        {"document_id": "invalid"},
        {"tenant_id": "a/b"},
        {"tenant_id": ""},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"schema_version": 2},
        {"schema_version": True},
        {"event_type": "document.updated"},
        {"content": "private"},
        {"vectors": [[1, 0]]},
        {"permissions": ["admin"]},
    ],
)
def test_event_rejects_invalid_metadata_and_unneeded_payloads(changes):
    with pytest.raises(ValidationError):
        event(**changes)


def test_claim_round_trip_preserves_delivery_token_and_attempt():
    original = claim(attempt=2)
    restored = DocumentCleanupClaim.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.attempt == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"lease_token": "invalid"},
        {"attempt": 0},
        {"attempt": True},
        {"attempt": "1"},
        {"attempt": 2**31},
        {"lease_expires_at": NOW},
        {"lease_expires_at": NOW - timedelta(seconds=1)},
        {"lease_expires_at": NOW.replace(tzinfo=None)},
        {"permissions": ["admin"]},
    ],
)
def test_claim_rejects_invalid_lease_shapes(changes):
    with pytest.raises(ValidationError):
        claim(**changes)


def test_contracts_are_immutable_including_nested_event():
    original = claim()
    with pytest.raises(ValidationError):
        original.attempt = 2
    with pytest.raises(ValidationError):
        original.event.tenant_id = "tenant_b"


def test_known_failure_codes_do_not_accept_raw_exception_messages():
    assert CleanupFailureCode("storage_unavailable") is CleanupFailureCode.STORAGE_UNAVAILABLE
    with pytest.raises(ValueError):
        CleanupFailureCode("Exception: private source content")
