"""Backend-created queue payload; structural validation does not prove trust."""

from pydantic import UUID4, BaseModel, ConfigDict, Field, model_serializer

from crewos.application.dtos.model_decision import NonBlankText, AgentAnswerMode


class AgentRunJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: UUID4
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    message: NonBlankText = Field(max_length=32768)
    max_decisions: int = Field(default=5, strict=True, ge=1)

    answer_mode: AgentAnswerMode = "general"

    @model_serializer(mode="wrap")
    def serialize_job(self, handler):
        data = handler(self)
        # Preserve the exact stored payload of pre-mode general jobs on replay.
        if self.answer_mode == "general":
            data.pop("answer_mode", None)
        return data
