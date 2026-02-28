# crewos/factories/llm_agent_factory.py

from crewos.domain.entities.agent import Agent as LLMEnabledAgent
from typing import Any


class LLMAgentFactory:
    """
    Factory to create domain Agent entities pre-configured with an LLM.
    This factory is now pure and does NOT instantiate any third-party services.
    The LLM must be provided externally (injected).
    """

    @staticmethod
    def research_agent(llm: Any) -> LLMEnabledAgent:
        """
        Returns a Research Specialist Agent using the provided LLM.
        """
        return LLMEnabledAgent(
            role="Research Specialist",
            goal="Analyze problems deeply and extract insights",
            backstory="Expert researcher with strong analytical skills",
            llm=llm,
            verbose=True,
            allow_delegation=False,
            tools=[]
        )

    @staticmethod
    def processing_agent(llm: Any) -> LLMEnabledAgent:
        """
        Returns a Processing Specialist Agent using the provided LLM.
        """
        return LLMEnabledAgent(
            role="Processing Specialist",
            goal="Convert research into structured actionable output",
            backstory="Expert in summarization",
            llm=llm,
            verbose=True,
            allow_delegation=False,
            tools=[]
        )

    @staticmethod
    def create(agent_type: str, llm: Any) -> LLMEnabledAgent:
        """
        Returns a domain Agent instance based on type.
        The LLM must be provided externally (injected).
        """
        if agent_type == "research":
            return LLMAgentFactory.research_agent(llm)
        elif agent_type == "processing":
            return LLMAgentFactory.processing_agent(llm)
        else:
            raise ValueError(f"Unsupported agent type: {agent_type}")