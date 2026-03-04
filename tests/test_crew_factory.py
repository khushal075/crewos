"""
Unit tests for domain entities, factories, and the RunCrewUseCase.
All LLM calls are mocked — no Ollama or OpenAI required.
"""
import pytest
from unittest.mock import MagicMock, patch
from typing import Any

from crewos.domain.entities.agent import Agent
from crewos.domain.entities.task import Task
from crewos.domain.entities.crew import Crew
from crewos.domain.factories.agent_factory import LLMAgentFactory
from crewos.domain.factories.task_factory import TaskFactory
from crewos.domain.factories.crew_factory import CrewFactory
from crewos.application.dtos.run_crew import RunCrewRequest, RunCrewResponse
from crewos.application.use_cases.run_crew import RunCrewUseCase


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mock_llm(response_text: str = "mocked response") -> Any:
    """Returns a mock LLM that mimics langchain's .generate() interface."""
    generation = MagicMock()
    generation.text = response_text
    llm = MagicMock()
    llm.generate.return_value = MagicMock(generations=[[generation]])
    return llm


def make_mock_llm_provider(response_text: str = "mocked response") -> Any:
    provider = MagicMock()
    provider.get.return_value = make_mock_llm(response_text)
    return provider


# ── Agent entity ──────────────────────────────────────────────────────────────

class TestAgent:

    def test_agent_creation(self):
        agent = Agent(role="Researcher", goal="Find facts", backstory="Expert")
        assert agent.role == "Researcher"
        assert agent.goal == "Find facts"
        assert agent.llm is None
        assert agent.tools == []

    def test_set_payload(self):
        agent = Agent(role="Researcher", goal="Find facts")
        agent.set_payload({"task_description": "analyze this"})
        assert agent.payload["task_description"] == "analyze this"

    def test_kickoff_without_llm_returns_fallback(self):
        agent = Agent(role="Tester", goal="Test things")
        agent.set_payload({"task_description": "do something"})
        result = agent.kickoff()
        assert result["role"] == "Tester"
        assert result["response"] == "Processed by Tester"

    def test_kickoff_with_llm_calls_generate(self):
        llm = make_mock_llm("LLM output")
        agent = Agent(role="Researcher", goal="Research", llm=llm)
        agent.set_payload({"task_description": "analyze AI trends"})
        result = agent.kickoff()
        assert result["response"] == "LLM output"
        llm.generate.assert_called_once()

    def test_kickoff_returns_full_dict(self):
        agent = Agent(role="Analyst", goal="Analyze", backstory="Senior analyst")
        agent.set_payload({"task_description": "summary"})
        result = agent.kickoff()
        assert "role" in result
        assert "goal" in result
        assert "backstory" in result
        assert "payload" in result
        assert "response" in result


# ── Task entity ───────────────────────────────────────────────────────────────

class TestTask:

    def test_task_creation(self):
        agent = Agent(role="R", goal="G")
        task = Task(
            name="Analysis",
            description="Analyze input",
            agent=agent,
            expected_output="Structured output"
        )
        assert task.name == "Analysis"
        assert task.result is None
        assert task.context == []

    def test_task_result_can_be_set(self):
        agent = Agent(role="R", goal="G")
        task = Task(name="T", description="D", agent=agent, expected_output="E")
        task.result = "final output"
        assert task.result == "final output"


# ── Crew entity ───────────────────────────────────────────────────────────────

