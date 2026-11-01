"""Full replacement commands carry expected revision, never tenant authority."""

from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError

from crewos.ingestion import DocumentUpdateRequest


def payload():
    return {
        "document_id": str(uuid4()),
        "expected_version": 3,
        "content": b"Updated recovery instructions.\n",
        "title": " CRM recovery ",
    }


def test_accepts_replacement_source_and_expected_current_revision():
    data = payload()
    request = DocumentUpdateRequest.model_validate(data)
    assert str(request.document_id) == data["document_id"]
    assert request.expected_version == 3
    assert request.content == data["content"]
    assert request.title == "CRM recovery"
    assert request.source_ref is None
    assert DocumentUpdateRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_id", "not-a-uuid"),
        ("document_id", str(uuid1())),
        ("expected_version", 0),
        ("expected_version", -1),
        ("expected_version", True),
        ("expected_version", "3"),
        ("expected_version", 3.0),
        ("content", "text"),
        ("content", b""),
        ("content", b"x" * 4_000_001),
        ("title", " "),
        ("title", "x" * 257),
        ("source_ref", " "),
        ("source_ref", "x" * 2049),
        ("tenant_id", "tenant_b"),
        ("permissions", ["document.update"]),
        ("version", 4),
        ("chunking_options", {}),
    ],
)
def test_rejects_invalid_values_and_server_owned_fields(field, value):
    with pytest.raises(ValidationError):
        DocumentUpdateRequest.model_validate({**payload(), field: value})


@pytest.mark.parametrize("field", ["document_id", "expected_version", "content", "title"])
def test_requires_identity_precondition_and_full_replacement_fields(field):
    data = payload()
    del data[field]
    with pytest.raises(ValidationError):
        DocumentUpdateRequest.model_validate(data)


def test_source_label_is_optional_metadata_and_request_is_immutable():
    request = DocumentUpdateRequest.model_validate({**payload(), "source_ref": " runbook:v2 "})
    assert request.source_ref == "runbook:v2"
    with pytest.raises(ValidationError):
        request.expected_version = 4
