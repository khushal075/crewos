from pydantic import BaseModel, Field

from typing import Dict, Any, Optional

class CrewRunRequest(BaseModel):
    agent_type: str = Field(default="research", description="Type of agent to run")
    input: Dict[str, Any] = Field(..., description="Input payload for the agent")


class CrewRunResponse(BaseModel):
    task_id: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[Any] = None