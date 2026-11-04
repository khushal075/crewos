"""Public agent submission data; identity is assigned by authenticated backend code."""

from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field

from crewos.application.dtos.model_decision import NonBlankText, AgentAnswerMode


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: NonBlankText = Field(max_length=32768)
    max_decisions: int = Field(default=5, strict=True, ge=1)
    answer_mode: AgentAnswerMode = "general"


class AgentRunAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: UUID4
    status: Literal[
        "queued", "running", "succeeded", "failed", "cancelled", "exhausted", "needs_review"
    ] = "queued"
    replayed: bool = False
