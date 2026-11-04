"""Exact passage identity; a reference alone proves neither access nor correctness."""

from pydantic import BaseModel, ConfigDict, Field, UUID4


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID4
    document_version: int = Field(strict=True, ge=1)
    chunk_index: int = Field(strict=True, ge=0)
