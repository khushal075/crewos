from typing import Any, List
from dataclasses import dataclass, field
from crewos.domain.entities.agent import Agent

@dataclass
class Task:
    name: str
    description: str
    agent: Agent
    expected_output: str
    context: List[Any] = field(default_factory=list)    # task dependencies
    result: str = None
    #status: str = "pending"

    # def mark_completed(self):
    #     self.status = "completed"
