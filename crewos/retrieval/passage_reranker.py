"""Provider-independent reranking inputs and score shape; no authority claims."""

from typing import Annotated, Protocol, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult, RetrievedPassage


class PassageRerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request: RetrievalRequest
    candidates: tuple[RetrievedPassage, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def admit(self) -> Self:
        RetrievalResult(strategy="candidate_validation", passages=self.candidates)
        if len(self.model_dump_json().encode()) > 128_000:
            raise ValueError("Reranking input exceeds byte admission")
        return self


class PassageRelevanceScores(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scores: tuple[Annotated[float, Field(strict=True, ge=0, le=4, allow_inf_nan=False)], ...] = (
        Field(max_length=20)
    )


class PassageScoringInterface(Protocol):
    def score(self, *, request: PassageRerankRequest) -> PassageRelevanceScores:
        """One score per candidate in input order; scores are not probabilities."""
        ...


class PassageRerankerInterface(Protocol):
    def rerank(self, *, request: PassageRerankRequest) -> RetrievalResult:
        """Select/reorder only supplied exact evidence; no new passages or authority."""
        ...
