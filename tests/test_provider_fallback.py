from decimal import Decimal

import httpx
import pytest
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.application.services.decision_errors import DecisionRefusedError, DecisionResponseError
from crewos.application.services.execution_control import (
    AgentDeadlineExceeded,
    ExecutionControl,
    ResourceBudgetExceeded,
    execution_scope,
)
from crewos.application.services.fallback_decision_provider import FallbackDecisionProvider
from crewos.third_party.llm.fallback_configuration import (
    FallbackModelConfiguration,
    eligible_provider_failure,
)


def failure(code):
    request = httpx.Request("POST", "https://fixture.example/v1")
    return APIStatusError("failure", response=httpx.Response(code, request=request), body=None)


class Provider:
    def __init__(self, outcome, *, advance=None):
        self.outcome, self.advance, self.calls = outcome, advance, []

    def decide(self, *, messages, tools, timeout_seconds):
        self.calls.append(timeout_seconds)
        if self.advance:
            self.advance()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def decide(primary, secondary, **kwargs):
    return FallbackDecisionProvider(
        primary, secondary, eligible=eligible_provider_failure, primary_timeout=2, **kwargs
    ).decide(messages=(), tools=(), timeout_seconds=5)


def test_fallback_preserves_remaining_time_and_valid_decision():
    clock = [0.0]
    primary = Provider(failure(503), advance=lambda: clock.__setitem__(0, 2))
    expected = FinalAnswerDecision(type="final_answer", answer="fallback")
    secondary = Provider(expected)
    assert decide(primary, secondary, clock=lambda: clock[0]) == expected
    assert primary.calls == [2] and secondary.calls == [3]


@pytest.mark.parametrize(
    "error",
    [
        failure(401),
        failure(403),
        failure(400),
        DecisionRefusedError("refused"),
        DecisionResponseError("invalid"),
        ResourceBudgetExceeded(),
    ],
)
def test_no_fallback_for_refusal_invalid_or_policy_errors(error):
    secondary = Provider(None)
    with pytest.raises(type(error)):
        decide(Provider(error), secondary)
    assert secondary.calls == []


def test_no_fallback_after_deadline_or_unpriced_secondary():
    secondary = Provider(None)
    clock = [0.0]
    with pytest.raises(AgentDeadlineExceeded):
        decide(
            Provider(failure(503), advance=lambda: clock.__setitem__(0, 5)),
            secondary,
            clock=lambda: clock[0],
        )
    control = ExecutionControl(max_cost_usd=1, input_usd_per_million=2, output_usd_per_million=3)
    with execution_scope(control), pytest.raises(APIStatusError):
        decide(Provider(failure(503)), secondary)
    with execution_scope(control), pytest.raises(APIStatusError):
        decide(Provider(failure(503)), secondary, secondary_rates=(Decimal(3), Decimal(3)))
    assert secondary.calls == []


def test_priced_secondary_allowed_at_or_below_reserved_ceiling():
    control = ExecutionControl(max_cost_usd=1, input_usd_per_million=2, output_usd_per_million=3)
    expected = FinalAnswerDecision(type="final_answer", answer="fallback")
    with execution_scope(control):
        assert (
            decide(
                Provider(
                    APIConnectionError(request=httpx.Request("POST", "https://fixture.example"))
                ),
                Provider(expected),
                secondary_rates=(Decimal(2), Decimal(1)),
            )
            == expected
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://remote.example/v1"},
        {"base_url": "https://user:pass@remote.example/v1"},
        {"api_key": "a\nb"},
        {"input_usd_per_million": 1},
    ],
)
def test_invalid_fallback_configuration(changes):
    with pytest.raises(ValidationError):
        FallbackModelConfiguration.model_validate(
            {
                "base_url": "https://remote.example/v1",
                "model": "model",
                "api_key": "secret",
                **changes,
            }
        )
