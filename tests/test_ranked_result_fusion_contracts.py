"""Fusion inputs preserve source rankings without granting authority."""

import pytest
from pydantic import ValidationError

from crewos.retrieval import RankedResultFusionRequest, RetrievalRequest, RetrievalResult


def test_empty_child_hits_and_repeated_strategy_names_are_valid():
    # Two independently configured children may use the same algorithm.
    children = (RetrievalResult(strategy="keyword_v1"), RetrievalResult(strategy="keyword_v1"))
    request = RankedResultFusionRequest(
        request=RetrievalRequest(query="webhook timeout", top_k=2), ranked_results=children
    )
    assert request.ranked_results == children
    assert RankedResultFusionRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("count", [0, 9])
def test_child_list_admission_is_bounded(count):
    with pytest.raises(ValidationError):
        RankedResultFusionRequest(
            request=RetrievalRequest(query="webhook"),
            ranked_results=(RetrievalResult(strategy="keyword_v1"),) * count,
        )


def test_fusion_contract_does_not_accept_authority_claims_and_is_frozen():
    values = {
        "request": RetrievalRequest(query="webhook"),
        "ranked_results": (RetrievalResult(strategy="keyword_v1"),),
    }
    for claim in ({"tenant_id": "other"}, {"permissions": ["document.read"]}):
        with pytest.raises(ValidationError):
            RankedResultFusionRequest(**values, **claim)
    request = RankedResultFusionRequest(**values)
    with pytest.raises(ValidationError):
        request.ranked_results = ()
