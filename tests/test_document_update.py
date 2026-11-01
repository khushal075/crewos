"""Authorized updates preserve identity/history and reject stale or competing edits."""

from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

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
from crewos.chunking import ChunkingLimitExceeded, ChunkingOptions, build_character_chunking
from crewos.ingestion import (
    DocumentIngestionFacade,
    DocumentIngestionRequest,
    DocumentNotFound,
    DocumentParseError,
    DocumentUpdateRequest,
    DocumentVersionConflict,
    build_plain_text_ingestion_facade,
    build_plain_text_preparation,
    build_sqlite_document_store,
)
from crewos.ingestion.document_processing_coordinator import DocumentProcessingCoordinator


def context(tenant="tenant_a", permissions=("document.create", "document.update")):
    return ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions))


def executor():
    return PolicyEnforcedExecutor(PermissionPolicyService())


@pytest.fixture
def existing(tmp_path):
    store = build_sqlite_document_store(tmp_path / "documents.sqlite3")
    facade = build_plain_text_ingestion_facade(store=store, executor=executor())
    first = facade.ingest(
        request=DocumentIngestionRequest(
            content=b"Original recovery instructions.", title="Original", source_ref="old-reference"
        ),
        context=context(),
    )
    return store, facade, first


def update_request(
    first, *, expected=None, content=b"Updated recovery instructions.", title="Updated"
):
    return DocumentUpdateRequest(
        document_id=first.document_id,
        expected_version=first.version if expected is None else expected,
        content=content,
        title=title,
    )


def test_update_preserves_id_appends_revision_and_replaces_metadata(existing):
    store, facade, first = existing
    second = facade.update(
        request=update_request(first), context=context(permissions=("document.update",))
    )
    assert second.document_id == first.document_id
    assert second.version == 2
    assert second.tenant_id == first.tenant_id
    assert second.document.title == "Updated"
    assert second.document.source_ref is None
    assert second.document.text == "Updated recovery instructions."
    assert second.chunking.chunks[0].text == second.document.text
    assert store.get(tenant_id="tenant_a", document_id=first.document_id, version=1) == first
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == second
    third = facade.update(
        request=update_request(second, content=b"Third revision"), context=context()
    )
    assert third.version == 3


@pytest.mark.parametrize("permissions", [(), ("document.create",)])
def test_denied_update_does_not_invoke_coordinator(existing, permissions):
    store, facade, first = existing
    processing = Mock(spec=DocumentProcessingCoordinator)
    protected = DocumentIngestionFacade(coordinator=processing, executor=executor())
    with pytest.raises(PolicyDeniedError):
        protected.update(request=update_request(first), context=context(permissions=permissions))
    processing.update.assert_not_called()
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == first


def test_policy_evaluation_failure_does_not_invoke_coordinator(existing):
    store, facade, first = existing
    policy = Mock(spec=PolicyServiceInterface)
    policy.evaluate.side_effect = RuntimeError("Policy offline")
    processing = Mock(spec=DocumentProcessingCoordinator)
    protected = DocumentIngestionFacade(
        coordinator=processing, executor=PolicyEnforcedExecutor(policy)
    )
    with pytest.raises(PolicyEvaluationError):
        protected.update(request=update_request(first), context=context())
    processing.update.assert_not_called()


def test_cross_tenant_update_fails_before_preparation(existing):
    store, facade, first = existing
    preparation, chunking = Mock(), Mock()
    coordinator = DocumentProcessingCoordinator(
        preparation=preparation, chunking=chunking, store=store
    )
    protected = DocumentIngestionFacade(coordinator=coordinator, executor=executor())
    with pytest.raises(DocumentNotFound):
        protected.update(request=update_request(first), context=context("tenant_b"))
    preparation.prepare.assert_not_called()
    chunking.chunk.assert_not_called()
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == first


def test_missing_document_is_not_created_by_update(existing):
    store, facade, first = existing
    command = update_request(first).model_copy(update={"document_id": uuid4()})
    with pytest.raises(DocumentNotFound):
        facade.update(request=command, context=context())
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=command.document_id)


