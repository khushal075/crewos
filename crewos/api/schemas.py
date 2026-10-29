from pydantic import BaseModel, ConfigDict, Field, field_validator

from typing import Any, Optional


class CrewInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str = Field(strict=True)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value


class CrewRunRequest(BaseModel):
    agent_type: str = Field(default="research", description="Type of agent to run")
    input: CrewInput = Field(..., description="Input payload for the agent")


class CrewRunResponse(BaseModel):
    task_id: str
    status: str


class CrewSyncRunResponse(CrewRunResponse):
    output: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[Any] = None
