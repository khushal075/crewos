"""Native embedding requests and failure handling with controlled HTTP responses."""

import io
import json
from http.client import IncompleteRead
from unittest.mock import MagicMock, Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from pydantic import ValidationError

from crewos.core.config import Settings
from crewos.core.embedding_setup import build_text_embedding_provider
from crewos.retrieval.embedding_contracts import TextEmbeddingRequest
from crewos.third_party.llm.ollama_embedding_adapter import (
    EmbeddingProviderError,
    OllamaEmbeddingConfig,
    OllamaEmbeddingProvider,
    _NoRedirects,
)


def config(**changes):
    return OllamaEmbeddingConfig(
        **{
            "base_url": "http://localhost:11434/",
            "model": "example:latest",
            "model_revision": "fixture-digest",
            "dimensions": 2,
            **changes,
        }
    )


def provider(monkeypatch, payload=None, raw=None, error=None):
    opener = Mock()
    if error is not None:
        opener.open.side_effect = error
    else:
        if raw is None:
            raw = json.dumps(
                payload
                if payload is not None
                else {
                    "model": "example:latest",
                    "embeddings": [[1, 0], [0.5, -0.2]],
                    "total_duration": 10,
                }
            ).encode()
        opener.open.return_value = io.BytesIO(raw)
    monkeypatch.setattr(
        "crewos.third_party.llm.ollama_embedding_adapter.build_opener", lambda *args: opener
    )
    return OllamaEmbeddingProvider(config=config()), opener


def test_sends_ordered_exact_text_with_truncation_disabled(monkeypatch):
    adapter, opener = provider(monkeypatch)
    request = TextEmbeddingRequest(texts=["  timeout\n", "Expired token"])
    result = adapter.embed(request=request)
    wire = opener.open.call_args.args[0]
    assert wire.full_url == "http://localhost:11434/api/embed"
    assert wire.get_method() == "POST"
    assert wire.get_header("Content-type") == "application/json"
    assert json.loads(wire.data) == {
        "model": "example:latest",
        "input": list(request.texts),
        "truncate": False,
    }
    assert opener.open.call_args.kwargs["timeout"] == 30
    assert result.vectors == ((1.0, 0.0), (0.5, -0.2))
    assert result.model_revision == "fixture-digest"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"error": "private source text"},
        {"model": "different", "embeddings": [[1, 2]]},
        {"model": "example:latest", "embeddings": [[1, 2]]},
        {"model": "example:latest", "embeddings": [[1], [2]]},
        {"model": "example:latest", "embeddings": [[True, 0], [0, 1]]},
        {"model": "example:latest", "embeddings": [[float("nan"), 0], [0, 1]]},
    ],
)
def test_rejects_provider_errors_mismatches_and_malformed_vectors(monkeypatch, payload):
    adapter, opener = provider(monkeypatch, payload=payload)
    with pytest.raises(EmbeddingProviderError, match="invalid embedding response") as captured:
        adapter.embed(request=TextEmbeddingRequest(texts=["private source text", "second"]))
    assert "private source text" not in str(captured.value)
    assert captured.value.__suppress_context__
    assert opener.open.call_count == 1


@pytest.mark.parametrize("raw", [b"not JSON: private source text", b"\xff"])
def test_rejects_non_json_or_invalid_encoding(monkeypatch, raw):
    adapter, _ = provider(monkeypatch, raw=raw)
    with pytest.raises(EmbeddingProviderError, match="invalid embedding response"):
        adapter.embed(request=TextEmbeddingRequest(texts=["text"]))


@pytest.mark.parametrize(
    "error", [URLError("private"), TimeoutError("private"), IncompleteRead(b"private")]
)
def test_transport_failures_are_explicit_private_and_not_retried(monkeypatch, error):
    adapter, opener = provider(monkeypatch, error=error)
    with pytest.raises(EmbeddingProviderError, match="transport failed") as captured:
        adapter.embed(request=TextEmbeddingRequest(texts=["text"]))
    assert "private" not in str(captured.value)
    assert opener.open.call_count == 1


def test_http_failure_does_not_expose_response_body(monkeypatch):
    error = HTTPError("http://localhost", 400, "private", {}, io.BytesIO(b"private"))
    adapter, opener = provider(monkeypatch, error=error)
    with pytest.raises(EmbeddingProviderError, match="HTTP 400"):
        adapter.embed(request=TextEmbeddingRequest(texts=["text"]))
    assert opener.open.call_count == 1


def test_response_size_limit(monkeypatch):
    adapter, opener = provider(monkeypatch)
    response = Mock()
    response.read.return_value = b"x" * 1025
    opener.open.return_value = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    adapter._config = config(max_response_bytes=1024)
    with pytest.raises(EmbeddingProviderError, match="admission limit"):
        adapter.embed(request=TextEmbeddingRequest(texts=["text"]))
    response.read.assert_called_once_with(1025)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost",
        "http://",
        "http://user:secret@localhost",
        "http://localhost/v1",
        "http://localhost?key=secret",
        "http://localhost/#fragment",
        "http://local host",
        "http://localhost:invalid",
        "http://localhost:0",
    ],
)
def test_rejects_ambiguous_or_credential_bearing_endpoints(url):
    with pytest.raises(ValidationError):
        config(base_url=url)


@pytest.mark.parametrize(
    "changes",
    [
        {"dimensions": True},
        {"dimensions": 0},
        {"model": " "},
        {"model_revision": ""},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 0},
        {"max_response_bytes": 0},
    ],
)
def test_configuration_rejects_invalid_limits_or_identity(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_redirect_handler_refuses_to_forward_source_content():
    assert (
        _NoRedirects().redirect_request(
            Request("http://localhost"), None, 307, "redirect", {}, "http://other"
        )
        is None
    )


def test_factory_requires_explicit_embedding_configuration():
    settings = Settings(
        _env_file=None,
        EMBEDDING_MODEL=None,
        EMBEDDING_MODEL_REVISION=None,
        EMBEDDING_DIMENSIONS=None,
    )
    with pytest.raises(ValueError, match="Configure embedding"):
        build_text_embedding_provider(settings)


def test_factory_builds_provider_without_network(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(
        "crewos.third_party.llm.ollama_embedding_adapter.build_opener", lambda *args: opener
    )
    settings = Settings(
        _env_file=None,
        EMBEDDING_MODEL="example:latest",
        EMBEDDING_MODEL_REVISION="fixture-digest",
        EMBEDDING_DIMENSIONS=2,
    )
    assert isinstance(build_text_embedding_provider(settings), OllamaEmbeddingProvider)
    opener.open.assert_not_called()
