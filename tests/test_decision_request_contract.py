"""Keep model-visible inputs distinct from executable tools and identity."""

import pytest
from pydantic import ValidationError

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusInput
from crewos.application.dtos.decision_request import DecisionMessage, ToolSchema
from crewos.application.interfaces.decision_provider import DecisionProviderInterface


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
def test_messages_preserve_content_formatting(role):
    message = DecisionMessage(role=role, content="  Preserve this formatting.\n")
    assert message.content == "  Preserve this formatting.\n"
    assert message.model_dump()["role"] == role


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "admin", "content": "hello"},
        {"role": "user", "content": ""},
        {"role": "user", "content": 123},
        {"role": "user", "content": "hello", "tenant_id": "tenant_a"},
    ],
)
def test_invalid_message_shapes_are_rejected(payload):
    with pytest.raises(ValidationError):
        DecisionMessage.model_validate(payload)


def test_tool_schema_can_publish_pydantic_argument_schema():
    schema = GetAgentTaskStatusInput.model_json_schema()
    tool = ToolSchema(
        name="get_agent_task_status",
        description="Read an owned task's status.",
        parameters=schema,
    )
    assert tool.parameters == schema
    assert tool.model_dump(mode="json")["parameters"]["required"] == ["agent_task_id"]


@pytest.mark.parametrize("extra,value", [("handler", lambda: None), ("permissions", ["*"])])
def test_tool_metadata_rejects_executable_or_authority_fields(extra, value):
    with pytest.raises(ValidationError):
        ToolSchema.model_validate(
            {
                "name": "example",
                "description": "Example tool",
                "parameters": {},
                extra: value,
            }
        )


def test_decision_provider_requires_an_implementation():
    with pytest.raises(TypeError):
        DecisionProviderInterface()
