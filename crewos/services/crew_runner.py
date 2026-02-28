# crewos/services/crew_runner.py

from typing import Dict, Any
from crewos.application.use_cases.run_crew import RunCrewUseCase
from crewos.application.dtos.run_crew import RunCrewRequest
from crewos.domain.factories.crew_factory import CrewFactory
from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider


class CrewRunner:
    """
    Entrypoint to run a Crew.
    Handles wiring of LLM provider into the application use case.
    """

    @staticmethod
    def run(tenant_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 1️⃣ Construct the Request DTO
        request = RunCrewRequest(
            tenant_id=tenant_id,
            agent_type=payload.get("agent_type", "research"),
            message=payload["input"].get("message", "").strip()
        )

        # 2️⃣ Instantiate third-party LLM provider (adapter)
        llm_provider = OllamaLLMProvider()  # instance

        # 3️⃣ Instantiate CrewFactory (stateless, but we inject instance for typing)
        crew_factory = CrewFactory()  # ✅ instance, not class

        # 4️⃣ Wire CrewFactory and LLM provider into the use case
        use_case = RunCrewUseCase(
            llm_provider=llm_provider,
            crew_factory=crew_factory
        )

        # 5️⃣ Execute the use case
        response = use_case.execute(request)

        # 6️⃣ Return as dict for API / worker compatibility
        return {
            "tenant_id": response.tenant_id,
            "agent_type": response.agent_type,
            "input": response.input,
            "output": response.output,
            "status": response.status
        }