"""Lifecycle metadata records progress without retaining sensitive content."""

import json
from unittest.mock import Mock
from uuid import UUID

import pytest

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision, ToolCallDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.tool_registry import UnknownToolError
from crewos.application.use_cases.agent_loop import AgentLoop, DecisionLimitExceeded
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.dtos.agent_task_status import AgentTaskUnavailable
from crewos.application.services.policy_executor import PolicyDeniedError
from tests.test_agent_loop import ScriptedProvider

SECRET = "sensitive-content-do-not-log"


def run(decisions, *, max_decisions=5, permissions=("agent_task.read",)):
    logger = Mock()
    reader = Mock(spec=TaskStatusReaderInterface)
    reader.read.return_value = AgentTaskUnavailable(outcome="unavailable", agent_task_id=SECRET)
    loop = AgentLoop(
        provider=ScriptedProvider(decisions),
        dispatcher=build_tool_dispatcher(reader=reader),
        logger=logger,
        max_decisions=max_decisions,
    )
    kwargs = {
        "messages": [DecisionMessage(role="user", content=SECRET)],
        "context": ToolExecutionContext(tenant_id="tenant_a", permissions=permissions),
    }
    return loop, logger, reader, kwargs


def events(logger):
    return [call.args[1] for call in logger.log.call_args_list]


def tool(name="get_agent_task_status"):
    return ToolCallDecision(type="tool_call", name=name, arguments={"agent_task_id": SECRET})


def test_success_logs_correlated_events_without_content():
    loop, logger, _, kwargs = run([tool(), FinalAnswerDecision(type="final_answer", answer=SECRET)])
    loop.run(**kwargs)
    records = events(logger)
    assert [record["event"] for record in records] == [
        "agent_run_started",
        "agent_model_call_started",
        "agent_model_call_completed",
        "agent_tool_call_started",
        "agent_tool_call_completed",
        "agent_model_call_started",
        "agent_model_call_completed",
        "agent_run_completed",
    ]
    ids = {record["agent_run_id"] for record in records}
    assert len(ids) == 1
    UUID(next(iter(ids)))
    tool_records = [record for record in records if record["event"].startswith("agent_tool")]
    assert tool_records[0]["tool_call_id"] == tool_records[1]["tool_call_id"]
    assert tool_records[0]["tool_call_id"] != records[0]["agent_run_id"]
    assert records[-1]["decisions"] == 2
    assert records[-1]["tools_completed"] == 1
    assert all(record.get("duration_ms", 0) >= 0 for record in records)
    assert SECRET not in json.dumps(records)


@pytest.mark.parametrize(
    "kind,error_type,phase",
    [
        ("denied", PolicyDeniedError, "tool"),
        ("unknown", UnknownToolError, "tool"),
        ("limit", DecisionLimitExceeded, "decision_limit"),
    ],
)
def test_failures_log_one_terminal_event_without_raw_details(kind, error_type, phase):
    decision = tool(SECRET) if kind == "unknown" else tool()
    loop, logger, _, kwargs = run(
        [decision],
        max_decisions=1,
        permissions=() if kind == "denied" else ("agent_task.read",),
    )
    with pytest.raises(error_type):
        loop.run(**kwargs)
    records = events(logger)
    assert records[-1]["event"] == "agent_run_failed"
    assert records[-1]["phase"] == phase
    assert records[-1]["error_type"] == error_type.__name__
    assert sum(record["event"] == "agent_run_failed" for record in records) == 1
    assert not any(record["event"] == "agent_run_completed" for record in records)
    assert SECRET not in json.dumps(records)


def test_model_failure_logs_phase_and_preserves_exception():
    loop, logger, _, kwargs = run([])
    failure = RuntimeError(SECRET)
    loop._provider = Mock()
    loop._provider.decide.side_effect = failure
    with pytest.raises(RuntimeError) as error:
        loop.run(**kwargs)
    assert error.value is failure
    assert events(logger)[-1]["phase"] == "model"
    assert SECRET not in json.dumps(events(logger))


def test_separate_invocations_receive_distinct_run_ids():
    final = FinalAnswerDecision(type="final_answer", answer="Done")
    loop, logger, _, kwargs = run([final, final])
    loop.run(**kwargs)
    loop.run(**kwargs)
    starts = [record for record in events(logger) if record["event"] == "agent_run_started"]
    assert starts[0]["agent_run_id"] != starts[1]["agent_run_id"]
