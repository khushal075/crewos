"""Provider-independent model proposals; validation does not authorize execution."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

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


ModelDecision = Annotated[
    ToolCallDecision | FinalAnswerDecision,
    Field(discriminator="type"),
]
