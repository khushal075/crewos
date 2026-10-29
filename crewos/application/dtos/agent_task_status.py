"""Input contract for querying an agent task; no lookup or execution logic."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class GetAgentTaskStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_task_id: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]
