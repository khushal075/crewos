"""Ingestion-owned document input; content remains untrusted."""

from typing import Annotated, Self
from datetime import datetime, timezone

from crewos.chunking import ChunkingResult, TextChunk  # Compatibility re-export.

from pydantic import (
    AwareDatetime,
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


DocumentTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
]
DocumentSourceReference = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048)
]


class DocumentIngestionRequest(BaseModel):
    """In-process source submission; tenant, IDs, and permissions are not inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes = Field(strict=True, min_length=1, max_length=4_000_000)
    title: DocumentTitle
    source_ref: DocumentSourceReference | None = None


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: DocumentTitle
    text: str = Field(strict=True, min_length=1, max_length=1_000_000)
    source_ref: DocumentSourceReference | None = None

    @field_validator("text")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Document text must contain non-whitespace characters")
        return value


class StoredDocumentVersion(BaseModel):
    """Backend-assembled immutable snapshot, not an upload/request schema.

    Validation proves structural consistency, not tenant authorization or truth
    of document content. IDs and tenant scope must come from trusted code.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID4
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    version: int = Field(strict=True, ge=1)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    document: DocumentInput
    chunking: ChunkingResult

    @model_validator(mode="after")
    def validate_saved_chunks(self) -> Self:
        if not self.chunking.chunks:
            raise ValueError("Stored document must contain chunks")
        text = self.document.text
        previous_end = 0
        for index, chunk in enumerate(self.chunking.chunks):
            if (
                chunk.index != index
                or (index == 0 and chunk.start_char != 0)
                or chunk.start_char > previous_end
                or chunk.end_char <= previous_end
                or chunk.text != text[chunk.start_char : chunk.end_char]
            ):
                raise ValueError("Stored chunks must match and cover the document")
            previous_end = chunk.end_char
        if previous_end != len(text):
            raise ValueError("Stored chunks must cover the complete document")
        return self
