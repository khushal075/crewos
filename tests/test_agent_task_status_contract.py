"""Validate tool arguments without a model, network, or task store."""

import pytest
from pydantic import ValidationError

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusInput


def test_accepts_agent_task_identifier():
    arguments = GetAgentTaskStatusInput.model_validate({"agent_task_id": "agent_task_123"})
    assert arguments.model_dump() == {"agent_task_id": "agent_task_123"}


def test_trims_surrounding_whitespace_from_identifier():
    arguments = GetAgentTaskStatusInput(agent_task_id="  agent_task_123\t")
    assert arguments.agent_task_id == "agent_task_123"


@pytest.mark.parametrize("value", ["", " \t\n", None, 123, True, [], {}, b"agent_task_123"])
def test_rejects_blank_or_non_string_identifier(value):
    with pytest.raises(ValidationError) as error:
        GetAgentTaskStatusInput.model_validate({"agent_task_id": value})
    assert error.value.errors()[0]["loc"] == ("agent_task_id",)


def test_requires_agent_task_identifier():
    with pytest.raises(ValidationError) as error:
        GetAgentTaskStatusInput.model_validate({})
    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("field", ["tenant_id", "permissions", "unexpected"])
def test_rejects_undeclared_arguments(field):
    with pytest.raises(ValidationError) as error:
        GetAgentTaskStatusInput.model_validate({"agent_task_id": "agent_task_123", field: "value"})
    assert error.value.errors()[0]["type"] == "extra_forbidden"
    assert error.value.errors()[0]["loc"] == (field,)
