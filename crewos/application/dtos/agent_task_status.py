"""Input and response contracts for task lookup; no execution logic."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class GetAgentTaskStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_task_id: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]


class TaskLookupOutcome(StrEnum):
    """Whether an accessible task record was found, not its execution state."""

    FOUND = "found"
    UNAVAILABLE = "unavailable"


class AgentTaskStatus(StrEnum):
    """Application task state; Celery state mapping is not implemented yet."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentTaskFound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[TaskLookupOutcome.FOUND]
    agent_task_id: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]
    status: AgentTaskStatus


class AgentTaskUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[TaskLookupOutcome.UNAVAILABLE]
    agent_task_id: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]


GetAgentTaskStatusResult = Annotated[
    AgentTaskFound | AgentTaskUnavailable,
    Field(discriminator="outcome"),
]
