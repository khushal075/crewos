from dataclasses import dataclass
from typing import List, Dict, Any
from crewos.domain.entities.agent import Agent
from crewos.domain.entities.task import Task

@dataclass
class Crew:
    agents: List[Agent]
    tasks: List[Task]
    verbose: bool = False

    def kickoff(self) -> List[Dict[str, Any]]:
        results = []
        context_output = None

        for task in self.tasks:
            agent = task.agent

            payload = {
                "task_description": task.description,
                "expected_output": task.expected_output
            }

            # Use explicit task.context if set, otherwise use previous task output
            if task.context:
                payload["context"] = "\n".join([c.result for c in task.context if c.result])
            elif context_output:
                payload["context"] = context_output

            if self.verbose:
                print(f"[Crew] Agent '{agent.role}' executing task '{task.name}'")
                print(f"My Payload: {payload}")

            agent.set_payload(payload)
            agent_result = agent.kickoff()
            context_output = agent_result.get("response")

            # Store result on task so downstream tasks can read it via context
            task.result = context_output

            results.append({
                "agent": agent.role,
                "task": task.description,
                "result": context_output
            })

        return results