class TestCrew:

    def test_crew_kickoff_runs_all_tasks(self):
        llm = make_mock_llm("result")
        agent1 = Agent(role="R1", goal="G1", llm=llm)
        agent2 = Agent(role="R2", goal="G2", llm=llm)
        task1 = Task(name="T1", description="D1", agent=agent1, expected_output="E1")
        task2 = Task(name="T2", description="D2", agent=agent2, expected_output="E2")
        crew = Crew(agents=[agent1, agent2], tasks=[task1, task2])
        results = crew.kickoff()
        assert len(results) == 2
        assert results[0]["agent"] == "R1"
        assert results[1]["agent"] == "R2"

    def test_crew_passes_context_between_tasks(self):
        """
        When task2 has no explicit context, it receives task1's LLM output
        as implicit context via the context_output carry-forward in Crew.kickoff().
        """
        llm1 = make_mock_llm("first output")
        llm2 = make_mock_llm("second output")
        agent1 = Agent(role="R1", goal="G1", llm=llm1)
        agent2 = Agent(role="R2", goal="G2", llm=llm2)
        task1 = Task(name="T1", description="D1", agent=agent1, expected_output="E1")
        task2 = Task(name="T2", description="D2", agent=agent2, expected_output="E2")
        crew = Crew(agents=[agent1, agent2], tasks=[task1, task2])
        crew.kickoff()
        assert agent2.payload.get("context") == "first output"

    def test_crew_explicit_task_context_uses_llm_output(self):
        """
        When task2 has explicit context=[task1], it reads task1.result which is
        set during kickoff — i.e. the LLM output from task1's execution, not
        any pre-set value (kickoff overwrites task.result on each run).
        """
        llm = make_mock_llm("task1 llm output")
        agent1 = Agent(role="R1", goal="G1", llm=llm)
        agent2 = Agent(role="R2", goal="G2", llm=llm)
        task1 = Task(name="T1", description="D1", agent=agent1, expected_output="E1")
        task2 = Task(
            name="T2", description="D2", agent=agent2,
            expected_output="E2", context=[task1]
        )
        crew = Crew(agents=[agent1, agent2], tasks=[task1, task2])
        crew.kickoff()
        # task1.result is set to the LLM output during kickoff
        # task2's context payload should contain that LLM output
        assert agent2.payload.get("context") == "task1 llm output"

    def test_crew_kickoff_stores_result_on_task(self):
        llm = make_mock_llm("stored result")
        agent = Agent(role="R", goal="G", llm=llm)
        task = Task(name="T", description="D", agent=agent, expected_output="E")
        crew = Crew(agents=[agent], tasks=[task])
        crew.kickoff()
        assert task.result == "stored result"

    def test_crew_kickoff_result_structure(self):
        llm = make_mock_llm("output")
        agent = Agent(role="R", goal="G", llm=llm)
        task = Task(name="T", description="D", agent=agent, expected_output="E")
        crew = Crew(agents=[agent], tasks=[task])
        results = crew.kickoff()
        assert "agent" in results[0]
        assert "task" in results[0]
        assert "result" in results[0]


# ── LLMAgentFactory ───────────────────────────────────────────────────────────

class TestLLMAgentFactory:

    def test_create_research_agent(self):
        llm = make_mock_llm()
        agent = LLMAgentFactory.create("research", llm)
        assert agent.role == "Research Specialist"
        assert agent.llm is llm
        assert agent.allow_delegation is False

    def test_create_processing_agent(self):
        llm = make_mock_llm()
        agent = LLMAgentFactory.create("processing", llm)
        assert agent.role == "Processing Specialist"
        assert agent.llm is llm

    def test_unsupported_agent_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported agent type"):
            LLMAgentFactory.create("unknown", MagicMock())

    def test_research_agent_shortcut(self):
        llm = make_mock_llm()
        agent = LLMAgentFactory.research_agent(llm)
        assert agent.role == "Research Specialist"

    def test_processing_agent_shortcut(self):
        llm = make_mock_llm()
        agent = LLMAgentFactory.processing_agent(llm)
        assert agent.role == "Processing Specialist"


# ── TaskFactory ───────────────────────────────────────────────────────────────

