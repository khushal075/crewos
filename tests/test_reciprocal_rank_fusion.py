"""Rank aggregation preserves source evidence and rejects contradictory payloads."""

import pytest
from pydantic import ValidationError

from crewos.retrieval import RankedResultFusionInterface, RankedResultFusionRequest
from crewos.retrieval import RetrievalRequest, RetrievalResult, RetrievalResultError
from crewos.retrieval.strategies.reciprocal_rank_fusion import ReciprocalRankFusion
from tests.test_retrieval_contracts import passage


def fuse(*children, top_k=20, rank_constant=60):
    strategy: RankedResultFusionInterface = ReciprocalRankFusion(rank_constant=rank_constant)
    return strategy.fuse(
        request=RankedResultFusionRequest(
            request=RetrievalRequest(query="webhook recovery", top_k=top_k),
            ranked_results=tuple(
                RetrievalResult(strategy="fixture_v1", passages=items) for items in children
            ),
        )
    )


def test_agreement_across_lists_outweighs_a_single_first_place():
    a, b, c = passage(), passage(), passage()
    # a: 1/61; b: 1/62 + 1/61; c: 1/62.
    result = fuse((a, b), (b, c))
    assert result.passages == (b, a, c)
    assert result.strategy == "reciprocal_rank_fusion_v1"
    assert fuse((a, b), (b, c), top_k=1).passages == (b,)


def test_one_based_rank_and_configurable_constant_affect_ranking():
    a, b, c, d = passage(), passage(), passage(), passage()
    # At constant 1: a=1/2, d=1/5+1/4 < a. At 60, consensus favors d.
    children = ((a, b, c, d), (b, c, d))
    assert fuse(*children, rank_constant=1).passages.index(a) < fuse(
        *children, rank_constant=1
    ).passages.index(d)
    assert fuse(*children, rank_constant=60).passages.index(d) < fuse(
        *children, rank_constant=60
    ).passages.index(a)


def test_exact_ties_use_first_appearance_not_float_accumulation_order():
    a, b, c = passage(), passage(), passage()
    result = fuse((a, b, c), (b, c, a), (c, a, b))
    assert result.passages == (a, b, c)
    assert fuse((c, b, a), (a, c, b), (b, a, c)).passages == (c, b, a)


def test_empty_children_and_single_child_preserve_ranking():
    a, b = passage(), passage()
    assert fuse((), ()).passages == ()
    assert fuse((), (b, a), ()).passages == (b, a)


@pytest.mark.parametrize("field,value", [("title", "Changed"), ("source_ref", "other:ref")])
def test_conflicting_metadata_is_rejected_even_outside_selected_top_k(field, value):
    a, b = passage(), passage()
    conflicting = b.model_copy(update={field: value})
    with pytest.raises(RetrievalResultError, match="Conflicting"):
        fuse((a, b), (a, conflicting), top_k=1)


def test_conflicting_chunk_text_is_rejected():
    a = passage()
    conflicting = a.model_copy(update={"chunk": a.chunk.model_copy(update={"text": "Other text!"})})
    with pytest.raises(RetrievalResultError):
        fuse((a,), (conflicting,))


def test_versions_and_chunks_have_distinct_identity():
    a = passage()
    version = a.model_copy(update={"document_version": 3})
    chunk = a.model_copy(update={"chunk": a.chunk.model_copy(update={"index": 1})})
    assert fuse((a, version, chunk), (a,)).passages == (a, version, chunk)


def test_nested_model_copy_cannot_bypass_validation():
    a = passage().model_copy(update={"document_version": 0})
    with pytest.raises(ValidationError):
        fuse((a,))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "60", 10001, float("inf")])
def test_rank_constant_admission(value):
    with pytest.raises(ValueError):
        ReciprocalRankFusion(rank_constant=value)
