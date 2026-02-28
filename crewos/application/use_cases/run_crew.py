# crewos/application/use_cases/run_crew.py

from crewos.application.dtos.run_crew import RunCrewRequest, RunCrewResponse
from crewos.application.interfaces.llm_provider import LLMProviderInterface
from crewos.domain.factories.crew_factory import CrewFactory
from crewos.infrastructure.logging import get_logger

logger = get_logger(__name__)


class RunCrewUseCase:
    """
    Use case to run a crew with LLM-enabled agents.
    Now properly injects LLM provider into CrewFactory.
    """

    def __init__(self, llm_provider: LLMProviderInterface, crew_factory: CrewFactory):
        self.llm_provider = llm_provider
        self.crew_factory = crew_factory

    def execute(self, request: RunCrewRequest) -> RunCrewResponse:
        logger.info({
            "event": "run_crew_started",
            "tenant_id": request.tenant_id,
            "agent_type": request.agent_type,
        })

        if not request.message:
            logger.warning({
                "event": "missing_message",
                "tenant_id": request.tenant_id
            })
            raise ValueError("Message is required")

        # 1️⃣ Log start of use case
        logger.info({
            "event": "run_crew_started",
            "tenant_id": request.tenant_id,
            "agent_type": request.agent_type,
            "has_message": bool(request.message)
        })

        # 2️⃣ Delegate Crew creation with injected LLM
        crew = self.crew_factory.create(
            payload={"input": {"message": request.message},
                     "agent_type": request.agent_type},
            llm_provider=self.llm_provider
        )

        # 3️⃣ Log before kickoff
        logger.info({
            "event": "crew_kickoff_started",
            "tenant_id": request.tenant_id,
            "agent_type": request.agent_type,
        })

        result = crew.kickoff()

        # 4️⃣ Log completion
        logger.info({
            "event": "run_crew_completed",
            "tenant_id": request.tenant_id,
            "agent_type": request.agent_type,
            "output_summary": str(result)[:200]  # avoid huge logs
        })

        final_result = result[-1]["result"] if isinstance(result, list) else result
        #final_result = result
        return RunCrewResponse(
            tenant_id=request.tenant_id,
            agent_type=request.agent_type,
            input=request.message,
            output=final_result,
            status="completed"
        )