"""Verify lifecycle rules before wiring up persistent storage."""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_lifecycle import (
    SourceDeleteRequest,
    SourceReadRequest,
    SourceUpdateRequest,
)
from crewos_ingestion.domain.source import StoredSourceVersion, SourceVersionLimitReached
from crewos_ingestion.lifecycle.delete_source import DeleteSource, SourceDeleteDenied
from crewos_ingestion.lifecycle.read_source import ReadSource, SourceReadDenied
from crewos_ingestion.lifecycle.update_source import UpdateSource, SourceUpdateDenied


@pytest.fixture
def revision():
    return StoredSourceVersion(
        tenant_id="tenant_a",
        source_id=uuid4(),
        version=1,
        title="Runbook",
        content="Check logs.",
        source_reference=None,
    )


def test_next_revision_keeps_identity_and_preserves_original(revision):
    updated = revision.next_revision(title="Runbook", content="Retry later.", source_reference=None)
    assert (updated.tenant_id, updated.source_id) == (revision.tenant_id, revision.source_id)
    assert (updated.version, updated.content) == (2, "Retry later.")
    assert (revision.version, revision.content) == (1, "Check logs.")


def test_version_cannot_exceed_sqlite_limit(revision):
    last = StoredSourceVersion.model_validate({**revision.model_dump(), "version": 2**63 - 1})
    with pytest.raises(SourceVersionLimitReached):
        last.next_revision(title="Runbook", content="New text", source_reference=None)


@pytest.mark.parametrize("version", [None, 1])
def test_read_passes_tenant_and_requested_version_without_exposing_tenant(revision, version):
    store = Mock()
    store.read.return_value = revision
    result = ReadSource(store=store).execute(
        request=SourceReadRequest(source_id=revision.source_id, version=version),
        context=CallerContext(tenant_id="tenant_a", permissions={"source.read"}),
    )
    store.read.assert_called_once_with(
        tenant_id="tenant_a", source_id=revision.source_id, version=version
    )
    assert result.model_dump() == revision.model_dump(exclude={"tenant_id"})


def test_update_forwards_expected_version_and_returns_stored_revision(revision):
    request = SourceUpdateRequest(
        source_id=revision.source_id, expected_version=1, title="Runbook", content="Retry later."
    )
    store = Mock()
    store.update.return_value = revision.next_revision(
        title=request.title, content=request.content, source_reference=request.source_reference
    )
    receipt = UpdateSource(store=store).execute(
        request=request, context=CallerContext(tenant_id="tenant_a", permissions={"source.update"})
    )
    store.update.assert_called_once_with(tenant_id="tenant_a", request=request)
    assert (receipt.source_id, receipt.version, receipt.status) == (
        revision.source_id,
        2,
        "updated",
    )


def test_delete_uses_requested_id_and_verified_tenant(revision):
    store = Mock()
    receipt = DeleteSource(store=store).execute(
        request=SourceDeleteRequest(source_id=revision.source_id),
        context=CallerContext(tenant_id="tenant_a", permissions={"source.delete"}),
    )
    store.delete.assert_called_once_with(tenant_id="tenant_a", source_id=revision.source_id)
    assert (receipt.source_id, receipt.status) == (revision.source_id, "deleted")


@pytest.mark.parametrize(
    "use_case, request_type, details, denied",
    [
        (ReadSource, SourceReadRequest, {}, SourceReadDenied),
        (
            UpdateSource,
            SourceUpdateRequest,
            {"title": "Runbook", "content": "Retry later.", "expected_version": 1},
            SourceUpdateDenied,
        ),
        (DeleteSource, SourceDeleteRequest, {}, SourceDeleteDenied),
    ],
)
def test_missing_permission_stops_before_storage(revision, use_case, request_type, details, denied):
    store = Mock()
    with pytest.raises(denied):
        use_case(store=store).execute(
            request=request_type(source_id=revision.source_id, **details),
            context=CallerContext(tenant_id="tenant_a", permissions={"source.create"}),
        )
    assert store.mock_calls == []