def test_stale_update_stops_before_preparation(existing):
    store, facade, first = existing
    second = facade.update(request=update_request(first), context=context())
    preparation, chunking = Mock(), Mock()
    coordinator = DocumentProcessingCoordinator(
        preparation=preparation, chunking=chunking, store=store
    )
    protected = DocumentIngestionFacade(coordinator=coordinator, executor=executor())
    with pytest.raises(DocumentVersionConflict):
        protected.update(request=update_request(first), context=context())
    preparation.prepare.assert_not_called()
    chunking.chunk.assert_not_called()
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == second


def test_future_expected_revision_is_also_a_conflict(existing):
    store, facade, first = existing
    with pytest.raises(DocumentVersionConflict):
        facade.update(request=update_request(first, expected=2), context=context())
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == first


@pytest.mark.parametrize(
    "failure_stage,error",
    [
        ("prepare", DocumentParseError("private-failure-detail")),
        ("chunk", ChunkingLimitExceeded("private-failure-detail")),
        ("save", RuntimeError("private-failure-detail")),
    ],
)
def test_processing_failures_preserve_prior_version(existing, failure_stage, error, caplog):
    store, facade, first = existing
    preparation = Mock(wraps=build_plain_text_preparation())
    chunking = Mock(wraps=build_character_chunking())
    storage = Mock(wraps=store)
    operation = {
        "prepare": preparation.prepare,
        "chunk": chunking.chunk,
        "save": storage.create_version,
    }[failure_stage]
    operation.side_effect = error
    protected = DocumentIngestionFacade(
        coordinator=DocumentProcessingCoordinator(
            preparation=preparation, chunking=chunking, store=storage
        ),
        executor=executor(),
    )
    with caplog.at_level(logging.INFO):
        with pytest.raises(type(error)):
            protected.update(request=update_request(first), context=context())
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == first
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=first.document_id, version=2)
    if failure_stage != "save":
        storage.create_version.assert_not_called()
    assert "document_update_failed" in caplog.text
    assert "document_update_succeeded" not in caplog.text
    assert "private-failure-detail" not in caplog.text


def test_success_events_exclude_source_content(existing, caplog):
    store, facade, first = existing
    with caplog.at_level(logging.INFO):
        facade.update(
            request=update_request(
                first, content=b"private-replacement-content", title="private-title"
            ),
            context=context(),
        )
    assert "document_update_started" in caplog.text
    assert "document_update_succeeded" in caplog.text
    assert "private-replacement-content" not in caplog.text
    assert "private-title" not in caplog.text


def test_competing_updates_detect_race_after_both_latest_reads(existing):
    store, facade, first = existing
    barrier = Barrier(2)
    preparation = build_plain_text_preparation()

    class SynchronizedPreparation:
        def prepare(self, **kwargs):
            result = preparation.prepare(**kwargs)
            barrier.wait(timeout=5)
            return result

    protected = DocumentIngestionFacade(
        coordinator=DocumentProcessingCoordinator(
            preparation=SynchronizedPreparation(), chunking=build_character_chunking(), store=store
        ),
        executor=executor(),
    )

    def edit(index):
        try:
            return protected.update(
                request=update_request(first, content=f"Writer {index} replacement".encode()),
                context=context(),
            )
        except DocumentVersionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, (0, 1)))
    winners = [record for record in results if record is not None]
    assert len(winners) == 1
    assert winners[0].version == 2
    assert store.get(tenant_id="tenant_a", document_id=first.document_id) == winners[0]
    assert store.get(tenant_id="tenant_a", document_id=first.document_id, version=1) == first
    with pytest.raises(DocumentNotFound):
        store.get(tenant_id="tenant_a", document_id=first.document_id, version=3)


def test_revalidates_constructed_update_before_storage_lookup(existing):
    store, facade, first = existing
    storage = Mock(wraps=store)
    coordinator = DocumentProcessingCoordinator(preparation=Mock(), chunking=Mock(), store=storage)
    protected = DocumentIngestionFacade(coordinator=coordinator, executor=executor())
    invalid = update_request(first).model_copy(update={"expected_version": True})
    with pytest.raises(ValidationError):
        protected.update(request=invalid, context=context())
    storage.get.assert_not_called()
    storage.create_version.assert_not_called()
