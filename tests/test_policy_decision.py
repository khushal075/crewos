"""Validate decision structure without evaluating permissions or resources."""

import pytest
from pydantic import ValidationError

from crewos.application.dtos.policy_decision import PolicyDecision, PolicyEffect


@pytest.mark.parametrize("effect,reason", [
    ("allow", "permission_granted"), ("deny", "permission_missing"),
])
def test_decision_round_trips_as_json_fields(effect, reason):
    payload = {"effect": effect, "reason_code": reason, "policy_version": "1"}
    decision = PolicyDecision.model_validate(payload)
    assert isinstance(decision.effect, PolicyEffect)
    assert decision.model_dump(mode="json") == payload


@pytest.mark.parametrize("field", ["effect", "reason_code", "policy_version"])
def test_decision_requires_explicit_fields(field):
    payload = {"effect": "deny", "reason_code": "permission_missing", "policy_version": "1"}
    del payload[field]
    with pytest.raises(ValidationError) as error:
        PolicyDecision.model_validate(payload)
    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("effect", ["found", "running", "", None, True])
def test_decision_rejects_unsupported_effects(effect):
    with pytest.raises(ValidationError):
        PolicyDecision(effect=effect, reason_code="permission_missing", policy_version="1")


@pytest.mark.parametrize("reason", ["", " ", "Permission Missing", "permission.missing", None, 123])
def test_reason_requires_a_machine_readable_string(reason):
    with pytest.raises(ValidationError):
        PolicyDecision(effect="deny", reason_code=reason, policy_version="1")


@pytest.mark.parametrize("version", ["", " \t", None, 1])
def test_policy_version_requires_a_nonblank_string(version):
    with pytest.raises(ValidationError):
        PolicyDecision(effect="deny", reason_code="permission_missing", policy_version=version)


def test_decision_cannot_be_changed_in_place():
    decision = PolicyDecision(effect="deny", reason_code="permission_missing", policy_version="1")
    with pytest.raises(ValidationError) as error:
        decision.effect = PolicyEffect.ALLOW
    assert error.value.errors()[0]["type"] == "frozen_instance"


def test_decision_rejects_undeclared_fields():
    with pytest.raises(ValidationError) as error:
        PolicyDecision(effect="deny", reason_code="permission_missing", policy_version="1", execute=True)
    assert error.value.errors()[0]["type"] == "extra_forbidden"
