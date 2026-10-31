"""Provider-neutral inputs for requesting one agent decision."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from crewos.application.dtos.model_decision import NonBlankText


class DecisionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(strict=True, min_length=1)


class ToolSchema(BaseModel):
    """Model-visible metadata only; never contains an executable handler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonBlankText
    description: NonBlankText
    parameters: dict[str, JsonValue] = Field(strict=True)
