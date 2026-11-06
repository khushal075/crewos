"""The lifecycle verifier must exercise storage/cleanup and report failures honestly."""

import json

import pytest

from crewos.retrieval import vector_lifecycle_demo as demo
from crewos.retrieval.embedding_storage_contracts import EmbeddingSpace
from tests.test_vector_demo import FixtureProvider


def fixture_space():
    return EmbeddingSpace(
        provider_id="fixture",
        model_id="fixture",
        model_revision="a" * 64,
        dimensions=3,
        preprocessing_revision="identity_v1",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )


def test_lifecycle_uses_real_cleanup_cli_and_reports_work_without_inventing_cost():
    report = demo.run_vector_lifecycle(provider=FixtureProvider(), space=fixture_space())
    assert report["passed"]
    assert all(report["checks"].values())
    assert report["relevance"]["recall_at_1"] == 1
    assert report["embedding_work"]["successful_calls"] == 12
    phases = [call["phase"] for call in report["embedding_work"]["calls"]]
    assert phases.count("cold") == 6
    assert phases.count("warm_reopened") == 3
    assert phases.count("updated") == 2
    assert phases.count("deleted") == 1
    assert "authorization" not in phases
    assert report["cost"]["monetary_cost"] is None
    assert report["cost"]["token_usage"] is None
    assert "temporary databases" in report["scope"]
    # Reports deliberately exclude vectors and submitted content.
    serialized = json.dumps(report)
    assert "Rotate the expired token" not in serialized
    assert '"vectors"' not in serialized


def test_missing_cleanup_cannot_produce_success(monkeypatch):
    monkeypatch.setattr(demo, "_cleanup", lambda *args: {"outcomes": {"idle": 1}})
    report = demo.run_vector_lifecycle(provider=FixtureProvider(), space=fixture_space())
    assert not report["passed"]
    assert not report["checks"]["update_cleanup_completed"]
    assert not report["checks"]["obsolete_write_fenced"]
    assert not report["checks"]["deleted_vectors_absent_after_reopen"]


def test_cli_returns_failure_for_failed_verification(monkeypatch, capsys):
    monkeypatch.setattr(demo, "OllamaEmbeddingProvider", lambda **kwargs: object())
    monkeypatch.setattr(demo, "run_vector_lifecycle", lambda **kwargs: {"passed": False})
    assert (
        demo.main(
            ["--model", "nomic-embed-text:v1.5", "--revision", "a" * 64, "--dimensions", "768"]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out) == {"passed": False}


def test_cli_rejects_implicit_prefixes_for_other_models():
    with pytest.raises(SystemExit) as error:
        demo.main(["--model", "another-model", "--revision", "a" * 64, "--dimensions", "768"])
    assert error.value.code == 2
