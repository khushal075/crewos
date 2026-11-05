"""The demo exercises real temporary source/cache storage without a model download."""

import json

from crewos.retrieval import vector_demo
from crewos.retrieval.embedding_contracts import EmbeddingBatch
from crewos.retrieval.embedding_storage_contracts import EmbeddingSpace


class FixtureProvider:
    def embed(self, *, request):
        vectors = []
        for text in request.texts:
            if any(word in text.lower() for word in ("token", "credentials")):
                vectors.append((1.0, 0.0, 0.0))
            elif any(word in text.lower() for word in ("webhook", "timeout")):
                vectors.append((0.0, 1.0, 0.0))
            else:
                vectors.append((0.0, 0.0, 1.0))
        return EmbeddingBatch(
            model_id="fixture", model_revision="a" * 64, dimensions=3, vectors=vectors
        )


def test_demo_reports_observed_results_and_warm_cache_reuse():
    space = EmbeddingSpace(
        provider_id="fixture",
        model_id="fixture",
        model_revision="a" * 64,
        dimensions=3,
        preprocessing_revision="identity_v1",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )
    report = vector_demo.run_vector_demo(provider=FixtureProvider(), space=space)
    assert report["vector_expected_at_1_count"] == 3
    assert report["cold_provider_calls"] == 6
    assert report["warm_provider_calls"] == 3
    assert "not representative" in report["scope"]


def test_demo_cli_passes_configured_model_and_returns_result(monkeypatch, capsys):
    monkeypatch.setattr(vector_demo, "OllamaEmbeddingProvider", lambda **kwargs: FixtureProvider())
    assert (
        vector_demo.main(["--model", "fixture", "--revision", "a" * 64, "--dimensions", "3"]) == 0
    )
    assert json.loads(capsys.readouterr().out)["case_count"] == 3
