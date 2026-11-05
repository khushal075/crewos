"""Native Ollama embedding adapter, independent of chat and retrieval wiring."""

import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crewos.retrieval.embedding_contracts import EmbeddingBatch, ModelLabel, TextEmbeddingRequest


class EmbeddingProviderError(RuntimeError):
    """Provider transport or response failure; message excludes submitted content."""


class OllamaEmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    model: ModelLabel
    model_revision: ModelLabel
    dimensions: int = Field(strict=True, ge=1, le=16384)
    timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    max_response_bytes: int = Field(default=32_000_000, strict=True, ge=1024, le=64_000_000)

    @field_validator("base_url")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        try:
            port = parts.port
        except ValueError:
            raise ValueError("Embedding URL has an invalid port") from None
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or any(char.isspace() for char in value)
            or port == 0
        ):
            raise ValueError("Embedding URL must be an HTTP(S) origin without credentials or path")
        return value.rstrip("/")


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OllamaEmbeddingProvider:
    def __init__(self, *, config: OllamaEmbeddingConfig):
        self._config = config
        # Local/native endpoint: do not forward source text through environment proxies.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def embed(self, *, request: TextEmbeddingRequest) -> EmbeddingBatch:
        payload = json.dumps(
            {
                "model": self._config.model,
                "input": list(request.texts),
                "truncate": False,
            }
        ).encode("utf-8")
        wire_request = Request(
            self._config.base_url + "/api/embed",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(wire_request, timeout=self._config.timeout_seconds) as response:
                raw = response.read(self._config.max_response_bytes + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            raise EmbeddingProviderError(
                f"Ollama embedding request failed (HTTP {status})"
            ) from None
        except (URLError, OSError, HTTPException):
            raise EmbeddingProviderError("Ollama embedding transport failed") from None
        if len(raw) > self._config.max_response_bytes:
            raise EmbeddingProviderError("Ollama embedding response exceeded admission limit")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("model") != self._config.model:
                raise ValueError("Unexpected provider model")
            result = EmbeddingBatch.model_validate(
                {
                    "model_id": self._config.model,
                    "model_revision": self._config.model_revision,
                    "dimensions": self._config.dimensions,
                    "vectors": data.get("embeddings"),
                }
            )
            return result.validate_for(request)
        except (ValueError, TypeError, RecursionError):
            raise EmbeddingProviderError("Ollama returned an invalid embedding response") from None
