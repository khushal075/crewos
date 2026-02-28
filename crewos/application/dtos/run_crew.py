from dataclasses import dataclass

from typing import Dict

@dataclass
class RunCrewRequest:
    tenant_id: str
    agent_type: str
    message: str


@dataclass
class RunCrewResponse:
    tenant_id: str
    agent_type: str
    input: str
    output: Dict
    status: str
