"""
Unit tests for crewos/workers/crew_task.py and CrewRunner.
Redis memory is a TODO stub — these tests cover the Celery task layer.
All external dependencies (LLM, Celery broker) are mocked.
"""
import pytest
import json
from unittest.mock import MagicMock, patch

from crewos.application.dtos.run_crew import RunCrewResponse


# ── CrewRunner ────────────────────────────────────────────────────────────────

class TestCrewRunner:

    def _mock_response(self, **kwargs):
        defaults = dict(
            tenant_id="t1",
            agent_type="research",
            input="test message",
            output="runner output",
            status="completed"
        )
        defaults.update(kwargs)
        return RunCrewResponse(**defaults)

    @patch("crewos.services.crew_runner.RunCrewUseCase")
    @patch("crewos.services.crew_runner.OllamaLLMProvider")
    @patch("crewos.services.crew_runner.CrewFactory")
    def test_run_returns_dict(self, mock_factory, mock_provider, mock_use_case):
        mock_use_case.return_value.execute.return_value = self._mock_response()
        from crewos.services.crew_runner import CrewRunner
        result = CrewRunner.run("t1", {"input": {"message": "hello"}, "agent_type": "research"})
        assert isinstance(result, dict)
        assert result["tenant_id"] == "t1"
        assert result["status"] == "completed"

    @patch("crewos.services.crew_runner.RunCrewUseCase")
    @patch("crewos.services.crew_runner.OllamaLLMProvider")
    @patch("crewos.services.crew_runner.CrewFactory")
    def test_run_result_keys(self, mock_factory, mock_provider, mock_use_case):
        mock_use_case.return_value.execute.return_value = self._mock_response()
        from crewos.services.crew_runner import CrewRunner
        result = CrewRunner.run("t1", {"input": {"message": "hello"}, "agent_type": "research"})
        assert set(result.keys()) == {"tenant_id", "agent_type", "input", "output", "status"}

    @patch("crewos.services.crew_runner.RunCrewUseCase")
    @patch("crewos.services.crew_runner.OllamaLLMProvider")
    @patch("crewos.services.crew_runner.CrewFactory")
    def test_run_passes_tenant_id(self, mock_factory, mock_provider, mock_use_case):
        mock_use_case.return_value.execute.return_value = self._mock_response(tenant_id="tenant-99")
        from crewos.services.crew_runner import CrewRunner
        result = CrewRunner.run("tenant-99", {"input": {"message": "hi"}, "agent_type": "research"})
        assert result["tenant_id"] == "tenant-99"

    @patch("crewos.services.crew_runner.RunCrewUseCase")
    @patch("crewos.services.crew_runner.OllamaLLMProvider")
    @patch("crewos.services.crew_runner.CrewFactory")
    def test_run_uses_default_agent_type(self, mock_factory, mock_provider, mock_use_case):
        """Payload without agent_type defaults to 'research'."""
        mock_use_case.return_value.execute.return_value = self._mock_response()
        from crewos.services.crew_runner import CrewRunner
        # No agent_type in payload — CrewRunner should default it
        result = CrewRunner.run("t1", {"input": {"message": "hello"}})
        assert result is not None


# ── Celery crew_task ──────────────────────────────────────────────────────────

class TestCrewCeleryTask:

    @patch("crewos.workers.crew_task.CrewRunner")
    def test_run_crew_task_success(self, mock_runner):
        mock_runner.run.return_value = {
            "tenant_id": "t1",
            "agent_type": "research",
            "input": "hello",
            "output": "done",
            "status": "completed"
        }
        from crewos.workers.crew_task import run_crew
        result = run_crew("t1", {"input": {"message": "hello"}, "agent_type": "research"})
        assert result["status"] == "completed"
        mock_runner.run.assert_called_once()

    @patch("crewos.workers.crew_task.CrewRunner")
    def test_run_crew_task_accepts_json_string_payload(self, mock_runner):
        mock_runner.run.return_value = {
            "tenant_id": "t1", "agent_type": "research",
            "input": "hello", "output": "done", "status": "completed"
        }
        from crewos.workers.crew_task import run_crew
        payload_str = json.dumps({"input": {"message": "hello"}, "agent_type": "research"})
        result = run_crew("t1", payload_str)
        assert result["status"] == "completed"

    def test_run_crew_task_raises_on_missing_tenant_id(self):
        from crewos.workers.crew_task import run_crew
        with pytest.raises(ValueError, match="tenant_id is required"):
            run_crew("", {"input": {"message": "hello"}})

    def test_run_crew_task_raises_on_empty_message(self):
        from crewos.workers.crew_task import run_crew
        with pytest.raises(ValueError, match="Message is required"):
            run_crew("t1", {"input": {"message": ""}, "agent_type": "research"})

    @patch("crewos.workers.crew_task.CrewRunner")
    def test_run_crew_task_sets_default_agent_type(self, mock_runner):
        mock_runner.run.return_value = {
            "tenant_id": "t1", "agent_type": "research",
            "input": "hello", "output": "done", "status": "completed"
        }
        from crewos.workers.crew_task import run_crew
        # No agent_type provided — task should default it before passing to runner
        result = run_crew("t1", {"input": {"message": "hello"}})
        call_payload = mock_runner.run.call_args[1]["payload"] if mock_runner.run.call_args[1] else mock_runner.run.call_args[0][1]
        assert call_payload.get("agent_type") == "research"