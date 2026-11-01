"""Read/delete facade authorization and tenant-scoped lifecycle behavior."""

import logging
from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor, PolicyDeniedError
from crewos.ingestion import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    DocumentReadRequest,
    DocumentDeleteRequest,
    DocumentNotFound,
    build_plain_text_ingestion_facade,
    build_sqlite_document_store,
)


def context(
    tenant="tenant_a",
    permissions=("document.create", "document.update", "document.read", "document.delete"),
):
    return ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions))


def test_read_exact_latest_and_delete_all_versions(tmp_path, caplog):
    facade = build_plain_text_ingestion_facade(
        store=build_sqlite_document_store(tmp_path / "docs.sqlite3"),
        executor=PolicyEnforcedExecutor(PermissionPolicyService()),
    )
    first = facade.ingest(
        request=DocumentIngestionRequest(content=b"Private content", title="Runbook"),
        context=context(),
    )
    second = facade.update(
        request=DocumentUpdateRequest(
            document_id=first.document_id, expected_version=1, content=b"Replacement", title="New"
        ),
        context=context(),
    )
    assert (
        facade.read(
            request=DocumentReadRequest(document_id=first.document_id, version=1), context=context()
        )
        == first
    )
    assert (
        facade.read(request=DocumentReadRequest(document_id=first.document_id), context=context())
        == second
    )
    with pytest.raises(PolicyDeniedError):
        facade.read(
            request=DocumentReadRequest(document_id=first.document_id),
            context=context(permissions=()),
        )
    with pytest.raises(PolicyDeniedError):
        facade.delete(
            request=DocumentDeleteRequest(document_id=first.document_id),
            context=context(permissions=("document.read",)),
        )
    for method, request in [
        (facade.read, DocumentReadRequest(document_id=first.document_id)),
        (facade.delete, DocumentDeleteRequest(document_id=first.document_id)),
    ]:
        with pytest.raises(DocumentNotFound):
            method(request=request, context=context("tenant_b"))
    with caplog.at_level(logging.INFO):
        assert facade.delete(
            request=DocumentDeleteRequest(document_id=first.document_id), context=context()
        ).deleted
    assert "document_delete_succeeded" in caplog.text
    assert "Private content" not in caplog.text
    for version in (None, 1, 2):
        with pytest.raises(DocumentNotFound):
            facade.read(
                request=DocumentReadRequest(document_id=first.document_id, version=version),
                context=context(),
            )
    with pytest.raises(DocumentNotFound):
        facade.delete(
            request=DocumentDeleteRequest(document_id=first.document_id), context=context()
        )


@pytest.mark.parametrize("value", [0, -1, True, "1", 2**63])
def test_read_contract_rejects_invalid_versions(value):
    with pytest.raises(ValidationError):
        DocumentReadRequest(document_id=uuid4(), version=value)


@pytest.mark.parametrize("model", [DocumentReadRequest, DocumentDeleteRequest])
def test_lifecycle_commands_reject_tenant_authority(model):
    with pytest.raises(ValidationError):
        model(document_id=uuid4(), tenant_id="tenant_b")
