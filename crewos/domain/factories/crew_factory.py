# crewos/factories/crew_factory.py

from crewos.domain.entities.crew import Crew
from crewos.domain.factories.agent_factory import LLMAgentFactory
from crewos.domain.factories.task_factory import TaskFactory
from crewos.application.interfaces.llm_provider import LLMProviderInterface


class CrewFactory:
    """
    Factory to create a Crew with agents and tasks.
    Now accepts an LLM provider to inject LLMs into agents.
    """

    @staticmethod
    def create(payload: dict, llm_provider: LLMProviderInterface) -> Crew:
        # Get LLM instance from provider (infrastructure/third-party)
        llm = llm_provider.get()

        # Inject LLM enabled agents
        research_agent = LLMAgentFactory.create('research', llm)
        processing_agent = LLMAgentFactory.create('processing', llm)

        # Create tasks for agents
        analysis_task = TaskFactory.analysis_task(payload, research_agent)
        processing_task = TaskFactory.processing_task(payload, processing_agent)
        processing_task.context = [analysis_task]

        return Crew(
            agents=[research_agent, processing_agent],
            tasks=[analysis_task, processing_task],
            verbose=True
        )