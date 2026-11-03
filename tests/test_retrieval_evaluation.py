from uuid import UUID

import pytest

from crewos.chunking import TextChunk
from crewos.retrieval import RetrievalResult, RetrievedPassage
from crewos.retrieval.evaluate_demo import run_demo
from crewos.retrieval.retrieval_evaluation import (
    QueryMetrics,
    evaluate_result,
    summarize_queries,
)


def result(*numbers):
    return RetrievalResult(
        strategy="test",
        passages=tuple(
            RetrievedPassage(
                document_id=UUID(int=n, version=4),
                document_version=1,
                title="Runbook",
                chunk=TextChunk(index=0, text="test", start_char=0, end_char=4),
            )
            for n in numbers
        ),
    )


def labels(*numbers):
    return frozenset((UUID(int=n, version=4), 1, 0) for n in numbers)


def test_rank_and_recall_measure_different_properties():
    measured = evaluate_result(result(3, 1), relevant=labels(1, 2), k=2)
    assert measured == QueryMetrics(recall_at_k=0.5, reciprocal_rank_at_k=0.5)
    assert evaluate_result(result(3, 1), relevant=labels(1, 2), k=1) == QueryMetrics(0, 0)
    assert evaluate_result(result(), relevant=labels(1), k=2) == QueryMetrics(0, 0)


def test_source_version_and_chunk_are_part_of_relevance_identity():
    for reference in ((UUID(int=1, version=4), 2, 0), (UUID(int=1, version=4), 1, 1)):
        assert evaluate_result(result(1), relevant=frozenset({reference}), k=1) == QueryMetrics(
            0, 0
        )


@pytest.mark.parametrize("k", [0, 21, True, 1.0])
def test_invalid_cutoff_rejected(k):
    with pytest.raises(ValueError, match="k must"):
        evaluate_result(result(), relevant=labels(1), k=k)


def test_empty_labels_and_empty_query_sets_are_not_assigned_perfect_scores():
    with pytest.raises(ValueError, match="nonempty"):
        evaluate_result(result(), relevant=frozenset(), k=1)
    with pytest.raises(ValueError, match="At least one"):
        summarize_queries(())


def test_aggregate_weights_queries_equally():
    assert summarize_queries((QueryMetrics(0.5, 1), QueryMetrics(0, 0))) == {
        "mean_recall_at_k": 0.25,
        "mrr_at_k": 0.5,
    }


def test_real_baseline_report_exposes_paraphrase_misses_and_cutoff_loss():
    report = run_demo()
    rows = {row["case_id"]: row for row in report["queries"]}
    assert rows["literal_timeout"]["recall_at_k"] == 1
    assert rows["paraphrased_credentials"]["reciprocal_rank_at_k"] == 0
    assert rows["paraphrased_duplicates"]["recall_at_k"] == 0
    assert rows["two_recovery_sources"]["recall_at_k"] == 0.5
    assert report["mean_recall_at_k"] == pytest.approx(4.5 / 7)
    assert report["mrr_at_k"] == pytest.approx(5 / 7)
