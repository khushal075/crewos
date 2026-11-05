"""Validate server-reported model package identity; not atomic inference attestation."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

OllamaModelDigest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ModelRevisionVerificationError(ValueError):
    """Missing, ambiguous or mismatched model metadata; no provider body exposed."""


class _ModelEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(strict=True, min_length=1, max_length=256)
    model: str | None = Field(default=None, strict=True, min_length=1, max_length=256)
    digest: OllamaModelDigest


class _ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    models: list[_ModelEntry] = Field(max_length=10000)


def verify_ollama_model_revision(*, metadata: object, model: str, expected_digest: str) -> str:
    """Require one exact installed tag with the expected SHA-256 package digest.

    No latest-tag inference, alias guessing, short digest matching or automatic
    revision changes. Metadata comes from the configured trusted server; a match
    is not cryptographic proof of which artifact handled an inference request.
    """
    try:
        catalog = _ModelCatalog.model_validate(metadata)
        # Validate expected input too, including callers outside adapter composition.
        expected = _ModelEntry(name=model, digest=expected_digest)
    except ValidationError:
        raise ModelRevisionVerificationError("Invalid Ollama model revision metadata") from None
    matches = [entry for entry in catalog.models if entry.name == model]
    if len(matches) != 1:
        raise ModelRevisionVerificationError("Expected one exact installed embedding model tag")
    selected = matches[0]
    if selected.model is not None and selected.model != model:
        raise ModelRevisionVerificationError("Ollama model aliases disagree")
    if selected.digest != expected.digest:
        raise ModelRevisionVerificationError(
            "Installed embedding model revision differs from configuration"
        )
    return selected.digest
