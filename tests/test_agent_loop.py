"""Scripted decisions verify orchestration, not actual model reasoning."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision, ToolCallDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.decision_provider import DecisionProviderInterface
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.services.tool_registry import UnknownToolError
from crewos.application.use_cases.agent_loop import AgentLoop, DecisionLimitExceeded
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis


class ScriptedProvider(DecisionProviderInterface):
    def __init__(self, decisions):
        self._decisions = iter(decisions)
        self.calls = []

    def decide(self, *, messages, tools):
        self.calls.append((messages, tools))
        return next(self._decisions)


def tool_call(name="get_agent_task_status"):
    return ToolCallDecision(type="tool_call", name=name, arguments={"agent_task_id": "task-123"})


def inputs(tenant="tenant_a", permissions=("agent_task.read",)):
    return {
        "messages": [DecisionMessage(role="user", content="Check task-123's status.")],
        "context": ToolExecutionContext(tenant_id=tenant, permissions=permissions),
    }


def setup_loop(decisions, max_decisions=5):
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    backend = Mock(return_value=SimpleNamespace(state="STARTED"))
    provider = ScriptedProvider(decisions)
    loop = AgentLoop(
        provider=provider,
        dispatcher=build_tool_dispatcher(reader=CeleryTaskStatusReader(store, backend)),
        max_decisions=max_decisions,
    )
    return loop, provider, backend


def test_tool_observation_reaches_next_decision_without_mutating_caller_history():
    final = FinalAnswerDecision(type="final_answer", answer="The task is running.")
    loop, provider, backend = setup_loop([tool_call(), final])
    request = inputs()
    assert loop.run(**request) == final
    assert len(request["messages"]) == 1
    assert len(provider.calls) == 2
    first_history, schemas = provider.calls[0]
    second_history, _ = provider.calls[1]
    assert len(first_history) == 1
    assert [message.role for message in second_history] == ["user", "assistant", "tool"]
    observation = json.loads(second_history[-1].content)
    assert observation == {
        "name": "get_agent_task_status",
        "result": {"outcome": "found", "agent_task_id": "task-123", "status": "running"},
    }
    assert schemas[0].name == "get_agent_task_status"
    backend.assert_called_once_with("task-123")


def test_immediate_final_answer_never_executes_tool():
    final = FinalAnswerDecision(type="final_answer", answer="No lookup requested.")
    loop, provider, backend = setup_loop([final])
    assert loop.run(**inputs()) == final
    assert len(provider.calls) == 1
    backend.assert_not_called()


def test_repeated_tool_calls_stop_at_decision_limit():
    loop, provider, backend = setup_loop([tool_call()] * 3, max_decisions=2)
    with pytest.raises(DecisionLimitExceeded):
        loop.run(**inputs())
    assert len(provider.calls) == 2
    assert backend.call_count == 2


def test_denied_tool_stops_before_backend_and_next_decision():
    loop, provider, backend = setup_loop([tool_call()])
    with pytest.raises(PolicyDeniedError):
        loop.run(**inputs(permissions=()))
    assert len(provider.calls) == 1
    backend.assert_not_called()


def test_cross_tenant_tool_returns_unavailable_observation():
    loop, provider, backend = setup_loop(
        [
            tool_call(),
            FinalAnswerDecision(type="final_answer", answer="Task unavailable."),
        ]
    )
    loop.run(**inputs(tenant="tenant_b"))
    observation = json.loads(provider.calls[1][0][-1].content)
    assert observation["result"] == {"outcome": "unavailable", "agent_task_id": "task-123"}
    backend.assert_not_called()


def test_unknown_tool_stops_without_backend_access():
    loop, provider, backend = setup_loop([tool_call("unregistered")])
    with pytest.raises(UnknownToolError):
        loop.run(**inputs())
    assert len(provider.calls) == 1
    backend.assert_not_called()


def test_malformed_provider_decision_is_rejected_before_dispatch():
    loop, _, backend = setup_loop([{"type": "tool_call", "name": "example"}])
    with pytest.raises(ValidationError):
        loop.run(**inputs())
    backend.assert_not_called()


def test_backend_failure_is_not_replaced_by_final_answer():
    loop, provider, backend = setup_loop([tool_call()])
    failure = RuntimeError("backend failed")
    backend.side_effect = failure
    with pytest.raises(RuntimeError) as error:
        loop.run(**inputs())
    assert error.value is failure
    assert len(provider.calls) == 1


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_invalid_decision_budgets_are_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        setup_loop([], max_decisions=limit)
