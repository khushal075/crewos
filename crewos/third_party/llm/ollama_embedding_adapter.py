"""Native Ollama embedding adapter, independent of chat and retrieval wiring."""

import json
import logging
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crewos.application.services.execution_control import get_execution_control
from crewos.retrieval.embedding_contracts import EmbeddingBatch, ModelLabel, TextEmbeddingRequest
from crewos.third_party.llm.ollama_model_revision import (
    ModelRevisionVerificationError,
    OllamaModelDigest,
    verify_ollama_model_revision,
)

logger = logging.getLogger(__name__)


class EmbeddingProviderError(RuntimeError):
    """Provider transport or response failure; message excludes submitted content."""


class OllamaEmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    model: ModelLabel
    model_revision: OllamaModelDigest
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

    revision_verification = "server_metadata_pre_and_post"

    def _request_json(self, *, path: str, payload: dict | None = None, max_bytes: int) -> object:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        wire_request = Request(
            self._config.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        control = get_execution_control()
        timeout = self._config.timeout_seconds
        if control is not None:
            timeout = min(timeout, control.tool_remaining())
        try:
            with self._opener.open(wire_request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            raise EmbeddingProviderError(
                f"Ollama embedding request failed (HTTP {status})"
            ) from None
        except (URLError, OSError, HTTPException):
            raise EmbeddingProviderError("Ollama embedding transport failed") from None
        if control is not None:
            control.tool_remaining()
        if len(raw) > max_bytes:
            raise EmbeddingProviderError("Ollama embedding response exceeded admission limit")
        try:
            return json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            raise EmbeddingProviderError("Ollama returned an invalid embedding response") from None

    def _verify_revision(self, *, stage: str) -> str:
        try:
            metadata = self._request_json(path="/api/tags", max_bytes=1_000_000)
            digest = verify_ollama_model_revision(
                metadata=metadata,
                model=self._config.model,
                expected_digest=self._config.model_revision,
            )
        except ModelRevisionVerificationError as error:
            logger.warning({"event": "embedding_revision_rejected", "stage": stage})
            raise EmbeddingProviderError(
                f"Ollama model revision verification failed: {error}"
            ) from None
        logger.info(
            {
                "event": "embedding_revision_checked",
                "stage": stage,
                "model_id": self._config.model,
                "revision": digest,
            }
        )
        return digest

    def embed(self, *, request: TextEmbeddingRequest) -> EmbeddingBatch:
        # No source text is sent until the expected package is observed.
        digest = self._verify_revision(stage="before")
        data = self._request_json(
            path="/api/embed",
            max_bytes=self._config.max_response_bytes,
            payload={"model": self._config.model, "input": list(request.texts), "truncate": False},
        )
        try:
            if not isinstance(data, dict) or data.get("model") != self._config.model:
                raise ValueError("Unexpected provider model")
            result = EmbeddingBatch.model_validate(
                {
                    "model_id": self._config.model,
                    "model_revision": digest,
                    "dimensions": self._config.dimensions,
                    "vectors": data.get("embeddings"),
                }
            ).validate_for(request)
        except (ValueError, TypeError, RecursionError):
            raise EmbeddingProviderError("Ollama returned an invalid embedding response") from None
        # Failure here discards vectors before callers can persist or rank them.
        self._verify_revision(stage="after")
        return result
