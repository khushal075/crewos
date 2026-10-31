"""CLI exercises real assembly with synthetic credentials and model replies."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from crewos.core.agent_demo import main
from crewos.core.config import settings
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from tests.helpers import MemoryRedis

ARGS = ["--tenant-id", "tenant_a", "--message", "Check task-123"]
TOOL_CALL = (
    '{"type":"tool_call","name":"get_agent_task_status","arguments":{"agent_task_id":"task-123"}}'
)


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {"tenant_a": SecretStr("test-token")})
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"agent_task.read"})}
    )
    monkeypatch.setenv("CREWOS_TENANT_TOKEN", "test-token")
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    model = Mock()
    model.invoke.side_effect = [
        AIMessage(content=TOOL_CALL),
        AIMessage(content='{"type":"final_answer","answer":"The task is running."}'),
    ]
    with (
        patch("crewos.core.agent_demo.OllamaLLMProvider") as provider,
        patch("crewos.core.agent_demo.get_task_store", return_value=store),
        patch("crewos.infrastructure.task_status_reader.AsyncResult") as backend,
    ):
        provider.return_value.get.return_value = model
        backend.return_value = SimpleNamespace(state="STARTED")
        yield provider, model, backend


def test_cli_runs_adapter_loop_and_owned_tool(demo, capsys):
    _, model, backend = demo
    assert main(ARGS) == 0
    assert capsys.readouterr().out.endswith("The task is running.\n")
    assert model.invoke.call_count == 2
    backend.assert_called_once()


def test_invalid_token_stops_before_model_or_backend(demo, monkeypatch, capsys):
    provider, model, backend = demo
    monkeypatch.setenv("CREWOS_TENANT_TOKEN", "wrong-token")
    assert main(ARGS) == 2
    captured = capsys.readouterr()
    assert captured.err == "Tenant authentication failed.\n"
    provider.assert_not_called()
    model.invoke.assert_not_called()
    backend.assert_not_called()


def test_interactive_token_prompt_is_used_when_environment_token_absent(demo, monkeypatch):
    monkeypatch.delenv("CREWOS_TENANT_TOKEN")
    with patch("crewos.core.agent_demo.getpass", return_value="test-token") as prompt:
        assert main(ARGS) == 0
    prompt.assert_called_once_with("Tenant token: ")


def test_cli_does_not_grant_missing_tool_permissions(demo, monkeypatch, capsys):
    _, _, backend = demo
    monkeypatch.setattr(settings, "TENANT_PERMISSIONS", {})
    assert main(ARGS) == 1
    assert capsys.readouterr().err == "Agent run failed (PolicyDeniedError).\n"
    backend.assert_not_called()


def test_cli_passes_decision_limit(demo, capsys):
    _, model, _ = demo
    assert main(ARGS + ["--max-decisions", "1"]) == 1
    assert "DecisionLimitExceeded" in capsys.readouterr().err
    model.invoke.assert_called_once()


def test_cli_failure_does_not_print_raw_exception(demo, capsys):
    _, model, backend = demo
    model.invoke.side_effect = RuntimeError("sensitive-provider-detail test-token")
    assert main(ARGS) == 1
    captured = capsys.readouterr()
    assert captured.err == "Agent run failed (RuntimeError).\n"
    assert "sensitive-provider-detail" not in captured.out
    assert "test-token" not in captured.out
    backend.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-decisions", "0"],
        ["--max-decisions", "many"],
        ["--message", " "],
    ],
)
def test_invalid_cli_arguments_stop_before_dependencies(demo, extra):
    provider, _, backend = demo
    with pytest.raises(SystemExit) as error:
        main(ARGS + extra)
    assert error.value.code == 2
    provider.assert_not_called()
    backend.assert_not_called()
