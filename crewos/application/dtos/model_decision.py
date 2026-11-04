"""Provider-independent model proposals; validation does not authorize execution."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from crewos.application.dtos.source_reference import SourceReference
from crewos.application.dtos.document_answer import DocumentAnswer

NonBlankText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class ToolCallDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_call"]
    name: NonBlankText
    arguments: dict[str, JsonValue] = Field(strict=True)


class FinalAnswerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["final_answer"]
    answer: NonBlankText
    citations: tuple[SourceReference, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def unique_citations(self):
        if len(set(self.citations)) != len(self.citations):
            raise ValueError("Citations must be distinct source references")
        return self


ModelDecision = Annotated[
    ToolCallDecision | FinalAnswerDecision,
    Field(discriminator="type"),
]


class DocumentFinalAnswerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["document_answer"]
    result: DocumentAnswer


DocumentModelDecision = Annotated[
    ToolCallDecision | DocumentFinalAnswerDecision, Field(discriminator="type")
]
AgentAnswer = FinalAnswerDecision | DocumentFinalAnswerDecision
AgentAnswerMode = Literal["general", "document"]
