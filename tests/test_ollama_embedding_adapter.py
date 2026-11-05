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
            "model_revision": "a" * 64,
            "dimensions": 2,
            **changes,
        }
    )


def provider(monkeypatch, payload=None, raw=None, error=None, before=None, after=None):
    opener = Mock()
    catalog = {
        "models": [{"name": "example:latest", "model": "example:latest", "digest": "a" * 64}]
    }
    checks = 0

    def open_response(request, **kwargs):
        nonlocal checks
        if request.full_url.endswith("/api/tags"):
            checks += 1
            metadata = before if checks == 1 else after
            if isinstance(metadata, Exception):
                raise metadata
            return io.BytesIO(json.dumps(catalog if metadata is None else metadata).encode())
        if error is not None:
            raise error
        content = raw
        if content is None:
            content = json.dumps(
                payload
                if payload is not None
                else {
                    "model": "example:latest",
                    "embeddings": [[1, 0], [0.5, -0.2]],
                    "total_duration": 10,
                }
            ).encode()
        return io.BytesIO(content)

    opener.open.side_effect = open_response
    monkeypatch.setattr(
        "crewos.third_party.llm.ollama_embedding_adapter.build_opener", lambda *args: opener
    )
    return OllamaEmbeddingProvider(config=config()), opener


def test_sends_ordered_exact_text_with_truncation_disabled(monkeypatch):
    adapter, opener = provider(monkeypatch)
    request = TextEmbeddingRequest(texts=["  timeout\n", "Expired token"])
    result = adapter.embed(request=request)
    wire = opener.open.call_args_list[1].args[0]
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
    assert result.model_revision == "a" * 64
    assert [call.args[0].get_method() for call in opener.open.call_args_list] == [
        "GET",
        "POST",
        "GET",
    ]
    assert [call.args[0].full_url.rsplit("/", 1)[-1] for call in opener.open.call_args_list] == [
        "tags",
        "embed",
        "tags",
    ]


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
    assert opener.open.call_count == 2


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
    assert opener.open.call_count == 2


def test_http_failure_does_not_expose_response_body(monkeypatch):
    error = HTTPError("http://localhost", 400, "private", {}, io.BytesIO(b"private"))
    adapter, opener = provider(monkeypatch, error=error)
    with pytest.raises(EmbeddingProviderError, match="HTTP 400"):
        adapter.embed(request=TextEmbeddingRequest(texts=["text"]))
    assert opener.open.call_count == 2


def test_response_size_limit(monkeypatch):
    adapter, opener = provider(monkeypatch, raw=b"x" * 1025)
    adapter._config = config(max_response_bytes=1024)
    with pytest.raises(EmbeddingProviderError, match="admission limit"):
        adapter.embed(request=TextEmbeddingRequest(texts=["text"]))
    assert opener.open.call_count == 2


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
        {"model_revision": "short-digest"},
        {"model_revision": "A" * 64},
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
        EMBEDDING_MODEL_REVISION="a" * 64,
        EMBEDDING_DIMENSIONS=2,
    )
    assert isinstance(build_text_embedding_provider(settings), OllamaEmbeddingProvider)
    opener.open.assert_not_called()


def test_active_execution_budget_limits_transport_timeout(monkeypatch):
    from crewos.application.services.execution_control import ExecutionControl, execution_scope

    adapter, opener = provider(monkeypatch)
    control = ExecutionControl()
    with execution_scope(control), control.tool_budget(5):
        adapter.embed(request=TextEmbeddingRequest(texts=["one", "two"]))
    assert 0 < opener.open.call_args.kwargs["timeout"] <= 5


@pytest.mark.parametrize(
    "metadata",
    [
        {"models": []},
        {"models": [{"name": "example:latest", "digest": "b" * 64}]},
        {"models": [{}]},
    ],
)
def test_initial_revision_failure_prevents_sending_text(monkeypatch, metadata):
    adapter, opener = provider(monkeypatch, before=metadata)
    with pytest.raises(EmbeddingProviderError, match="revision verification failed"):
        adapter.embed(request=TextEmbeddingRequest(texts=["private text"]))
    assert opener.open.call_count == 1
    assert opener.open.call_args.args[0].data is None


def test_changed_revision_after_inference_discards_result(monkeypatch):
    adapter, opener = provider(
        monkeypatch, after={"models": [{"name": "example:latest", "digest": "b" * 64}]}
    )
    with pytest.raises(EmbeddingProviderError, match="revision verification failed"):
        adapter.embed(request=TextEmbeddingRequest(texts=["one", "two"]))
    assert opener.open.call_count == 3


@pytest.mark.parametrize("stage", ["before", "after"])
def test_metadata_transport_failure_never_returns_vectors(monkeypatch, stage):
    adapter, opener = provider(monkeypatch, **{stage: URLError("private metadata")})
    with pytest.raises(EmbeddingProviderError, match="transport failed") as captured:
        adapter.embed(request=TextEmbeddingRequest(texts=["one", "two"]))
    assert "private" not in str(captured.value)
    assert opener.open.call_count == (1 if stage == "before" else 3)


def test_metadata_response_size_is_bounded_before_inference(monkeypatch):
    adapter, opener = provider(monkeypatch)
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"x" * 1_000_001
    opener.open.side_effect = None
    opener.open.return_value = response
    with pytest.raises(EmbeddingProviderError, match="admission limit"):
        adapter.embed(request=TextEmbeddingRequest(texts=["private text"]))
    response.__enter__.return_value.read.assert_called_once_with(1_000_001)
    assert opener.open.call_count == 1


def test_remaining_budget_is_recomputed_for_each_http_request(monkeypatch):
    from crewos.application.services.execution_control import ExecutionControl, execution_scope

    adapter, opener = provider(monkeypatch)
    control = Mock(spec=ExecutionControl)
    control.tool_remaining.side_effect = [6, 5, 4, 3, 2, 1]
    with execution_scope(control):
        adapter.embed(request=TextEmbeddingRequest(texts=["one", "two"]))
    assert [call.kwargs["timeout"] for call in opener.open.call_args_list] == [6, 4, 2]


def test_post_verification_failure_prevents_index_writes(monkeypatch, tmp_path):
    from crewos.ingestion import build_sqlite_document_store
    from crewos.retrieval.document_embedding_indexer import (
        DocumentEmbeddingIndexer,
        EmbeddingWorkBudget,
    )
    from tests.test_embedding_storage_contracts import space
    from tests.test_keyword_retrieval import save

    adapter, _ = provider(
        monkeypatch,
        payload={"model": "example:latest", "embeddings": [[1, 0]]},
        after={"models": [{"name": "example:latest", "digest": "b" * 64}]},
    )
    document = save(build_sqlite_document_store(tmp_path / "source.sqlite3"), "Expired token.")
    cache = Mock()
    cache.read_document.return_value = ()
    indexer = DocumentEmbeddingIndexer(
        provider=adapter,
        store=cache,
        space=space(model_id="example:latest", model_revision="a" * 64),
    )
    with pytest.raises(EmbeddingProviderError, match="revision verification failed"):
        indexer.ensure_document(
            document, budget=EmbeddingWorkBudget(), max_records=10, max_snapshot_bytes=100000
        )
    cache.put.assert_not_called()
