"""Authorization, trusted scope, backend failures, and result-limit enforcement."""

import logging
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
from crewos.chunking import TextChunk
from crewos.retrieval import (
    PassageRetrieverInterface,
    RetrievalFacade,
    RetrievalRequest,
    RetrievedPassage,
    RetrievalResult,
    RetrievalResultError,
)


def context(permissions=("document.search", "document.read")):
    return ToolExecutionContext(tenant_id="tenant_a", permissions=frozenset(permissions))


def setup(result=None):
    backend = Mock(spec=PassageRetrieverInterface)
    backend.search.return_value = (
        result if result is not None else RetrievalResult(strategy="fixture_v1")
    )
    facade = RetrievalFacade(
        retriever=backend, executor=PolicyEnforcedExecutor(PermissionPolicyService())
    )
    return facade, backend


def hit():
    return RetrievedPassage(
        document_id=uuid4(),
        document_version=1,
        title="Private title",
        chunk=TextChunk(index=0, text="Private source", start_char=0, end_char=14),
    )


def test_passes_trusted_tenant_and_validated_request_to_strategy():
    expected = RetrievalResult(strategy="fixture_v1", passages=(hit(),))
    facade, backend = setup(expected)
    request = RetrievalRequest(query="Private query", top_k=1)
    assert facade.search(request=request, context=context()) == expected
    backend.search.assert_called_once_with(request=request, tenant_id="tenant_a")


@pytest.mark.parametrize("permissions", [(), ("document.read",), ("document.search",)])
def test_either_missing_permission_prevents_backend_access(permissions):
    facade, backend = setup()
    with pytest.raises(PolicyDeniedError):
        facade.search(request=RetrievalRequest(query="Find runbook"), context=context(permissions))
    backend.search.assert_not_called()


def test_policy_failure_stops_before_backend():
    backend = Mock(spec=PassageRetrieverInterface)
    policy = Mock(spec=PolicyServiceInterface)
    policy.evaluate.side_effect = RuntimeError("Unavailable")
    facade = RetrievalFacade(retriever=backend, executor=PolicyEnforcedExecutor(policy))
    with pytest.raises(PolicyEvaluationError):
        facade.search(request=RetrievalRequest(query="Find runbook"), context=context())
    backend.search.assert_not_called()


def test_backend_failure_propagates_without_becoming_empty_results(caplog):
    facade, backend = setup()
    backend.search.side_effect = RuntimeError("private-storage-detail")
    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError):
            facade.search(request=RetrievalRequest(query="private-query"), context=context())
    assert "retrieval_failed" in caplog.text and "retrieval_succeeded" not in caplog.text
    assert "private-storage-detail" not in caplog.text and "private-query" not in caplog.text


def test_success_logs_counts_without_query_or_passage_content(caplog):
    facade, _ = setup(RetrievalResult(strategy="fixture_v1", passages=(hit(),)))
    with caplog.at_level(logging.INFO):
        facade.search(request=RetrievalRequest(query="private-query"), context=context())
    assert "retrieval_succeeded" in caplog.text
    for secret in ("private-query", "Private source", "Private title"):
        assert secret not in caplog.text


def test_top_k_violation_is_rejected_instead_of_truncating_silently():
    facade, _ = setup(RetrievalResult(strategy="fixture_v1", passages=(hit(), hit())))
    with pytest.raises(RetrievalResultError):
        facade.search(request=RetrievalRequest(query="Find", top_k=1), context=context())


def test_wrong_response_type_is_rejected():
    facade, backend = setup()
    backend.search.return_value = {"passages": []}
    with pytest.raises(RetrievalResultError):
        facade.search(request=RetrievalRequest(query="Find"), context=context())


def test_constructed_invalid_request_is_revalidated_before_backend():
    facade, backend = setup()
    invalid = RetrievalRequest(query="Find").model_copy(update={"top_k": True})
    with pytest.raises(ValidationError):
        facade.search(request=invalid, context=context())
    backend.search.assert_not_called()


def test_constructed_invalid_result_is_revalidated():
    invalid = RetrievalResult(strategy="fixture_v1").model_copy(update={"strategy": " "})
    facade, _ = setup(invalid)
    with pytest.raises(ValidationError):
        facade.search(request=RetrievalRequest(query="Find"), context=context())
