"""Graded metrics, labels, negatives and explicit error accounting."""

import json
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID
import pytest
from pydantic import ValidationError
from crewos.evaluation.evaluate_retrieval import graded_metrics, evaluate_strategies, run_comparison
from crewos.evaluation.retrieval_dataset import RelevanceDataset

DATASET = (
    Path(__file__).resolve().parents[1] / "docs/evaluation/datasets/integration_incidents_v1.json"
)


def test_graded_metrics_use_ranks_multiple_labels_and_fixed_precision_denominator():
    values = graded_metrics(["low", "high"], {"high": 3, "low": 1, "missing": 2}, k=3)
    assert values["recall_at_k"] == pytest.approx(2 / 3)
    assert values["precision_at_k"] == pytest.approx(2 / 3)
    assert values["mrr_at_k"] == 1
    assert 0 < values["ndcg_at_k"] < 1
    assert (
        graded_metrics(["high", "missing", "low"], {"high": 3, "low": 1, "missing": 2}, k=3)[
            "ndcg_at_k"
        ]
        == 1
    )
    assert graded_metrics(["unrelated", "high"], {"high": 3}, k=2)["mrr_at_k"] == 0.5


def test_unanswerable_queries_are_not_given_fake_recall():
    assert graded_metrics(["anything"], {}, k=3)["negative_hit"]
    assert graded_metrics([], {}, k=3)["negative_hit"] is False
    assert graded_metrics([], {}, k=3)["recall_at_k"] is None


def test_dataset_rejects_unknown_or_cross_tenant_labels():
    values = json.loads(DATASET.read_text())
    values["queries"][0]["relevant"] = {"other_private": 3}
    with pytest.raises(ValidationError):
        RelevanceDataset.model_validate(values)
    values["queries"][0]["relevant"] = {"missing": 3}
    with pytest.raises(ValidationError):
        RelevanceDataset.model_validate(values)


def test_offline_comparison_uses_same_holdout_and_scopes():
    dataset = RelevanceDataset.model_validate_json(DATASET.read_text())
    report = run_comparison(dataset=dataset)
    assert [r["strategy"] for r in report["reports"]] == ["bm25_scan", "fts5_index"]
    assert len(report["dataset_sha256"]) == 64
    for strategy in report["reports"]:
        assert strategy["error_count"] == 0
        assert strategy["query_count"] == 19
        assert strategy["negative_successful_query_count"] == 4
        assert all(row["split"] == "holdout" for row in strategy["queries"])


def test_backend_errors_are_visible_and_not_mislabeled_as_empty_success():
    dataset = RelevanceDataset.model_validate_json(DATASET.read_text())
    broken = Mock()
    broken.search.side_effect = OSError("do not expose private error body")
    identities = {doc.id: UUID(int=i, version=4) for i, doc in enumerate(dataset.documents, 1)}
    report = evaluate_strategies(
        dataset=dataset, strategies={"broken": broken}, identities=identities
    )
    result = report["reports"][0]
    assert result["error_count"] == result["query_count"]
    assert result["metrics"]["recall_at_k"] == 0
    assert result["negative_hit_rate"] is None
    assert all(row["error_type"] == "OSError" for row in result["queries"])
    assert "private error body" not in json.dumps(report)


def test_selected_index_strategy_can_run_without_scan_baseline():
    dataset = RelevanceDataset.model_validate_json(DATASET.read_text())
    report = run_comparison(dataset=dataset, strategy_names=["fts5_index"])
    assert [r["strategy"] for r in report["reports"]] == ["fts5_index"]
    with pytest.raises(ValueError, match="missing model"):
        run_comparison(dataset=dataset, strategy_names=["vector"])
