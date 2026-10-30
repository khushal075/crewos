"""Validate tool arguments without a model, network, or task store."""

import pytest
from pydantic import TypeAdapter, ValidationError

from crewos.application.dtos.agent_task_status import (
    AgentTaskFound,
    AgentTaskStatus,
    AgentTaskUnavailable,
    GetAgentTaskStatusInput,
    GetAgentTaskStatusResult,
    TaskLookupOutcome,
)


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


@pytest.mark.parametrize("value", ["found", "unavailable"])
def test_lookup_outcome_round_trips_as_a_json_string(value):
    adapter = TypeAdapter(TaskLookupOutcome)
    outcome = adapter.validate_json('"' + value + '"')
    assert isinstance(outcome, TaskLookupOutcome)
    assert adapter.dump_json(outcome).decode() == '"' + value + '"'


@pytest.mark.parametrize("value", ["running", "failed", "", None, 123, {}])
def test_lookup_outcome_rejects_task_states_and_invalid_values(value):
    with pytest.raises(ValidationError):
        TypeAdapter(TaskLookupOutcome).validate_python(value)


@pytest.mark.parametrize("value", ["queued", "running", "succeeded", "failed"])
def test_task_status_round_trips_as_a_json_string(value):
    adapter = TypeAdapter(AgentTaskStatus)
    status = adapter.validate_json('"' + value + '"')
    assert isinstance(status, AgentTaskStatus)
    assert adapter.dump_json(status).decode() == '"' + value + '"'


@pytest.mark.parametrize("value", ["found", "unavailable", "PENDING", "", None, 123])
def test_task_status_rejects_lookup_outcomes_and_unsupported_values(value):
    with pytest.raises(ValidationError):
        TypeAdapter(AgentTaskStatus).validate_python(value)


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed"])
def test_found_response_returns_task_state_as_json_strings(status):
    payload = {"outcome": "found", "agent_task_id": "agent_task_123", "status": status}
    response = AgentTaskFound.model_validate(payload)
    assert response.model_dump(mode="json") == payload


def test_unavailable_response_contains_no_task_state():
    payload = {"outcome": "unavailable", "agent_task_id": "agent_task_123"}
    response = AgentTaskUnavailable.model_validate(payload)
    assert response.model_dump(mode="json") == payload


@pytest.mark.parametrize("field", ["outcome", "agent_task_id", "status"])
def test_found_response_requires_all_fields(field):
    payload = {"outcome": "found", "agent_task_id": "agent_task_123", "status": "failed"}
    del payload[field]
    with pytest.raises(ValidationError) as error:
        AgentTaskFound.model_validate(payload)
    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["outcome", "agent_task_id"])
def test_unavailable_response_requires_its_fields(field):
    payload = {"outcome": "unavailable", "agent_task_id": "agent_task_123"}
    del payload[field]
    with pytest.raises(ValidationError) as error:
        AgentTaskUnavailable.model_validate(payload)
    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("status", ["running", None])
def test_unavailable_response_rejects_status_even_when_null(status):
    with pytest.raises(ValidationError) as error:
        AgentTaskUnavailable.model_validate({
            "outcome": "unavailable", "agent_task_id": "agent_task_123", "status": status,
        })
    assert error.value.errors()[0]["loc"] == ("status",)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("model,payload", [
    (AgentTaskFound, {"outcome": "unavailable", "agent_task_id": "agent_task_123", "status": "failed"}),
    (AgentTaskUnavailable, {"outcome": "found", "agent_task_id": "agent_task_123"}),
    (AgentTaskFound, {"outcome": "found", "agent_task_id": "agent_task_123", "status": "unavailable"}),
])
def test_response_rejects_mismatched_outcome_or_invalid_state(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("model,outcome", [
    (AgentTaskFound, "found"), (AgentTaskUnavailable, "unavailable"),
])
@pytest.mark.parametrize("value", [" ", None, 123])
def test_responses_reject_invalid_task_identifiers(model, outcome, value):
    payload = {"outcome": outcome, "agent_task_id": value}
    if outcome == "found":
        payload["status"] = "running"
    with pytest.raises(ValidationError) as error:
        model.model_validate(payload)
    assert error.value.errors()[0]["loc"] == ("agent_task_id",)


@pytest.mark.parametrize("model,outcome", [
    (AgentTaskFound, "found"), (AgentTaskUnavailable, "unavailable"),
])
def test_responses_reject_undeclared_fields(model, outcome):
    payload = {"outcome": outcome, "agent_task_id": "agent_task_123", "tenant_id": "tenant_a"}
    if outcome == "found":
        payload["status"] = "running"
    with pytest.raises(ValidationError) as error:
        model.model_validate(payload)
    assert error.value.errors()[0]["loc"] == ("tenant_id",)


@pytest.mark.parametrize("payload,expected_model", [
    ({"outcome": "found", "agent_task_id": "agent_task_123", "status": "failed"}, AgentTaskFound),
    ({"outcome": "unavailable", "agent_task_id": "agent_task_123"}, AgentTaskUnavailable),
])
def test_result_selects_model_from_outcome(payload, expected_model):
    adapter = TypeAdapter(GetAgentTaskStatusResult)
    result = adapter.validate_python(payload)
    assert isinstance(result, expected_model)
    assert adapter.dump_python(result, mode="json") == payload


@pytest.mark.parametrize("payload", [
    {"agent_task_id": "agent_task_123"},
    {"outcome": "unknown", "agent_task_id": "agent_task_123"},
    {"outcome": "running", "agent_task_id": "agent_task_123"},
    {"outcome": "found", "agent_task_id": "agent_task_123"},
    {"outcome": "unavailable", "agent_task_id": "agent_task_123", "status": "running"},
])
def test_result_rejects_missing_tags_and_invalid_shapes(payload):
    with pytest.raises(ValidationError):
        TypeAdapter(GetAgentTaskStatusResult).validate_python(payload)
