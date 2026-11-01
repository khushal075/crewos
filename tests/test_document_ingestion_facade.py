"""Authorized preparation/chunking/storage composition and failure boundaries."""

import logging
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.policy_service import PolicyServiceInterface
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import (
    PolicyDeniedError,
    PolicyEnforcedExecutor,
    PolicyEvaluationError,
)
from crewos.chunking import ChunkingLimitExceeded, ChunkingOptions
from crewos.ingestion import (
    DocumentIngestionRequest,
    DocumentIngestionFacade,
    DocumentNotFound,
    DocumentParseError,
    build_plain_text_ingestion_facade,
    build_sqlite_document_store,
)


from crewos.ingestion.document_processing_coordinator import DocumentProcessingCoordinator


def make_facade(*, preparation, chunking, store, executor):
    return DocumentIngestionFacade(
        coordinator=DocumentProcessingCoordinator(
            preparation=preparation, chunking=chunking, store=store
        ),
        executor=executor,
    )


def context(tenant="tenant_a", permissions=("document.create",)):
    return ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions))


def request():
    return DocumentIngestionRequest(
        content=b"  Private incident details.\n  Retry carefully.\n",
        title="CRM runbook",
        source_ref="private-runbook:v1",
    )


def executor():
    return PolicyEnforcedExecutor(PermissionPolicyService())


def test_authorized_call_connects_real_components_and_persists_version_one(tmp_path):
    path = tmp_path / "documents.sqlite3"
    facade = build_plain_text_ingestion_facade(
        store=build_sqlite_document_store(path),
        executor=executor(),
        chunking_options=ChunkingOptions(chunk_size=10, overlap=2),
    )
    saved = facade.ingest(request=request(), context=context())
    assert saved.tenant_id == "tenant_a"
    assert saved.version == 1
    assert saved.document_id.version == 4
    assert saved.document.text == request().content.decode()
    assert saved.chunking.options.chunk_size == 10
    assert len(saved.chunking.chunks) > 1
    assert (
        build_sqlite_document_store(path).get(tenant_id="tenant_a", document_id=saved.document_id)
        == saved
    )
    with pytest.raises(DocumentNotFound):
        build_sqlite_document_store(path).get(tenant_id="tenant_b", document_id=saved.document_id)


def test_no_permission_means_no_parsing_chunking_or_storage():
    preparation, chunking, store = Mock(), Mock(), Mock()
    facade = make_facade(
        preparation=preparation, chunking=chunking, store=store, executor=executor()
    )
    with pytest.raises(PolicyDeniedError):
        facade.ingest(request=request(), context=context(permissions=()))
    preparation.prepare.assert_not_called()
    chunking.chunk.assert_not_called()
    store.create_version.assert_not_called()


def test_policy_failure_stops_before_preparation():
    policy = Mock(spec=PolicyServiceInterface)
    policy.evaluate.side_effect = RuntimeError("Policy unavailable")
    preparation, chunking, store = Mock(), Mock(), Mock()
    facade = make_facade(
        preparation=preparation,
        chunking=chunking,
        store=store,
        executor=PolicyEnforcedExecutor(policy),
    )
    with pytest.raises(PolicyEvaluationError):
        facade.ingest(request=request(), context=context())
    preparation.prepare.assert_not_called()
    chunking.chunk.assert_not_called()
    store.create_version.assert_not_called()


def test_parse_failure_prevents_chunking_and_storage():
    preparation, chunking, store = Mock(), Mock(), Mock()
    preparation.prepare.side_effect = DocumentParseError("Failed")
    facade = make_facade(
        preparation=preparation, chunking=chunking, store=store, executor=executor()
    )
    with pytest.raises(DocumentParseError):
        facade.ingest(request=request(), context=context())
    chunking.chunk.assert_not_called()
    store.create_version.assert_not_called()


def test_chunking_failure_prevents_storage():
    store = Mock()
    chunking = Mock()
    chunking.chunk.side_effect = ChunkingLimitExceeded("Failed")
    from crewos.ingestion import build_plain_text_preparation

    facade = make_facade(
        preparation=build_plain_text_preparation(),
        chunking=chunking,
        store=store,
        executor=executor(),
    )
    with pytest.raises(ChunkingLimitExceeded):
        facade.ingest(request=request(), context=context())
    store.create_version.assert_not_called()


def test_storage_failure_propagates_and_logs_do_not_include_content(caplog):
    store = Mock()
    store.create_version.side_effect = RuntimeError("private-error-message")
    facade = build_plain_text_ingestion_facade(store=store, executor=executor())
    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="private-error-message"):
            facade.ingest(request=request(), context=context())
    assert "document_ingestion_failed" in caplog.text
    assert "document_ingestion_succeeded" not in caplog.text
    for sensitive in (
        "private-error-message",
        "Private incident",
        "CRM runbook",
        "private-runbook:v1",
    ):
        assert sensitive not in caplog.text


def test_success_logs_metadata_without_source_content(tmp_path, caplog):
    facade = build_plain_text_ingestion_facade(
        store=build_sqlite_document_store(tmp_path / "docs.sqlite3"), executor=executor()
    )
    with caplog.at_level(logging.INFO):
        saved = facade.ingest(request=request(), context=context())
    assert "document_ingestion_started" in caplog.text
    assert "document_ingestion_succeeded" in caplog.text
    assert str(saved.document_id) in caplog.text
    assert "Private incident" not in caplog.text
    assert "private-runbook:v1" not in caplog.text


def test_each_call_creates_a_new_document_without_claiming_idempotency(tmp_path):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3")
    facade = build_plain_text_ingestion_facade(store=store, executor=executor())
    first = facade.ingest(request=request(), context=context())
    second = facade.ingest(request=request(), context=context())
    assert first.document_id != second.document_id
    assert first.version == second.version == 1


def test_revalidates_constructed_request_before_parsing():
    preparation, chunking, store = Mock(), Mock(), Mock()
    facade = make_facade(
        preparation=preparation, chunking=chunking, store=store, executor=executor()
    )
    invalid = request().model_copy(update={"content": "not bytes"})
    with pytest.raises(ValidationError):
        facade.ingest(request=invalid, context=context())
    preparation.prepare.assert_not_called()
    store.create_version.assert_not_called()


def test_facade_denial_never_invokes_processing_coordinator():
    processing = Mock(spec=DocumentProcessingCoordinator)
    facade = DocumentIngestionFacade(coordinator=processing, executor=executor())
    with pytest.raises(PolicyDeniedError):
        facade.ingest(request=request(), context=context(permissions=()))
    processing.process.assert_not_called()


def test_facade_delegates_request_with_tenant_from_trusted_context(tmp_path):
    saved = build_plain_text_ingestion_facade(
        store=build_sqlite_document_store(tmp_path / "docs.sqlite3"), executor=executor()
    ).ingest(request=request(), context=context("tenant_b"))
    processing = Mock(spec=DocumentProcessingCoordinator)
    processing.process.return_value = saved
    facade = DocumentIngestionFacade(coordinator=processing, executor=executor())
    command = request()
    assert facade.ingest(request=command, context=context("tenant_b")) == saved
    processing.process.assert_called_once_with(request=command, tenant_id="tenant_b")