class TestTaskFactory:

    def test_analysis_task_creation(self):
        agent = Agent(role="R", goal="G")
        payload = {"input": {"message": "hello"}}
        task = TaskFactory.analysis_task(payload, agent)
        assert task.name == "Engineering Analysis"
        assert task.agent is agent

    def test_processing_task_creation(self):
        agent = Agent(role="P", goal="G")
        payload = {"input": {"message": "hello"}}
        task = TaskFactory.processing_task(payload, agent)
        assert task.name == "Engineering Processing"
        assert task.agent is agent

    def test_tasks_have_expected_output(self):
        agent = Agent(role="R", goal="G")
        payload = {}
        analysis = TaskFactory.analysis_task(payload, agent)
        processing = TaskFactory.processing_task(payload, agent)
        assert analysis.expected_output != ""
        assert processing.expected_output != ""

    def test_analysis_task_description_contains_payload(self):
        agent = Agent(role="R", goal="G")
        payload = {"input": {"message": "summarize quantum computing"}}
        task = TaskFactory.analysis_task(payload, agent)
        assert str(payload) in task.description


# ── CrewFactory ───────────────────────────────────────────────────────────────

class TestCrewFactory:

    def test_create_returns_crew(self):
        provider = make_mock_llm_provider()
        payload = {"input": {"message": "test"}, "agent_type": "research"}
        crew = CrewFactory.create(payload, provider)
        assert isinstance(crew, Crew)

    def test_crew_has_two_agents(self):
        provider = make_mock_llm_provider()
        payload = {"input": {"message": "test"}, "agent_type": "research"}
        crew = CrewFactory.create(payload, provider)
        assert len(crew.agents) == 2

    def test_crew_has_two_tasks(self):
        provider = make_mock_llm_provider()
        payload = {"input": {"message": "test"}, "agent_type": "research"}
        crew = CrewFactory.create(payload, provider)
        assert len(crew.tasks) == 2

    def test_processing_task_context_set_to_analysis_task(self):
        provider = make_mock_llm_provider()
        payload = {"input": {"message": "test"}, "agent_type": "research"}
        crew = CrewFactory.create(payload, provider)
        processing_task = crew.tasks[1]
        assert len(processing_task.context) == 1
        assert processing_task.context[0] is crew.tasks[0]

    def test_llm_provider_get_called(self):
        provider = make_mock_llm_provider()
        payload = {"input": {"message": "test"}, "agent_type": "research"}
        CrewFactory.create(payload, provider)
        provider.get.assert_called_once()


# ── RunCrewUseCase ────────────────────────────────────────────────────────────

class TestRunCrewUseCase:

    def _make_use_case(self, llm_response: str = "final answer"):
        provider = make_mock_llm_provider(llm_response)
        crew_factory = CrewFactory()
        return RunCrewUseCase(llm_provider=provider, crew_factory=crew_factory)

    def test_execute_returns_response(self):
        use_case = self._make_use_case()
        request = RunCrewRequest(
            tenant_id="t1", agent_type="research", message="What is AI?"
        )
        response = use_case.execute(request)
        assert isinstance(response, RunCrewResponse)
        assert response.tenant_id == "t1"
        assert response.status == "completed"

    def test_execute_returns_last_task_result(self):
        use_case = self._make_use_case("final answer")
        request = RunCrewRequest(
            tenant_id="t1", agent_type="research", message="Summarize this"
        )
        response = use_case.execute(request)
        assert response.output == "final answer"

    def test_empty_message_raises_value_error(self):
        use_case = self._make_use_case()
        request = RunCrewRequest(tenant_id="t1", agent_type="research", message="")
        with pytest.raises(ValueError, match="Message is required"):
            use_case.execute(request)

    def test_response_contains_input_message(self):
        use_case = self._make_use_case()
        request = RunCrewRequest(
            tenant_id="t1", agent_type="research", message="hello world"
        )
        response = use_case.execute(request)
        assert response.input == "hello world"

    def test_response_agent_type_preserved(self):
        use_case = self._make_use_case()
        request = RunCrewRequest(
            tenant_id="t1", agent_type="processing", message="process this"
        )
        response = use_case.execute(request)
        assert response.agent_type == "processing"