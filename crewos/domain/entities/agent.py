from dataclasses import dataclass, field
from typing import Any, Dict, List
from langchain_core.messages import HumanMessage


@dataclass
class Agent:
    role: str
    goal: str
    backstory: str = ""
    verbose: bool = False
    tools: List[str] = field(default_factory=list)
    allow_delegation: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)
    llm: Any = None  # Optional, can be injected later

    def set_payload(self, payload: Dict[str, Any]):
        self.payload = payload

    def kickoff(self) -> dict:
        response = f"Processed by {self.role}"

        if self.llm:
            task_description = self.payload.get("task_description", "")
            context = self.payload.get("context", "")
            message = task_description
            if context:
                message += f"\n\nPrevious task output:\n{context}"

            batch_messages = [[HumanMessage(content=message)]]

            result = self.llm.generate(batch_messages)
            response = result.generations[0][0].text

        return {
            "role": self.role,
            "goal": self.goal,
            "backstory": self.backstory,
            "payload": self.payload,
            "response": response,
        }
