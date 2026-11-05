"""Smoke runner reports actual checks without exposing vectors or source text."""

import json

import pytest

from crewos.retrieval.embedding_contracts import EmbeddingBatch
from crewos.retrieval import embedding_demo
from crewos.third_party.llm.ollama_embedding_adapter import EmbeddingProviderError


class ExampleProvider:
    def embed(self, *, request):
        assert request.texts == embedding_demo.SYNTHETIC_TEXTS
        return EmbeddingBatch(
            model_id="fixture",
            model_revision="fixture-revision",
            dimensions=2,
            vectors=((1, 0), (0, 1)),
        )


def test_smoke_reports_shape_without_claiming_relevance():
    report = embedding_demo.run_embedding_smoke(ExampleProvider())
    assert report["dimensions"] == 2
    assert report["vector_count"] == report["input_count"] == 2
    assert report["all_vectors_nonzero"]
    assert not report["revision_verified_by_adapter"]
    assert "not semantic relevance" in report["scope"]
    assert "vectors" not in report


@pytest.mark.parametrize("vectors", [((0, 0), (1, 0)), ((1, 0),)])
def test_smoke_rejects_degenerate_or_incomplete_results(vectors):
    class InvalidProvider:
        def embed(self, *, request):
            return EmbeddingBatch(
                model_id="fixture", model_revision="r1", dimensions=2, vectors=vectors
            )

    with pytest.raises(ValueError):
        embedding_demo.run_embedding_smoke(InvalidProvider())


def test_cli_uses_explicit_configuration_and_prints_report(monkeypatch, capsys):
    configs = []

    def build(*, config):
        configs.append(config)
        return ExampleProvider()

    monkeypatch.setattr(embedding_demo, "OllamaEmbeddingProvider", build)
    assert (
        embedding_demo.main(["--model", "fixture", "--revision", "a" * 64, "--dimensions", "2"])
        == 0
    )
    assert configs[0].model == "fixture"
    assert json.loads(capsys.readouterr().out)["vector_count"] == 2


@pytest.mark.parametrize(
    "error", [EmbeddingProviderError("private text"), ValueError("private text")]
)
def test_cli_fails_without_exposing_error_content(monkeypatch, capsys, error):
    def fail(*, config):
        raise error

    monkeypatch.setattr(embedding_demo, "OllamaEmbeddingProvider", fail)
    assert (
        embedding_demo.main(["--model", "fixture", "--revision", "a" * 64, "--dimensions", "2"])
        == 1
    )
    assert json.loads(capsys.readouterr().out) == {
        "check": "embedding_inference_smoke",
        "status": "failed",
    }
