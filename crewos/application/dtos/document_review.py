"""Bounded model-review contracts. A verdict is a fallible assessment, not proof."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from crewos.application.dtos.document_answer import DocumentAnswer
from crewos.application.dtos.source_reference import SourceReference
from crewos.retrieval import RetrievedPassage

ReviewText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048)
]


class EvidenceExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: SourceReference
    quote: ReviewText


class ClaimReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim: ReviewText = Field(
        description="An exact contiguous span copied from the proposed answer, without paraphrasing."
    )
    verdict: Literal["supported", "unsupported", "contradicted", "uncertain"]
    excerpts: tuple[EvidenceExcerpt, ...] = Field(default=(), max_length=10)


class DocumentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    question: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=32768)]
    answer: DocumentAnswer
    passages: tuple[RetrievedPassage, ...] = Field(default=(), max_length=100)


class DocumentReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    verdict: Literal[
        "supported",
        "unsupported",
        "conflicting",
        "uncertain",
        "appropriate_abstention",
        "unnecessary_abstention",
    ] = Field(
        description="Verdict on the proposed answer/explanation, not merely a label for the passages."
    )
    reason: ReviewText
    evidence_answerability: Literal["answerable", "insufficient", "conflicting", "uncertain"]
    covers_all_claims: bool = Field(strict=True)
    addresses_question: bool = Field(
        strict=True,
        description=(
            "Whether the answer or abstention explanation responds to the original question. "
            "An explanation of missing evidence can address the question without answering it."
        ),
    )
    evidence_conflict: bool = Field(strict=True)
    claims: tuple[ClaimReview, ...] = Field(default=(), max_length=20)
