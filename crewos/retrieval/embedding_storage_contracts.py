"""Internal embedding persistence contracts; these are not public request schemas."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crewos.retrieval.embedding_contracts import ModelLabel
from crewos.retrieval.search_contracts import RetrievedPassage

Coordinate = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class EmbeddingSpace(BaseModel):
    """Configuration identity, not proof of actual provider behavior or compatibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ModelLabel
    model_id: ModelLabel
    model_revision: ModelLabel
    dimensions: int = Field(strict=True, ge=1, le=16384)
    preprocessing_revision: ModelLabel
    document_prefix: str = Field(default="", strict=True, max_length=1024)
    query_prefix: str = Field(default="", strict=True, max_length=1024)
    normalization: Literal["provider", "l2"] = "provider"

    @property
    def key(self) -> str:
        """Stable configuration namespace; not a secret or an authorization token."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StoredChunkEmbedding(BaseModel):
    """Backend-assembled derived record with exact evidence text and source version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    space: EmbeddingSpace
    passage: RetrievedPassage
    vector: tuple[Coordinate, ...] = Field(min_length=1, max_length=16384)

    @model_validator(mode="after")
    def validate_vector(self) -> Self:
        if len(self.vector) != self.space.dimensions:
            raise ValueError("Vector length must match the embedding space dimensions")
        if not any(value != 0 for value in self.vector):
            raise ValueError("Stored vectors must be nonzero for cosine retrieval")
        return self


class ChunkEmbeddingWriteBatch(BaseModel):
    """One bounded atomic write unit, not necessarily a complete document index."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    records: tuple[StoredChunkEmbedding, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_scope_and_identity(self) -> Self:
        if len({record.tenant_id for record in self.records}) != 1:
            raise ValueError("One write batch must belong to exactly one tenant")
        keys = [
            (
                record.space.key,
                record.passage.document_id,
                record.passage.document_version,
                record.passage.chunk.index,
            )
            for record in self.records
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("A write batch must have distinct source/configuration identities")
        if sum(len(record.passage.chunk.text) for record in self.records) > 100_000:
            raise ValueError("Write batch exceeds 100000 source text characters")
        return self
