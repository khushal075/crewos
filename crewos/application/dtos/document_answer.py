"""Document-answer shapes; schema validity does not establish evidence sufficiency."""

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from crewos.application.dtos.source_reference import SourceReference


class DocumentAnswerOutcome(str, Enum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AnsweredDocumentAnswer(BaseModel):
    """Proposed answer with references; provenance/support must be checked separately."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal[DocumentAnswerOutcome.ANSWERED]
    answer: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=8192)
    ]
    citations: tuple[SourceReference, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct_citations(self) -> Self:
        if len(set(self.citations)) != len(self.citations):
            raise ValueError("Citations must be distinct source references")
        return self


class InsufficientEvidenceAnswer(BaseModel):
    """Explain why the available evidence cannot support an answer.

    This is an answer outcome, not a replacement for an authorization, storage,
    timeout, or capacity exception. Shape validation cannot judge the explanation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal[DocumentAnswerOutcome.INSUFFICIENT_EVIDENCE]
    explanation: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048)
    ]


DocumentAnswer = Annotated[
    AnsweredDocumentAnswer | InsufficientEvidenceAnswer, Field(discriminator="outcome")
]
