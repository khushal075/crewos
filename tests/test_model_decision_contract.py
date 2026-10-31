"""Model proposals are exclusive, JSON-shaped, and separate from authority."""

import pytest
from pydantic import TypeAdapter, ValidationError

from crewos.application.dtos.model_decision import (
    FinalAnswerDecision,
    ModelDecision,
    ToolCallDecision,
)

adapter = TypeAdapter(ModelDecision)


def test_tool_call_json_selects_tool_contract_and_preserves_arguments():
    decision = adapter.validate_json(
        '{"type":"tool_call","name":"get_agent_task_status",'
        '"arguments":{"agent_task_id":"task-123"}}'
    )
    assert isinstance(decision, ToolCallDecision)
    assert decision.arguments == {"agent_task_id": "task-123"}
    assert adapter.validate_json(adapter.dump_json(decision)) == decision


def test_final_answer_selects_answer_contract():
    decision = adapter.validate_python({"type": "final_answer", "answer": " Still running. "})
    assert isinstance(decision, FinalAnswerDecision)
    assert decision.answer == "Still running."
    assert adapter.validate_json(adapter.dump_json(decision)) == decision


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "execute", "name": "get_agent_task_status", "arguments": {}},
        {"type": "tool_call", "name": "get_agent_task_status"},
        {"type": "tool_call", "arguments": {}},
        {"type": "tool_call", "name": " ", "arguments": {}},
        {"type": "tool_call", "name": 123, "arguments": {}},
        {"type": "tool_call", "name": "example", "arguments": []},
        {"type": "tool_call", "name": "example", "arguments": "{}"},
        {"type": "final_answer"},
        {"type": "final_answer", "answer": " "},
        {"type": "final_answer", "answer": 123},
        {"type": "final_answer", "answer": "done", "name": "example"},
        {"type": "tool_call", "name": "example", "arguments": {}, "answer": "done"},
    ],
)
def test_rejects_missing_malformed_or_mixed_decisions(payload):
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "tenant_a"),
        ("permissions", ["agent_task.read"]),
        ("policy_effect", "allow"),
    ],
)
def test_tool_decision_rejects_top_level_authority_claims(field, value):
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "type": "tool_call",
                "name": "example",
                "arguments": {},
                field: value,
            }
        )


def test_envelope_does_not_claim_tool_registration_or_tool_specific_validation():
    decision = adapter.validate_python(
        {
            "type": "tool_call",
            "name": "not_registered",
            "arguments": {"unexpected": True},
        }
    )
    assert decision.name == "not_registered"
    # Registry lookup and each tool's argument schema are dispatcher responsibilities.
    assert decision.arguments == {"unexpected": True}


def test_json_shaped_arguments_can_contain_nested_tool_data():
    decision = ToolCallDecision(
        type="tool_call",
        name="example",
        arguments={"filters": {"enabled": True, "tags": ["a", None], "limit": 5}},
    )
    assert decision.arguments["filters"]["tags"] == ["a", None]


def test_python_objects_are_not_valid_json_arguments():
    with pytest.raises(ValidationError):
        ToolCallDecision(type="tool_call", name="example", arguments={"value": object()})
