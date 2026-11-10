from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError

from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt


def test_submission_preserves_source_text():
    submission = SourceSubmission(title=" Recovery ", content="  Check logs.\n")
    assert submission.title == "Recovery"
    assert submission.content == "  Check logs.\n"
    assert submission.source_reference is None


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "Runbook"},
        {"content": "Check logs"},
        {"title": " ", "content": "Check logs"},
        {"title": "Runbook", "content": " \n"},
        {"title": "Runbook", "content": b"Check logs"},
        {"title": "Runbook", "content": "Check logs", "source_reference": " "},
        {"title": "Runbook", "content": "Check logs", "tenant_id": "tenant_a"},
        {"title": "Runbook", "content": "Check logs", "channel": "webhook"},
    ],
)
def test_invalid_submission_rejected(payload):
    with pytest.raises(ValidationError):
        SourceSubmission.model_validate(payload)


def test_limit_counts_utf8_bytes_not_characters():
    at_limit = "é" * 2_000_000
    assert SourceSubmission(title="Runbook", content=at_limit).content == at_limit
    with pytest.raises(ValidationError, match="UTF-8 byte limit"):
        SourceSubmission(title="Runbook", content=at_limit + "a")


def test_invalid_utf8_text_rejected():
    with pytest.raises(ValidationError):
        SourceSubmission(title="Runbook", content="\ud800")


def test_receipt_round_trips():
    receipt = SourceAcceptanceReceipt(source_id=uuid4(), version=1)
    assert receipt.status == "accepted"
    assert SourceAcceptanceReceipt.model_validate_json(receipt.model_dump_json()) == receipt


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_id": uuid1()},
        {"version": 0},
        {"version": True},
        {"version": "1"},
        {"status": "searchable"},
    ],
)
def test_invalid_receipt_rejected(overrides):
    with pytest.raises(ValidationError):
        SourceAcceptanceReceipt.model_validate({"source_id": uuid4(), "version": 1, **overrides})
