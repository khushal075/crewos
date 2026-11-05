"""Provider-independent text embedding shapes; validation does not authorize access."""

from math import isfinite
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

EmbeddingText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=8192)]
ModelLabel = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
]


class TextEmbeddingRequest(BaseModel):
    """Ordered text batch. Preserve exact input text rather than normalizing it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    texts: tuple[EmbeddingText, ...] = Field(min_length=1, max_length=128)

    @field_validator("texts")
    @classmethod
    def admit_text(cls, texts: tuple[str, ...]) -> tuple[str, ...]:
        if any(not text.strip() for text in texts):
            raise ValueError("Embedding inputs must contain non-whitespace text")
        if sum(len(text) for text in texts) > 65536:
            raise ValueError("Embedding batch exceeds 65536 characters")
        return texts


class EmbeddingBatch(BaseModel):
    """Ordered vectors from one configured embedding space, not confidence scores."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: ModelLabel
    model_revision: ModelLabel
    dimensions: int = Field(strict=True, ge=1, le=16384)
    vectors: tuple[tuple[float, ...], ...] = Field(min_length=1, max_length=128)

    @field_validator("vectors", mode="before")
    @classmethod
    def admit_numeric_vectors(cls, vectors):
        if not isinstance(vectors, (list, tuple)):
            raise ValueError("Vectors must be a list or tuple")
        if not 1 <= len(vectors) <= 128:
            raise ValueError("Embedding batches require 1 to 128 vectors")
        for vector in vectors:
            if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 16384:
                raise ValueError("Each vector requires 1 to 16384 coordinates")
            for value in vector:
                if type(value) not in (int, float):
                    raise ValueError("Coordinates must be numbers, excluding booleans")
                try:
                    finite = isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError("Coordinates must be finite")
        return vectors

    @model_validator(mode="after")
    def consistent_dimensions(self) -> Self:
        if any(len(vector) != self.dimensions for vector in self.vectors):
            raise ValueError("Every vector must match the declared dimensions")
        return self

    def validate_for(self, request: TextEmbeddingRequest) -> Self:
        """Check output count against the actual request; cannot prove ordering."""
        if len(self.vectors) != len(request.texts):
            raise ValueError("Expected exactly one vector for each input text")
        return self
