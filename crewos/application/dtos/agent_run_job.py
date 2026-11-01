"""Backend-created queue payload; structural validation does not prove trust."""

from pydantic import UUID4, BaseModel, ConfigDict, Field

from crewos.application.dtos.model_decision import NonBlankText


class AgentRunJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: UUID4
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    message: NonBlankText
    max_decisions: int = Field(default=5, strict=True, ge=1)
