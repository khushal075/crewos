"""Ingestion source commands exclude tenant identity and server-owned settings."""

import pytest
from pydantic import ValidationError

from crewos.ingestion import DocumentIngestionRequest


def request():
    return DocumentIngestionRequest(
        content=b"  Private incident details.\n  Retry carefully.\n",
        title="CRM runbook",
        source_ref="private-runbook:v1",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "tenant_b"),
        ("permissions", ["document.create"]),
        ("document_id", "chosen"),
        ("version", 2),
        ("chunking_options", {}),
        ("content", b""),
        ("content", "text"),
        ("content", b"x" * 4_000_001),
        ("title", " "),
        ("source_ref", " "),
    ],
)
def test_request_rejects_authority_claims_and_invalid_source_fields(field, value):
    with pytest.raises(ValidationError):
        DocumentIngestionRequest.model_validate({**request().model_dump(), field: value})
