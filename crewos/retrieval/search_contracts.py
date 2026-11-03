"""Query and evidence contracts; shape validation does not authorize retrieval."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, UUID4, model_validator

from crewos.chunking import TextChunk


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048)
    ]
    top_k: int = Field(default=5, strict=True, ge=1, le=20)


class RetrievedPassage(BaseModel):
    """One source passage; identity is meaningful only within trusted tenant scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID4
    document_version: int = Field(strict=True, ge=1)
    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
    ]
    source_ref: (
        Annotated[
            str,
            StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048),
        ]
        | None
    ) = None
    chunk: TextChunk


class RetrievalResult(BaseModel):
    """Passages ordered by the strategy; no hits is distinct from execution failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    passages: tuple[RetrievedPassage, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def unique_source_passages(self) -> Self:
        identities = [
            (item.document_id, item.document_version, item.chunk.index) for item in self.passages
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("Retrieved passages must have distinct source identities")
        return self
