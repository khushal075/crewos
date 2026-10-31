"""Full adapter/loop/tool assembly with controlled model and storage responses."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.use_cases.agent_loop import DecisionLimitExceeded
from crewos.core.agent_setup import build_agent_loop
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader

from tests.helpers import MemoryRedis

TOOL_CALL = (
    '{"type":"tool_call","name":"get_agent_task_status","arguments":{"agent_task_id":"task-123"}}'
)


def run_inputs(tenant="tenant_a", permissions=("agent_task.read",)):
    return {
        "messages": [DecisionMessage(role="user", content="Check task-123")],
        "context": ToolExecutionContext(tenant_id=tenant, permissions=permissions),
    }


def test_assembly_does_not_call_model_or_reader():
    model = Mock(spec=BaseChatModel)
    reader = Mock(spec=TaskStatusReaderInterface)
    build_agent_loop(model=model, reader=reader)
    model.invoke.assert_not_called()
    reader.read.assert_not_called()


def test_assembled_loop_translates_observation_into_next_model_request():
    model = Mock(spec=BaseChatModel)
    model.invoke.side_effect = [
        AIMessage(content=TOOL_CALL),
        AIMessage(content='{"type":"final_answer","answer":"The task is running."}'),
    ]
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    backend = Mock(return_value=SimpleNamespace(state="STARTED"))
    loop = build_agent_loop(model=model, reader=CeleryTaskStatusReader(store, backend))
    result = loop.run(**run_inputs())
    assert result.answer == "The task is running."
    system_prompt = model.invoke.call_args_list[0].args[0][0].content
    metadata = json.loads(system_prompt.split("\n", 1)[1])
    assert metadata["tools"][0]["name"] == "get_agent_task_status"
    assert metadata["tools"][0]["parameters"]["required"] == ["agent_task_id"]
    assert "handler" not in metadata["tools"][0]
    assert model.invoke.call_count == 2
    backend.assert_called_once_with("task-123")
    history = json.loads(model.invoke.call_args.args[0][1].content)["messages"]
    observation = json.loads(history[-1]["content"])
    assert history[-1]["role"] == "tool"
    assert observation["result"]["status"] == "running"


def test_assembled_loop_cannot_bypass_policy():
    model = Mock(spec=BaseChatModel)
    model.invoke.return_value = AIMessage(content=TOOL_CALL)
    reader = Mock(spec=TaskStatusReaderInterface)
    loop = build_agent_loop(model=model, reader=reader)
    with pytest.raises(PolicyDeniedError):
        loop.run(**run_inputs(permissions=()))
    reader.read.assert_not_called()
    model.invoke.assert_called_once()


def test_assembly_passes_decision_budget_to_loop():
    model = Mock(spec=BaseChatModel)
    model.invoke.return_value = AIMessage(content=TOOL_CALL)
    reader = Mock(spec=TaskStatusReaderInterface)
    from crewos.application.dtos.agent_task_status import AgentTaskUnavailable

    reader.read.return_value = AgentTaskUnavailable(outcome="unavailable", agent_task_id="task-123")
    loop = build_agent_loop(model=model, reader=reader, max_decisions=1)
    with pytest.raises(DecisionLimitExceeded):
        loop.run(**run_inputs())
    model.invoke.assert_called_once()
    reader.read.assert_called_once()
