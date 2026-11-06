"""Real HTTP, policy, dispatcher and agent-loop verification of read-only integration."""

from datetime import datetime, timezone
import json
import logging
from unittest.mock import Mock, MagicMock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision, ToolCallDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.dtos.webhook_delivery_status import (
    GetWebhookDeliveryStatusInput,
    WebhookDeliveryFound,
)
from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookResponseInvalid,
    WebhookServiceUnavailable,
)
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor, PolicyDeniedError
from crewos.application.use_cases.get_webhook_delivery_status import GetWebhookDeliveryStatusHandler
from crewos.application.use_cases.agent_loop import AgentLoop
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.evaluation.webhook_reference_service import webhook_server
from crewos.infrastructure.webhook_fixture_reader import FixtureWebhookDeliveryStatusReader
from crewos.third_party.webhooks.http_delivery_config import HttpWebhookTenantConfig
from crewos.third_party.webhooks.http_delivery_reader import HttpWebhookDeliveryStatusReader
from tests.test_agent_loop import ScriptedProvider


def context(tenant="tenant_a", permissions=("webhook_delivery.read",)):
    return ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions))


def found(delivery="same_id", status="pending"):
    return WebhookDeliveryFound(
        outcome="found", delivery_id=delivery, status=status, observed_at=datetime.now(timezone.utc)
    )


def http_reader(origin, **kwargs):
    return HttpWebhookDeliveryStatusReader(
        {
            "tenant_a": HttpWebhookTenantConfig(base_url=origin, token="test-token-a"),
            "tenant_b": HttpWebhookTenantConfig(base_url=origin, token="test-token-b"),
        },
        **kwargs,
    )


def test_fixture_is_scoped_and_snapshots_records():
    records = {
        ("tenant_a", "same_id"): found(status="pending"),
        ("tenant_b", "same_id"): found(status="failed"),
    }
    reader = FixtureWebhookDeliveryStatusReader(records)
    records.clear()
    assert (
        reader.read(delivery_id="same_id", tenant_id="tenant_a", timeout_seconds=1).status
        == "pending"
    )
    assert (
        reader.read(delivery_id="same_id", tenant_id="tenant_b", timeout_seconds=1).status
        == "failed"
    )
    assert (
        reader.read(delivery_id="same_id", tenant_id="tenant_c", timeout_seconds=1).outcome
        == "unavailable"
    )
    assert (
        reader.read(delivery_id="missing", tenant_id="tenant_a", timeout_seconds=1).outcome
        == "unavailable"
    )
    with pytest.raises(ValueError):
        FixtureWebhookDeliveryStatusReader({("tenant_a", "different"): found()})


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), "5"])
def test_fixture_timeout_admission(timeout):
    with pytest.raises(ValueError):
        FixtureWebhookDeliveryStatusReader({}).read(
            delivery_id="id", tenant_id="tenant_a", timeout_seconds=timeout
        )


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "ftp://example.com",
        "https://example.com:0",
        "https://example.com:bad",
    ],
)
def test_config_rejects_unsafe_origins(origin):
    with pytest.raises(ValidationError):
        HttpWebhookTenantConfig(base_url=origin, token="private-token")


@pytest.mark.parametrize("token", ["", "white space", "line\nbreak", "x" * 4097])
def test_config_validates_and_hides_credentials(token):
    with pytest.raises(ValidationError) as error:
        HttpWebhookTenantConfig(base_url="https://example.com", token=token)
    assert "input_value=" not in str(error.value)
    assert "private-token" not in repr(
        HttpWebhookTenantConfig(base_url="https://example.com", token="private-token")
    )


def test_live_http_scopes_credentials_and_does_not_write():
    with webhook_server() as (origin, calls):
        reader = http_reader(origin)
        a = reader.read(delivery_id="same_id", tenant_id="tenant_a", timeout_seconds=1)
        b = reader.read(delivery_id="same_id", tenant_id="tenant_b", timeout_seconds=1)
        assert a.status == b.status == "succeeded"
        assert [c["authorization"] for c in calls] == ["Bearer test-token-a", "Bearer test-token-b"]
        assert all(c["method"] == "GET" for c in calls)
        assert (
            reader.read(delivery_id="only_a", tenant_id="tenant_b", timeout_seconds=1).outcome
            == "unavailable"
        )
        assert (
            reader.read(delivery_id="missing", tenant_id="tenant_b", timeout_seconds=1).outcome
            == "unavailable"
        )
        assert (
            reader.read(delivery_id="unknown", tenant_id="tenant_a", timeout_seconds=1).status
            == "unknown"
        )
        assert (
            reader.read(delivery_id="retry", tenant_id="tenant_a", timeout_seconds=1).status
            == "retry_scheduled"
        )


@pytest.mark.parametrize(
    "delivery",
    [
        "wrong_tenant",
        "wrong_id",
        "bad_json",
        "duplicate_keys",
        "bad_state",
        "mime",
        "empty204",
        "gzip",
    ],
)
def test_bad_upstream_data_is_not_a_found_or_missing_record(delivery):
    with webhook_server() as (origin, _):
        with pytest.raises(WebhookResponseInvalid) as error:
            http_reader(origin).read(delivery_id=delivery, tenant_id="tenant_a", timeout_seconds=1)
    assert "private malformed response" not in str(error.value)


@pytest.mark.parametrize(
    "delivery", ["error401", "error403", "error429", "error500", "error503", "redirect"]
)
def test_service_errors_and_redirects_never_become_missing_records(delivery):
    with webhook_server() as (origin, calls):
        with pytest.raises(WebhookServiceUnavailable) as error:
            http_reader(origin).read(delivery_id=delivery, tenant_id="tenant_a", timeout_seconds=1)
        assert len(calls) == 1  # no redirect following or automatic retries
        assert "must-never-be-logged" not in str(error.value)


def test_timeout_and_response_admission():
    with webhook_server() as (origin, _):
        with pytest.raises(WebhookServiceUnavailable):
            http_reader(origin).read(delivery_id="slow", tenant_id="tenant_a", timeout_seconds=0.02)
        with pytest.raises(WebhookResponseInvalid, match="byte admission"):
            http_reader(origin, max_response_bytes=512).read(
                delivery_id="oversize", tenant_id="tenant_a", timeout_seconds=1
            )


def test_missing_tenant_configuration_never_sends_request():
    with webhook_server() as (origin, calls):
        with pytest.raises(WebhookServiceUnavailable):
            http_reader(origin).read(delivery_id="id", tenant_id="tenant_c", timeout_seconds=1)
        assert calls == []


def test_handler_denial_and_returned_identity_validation():
    reader = Mock()
    handler = GetWebhookDeliveryStatusHandler(
        executor=PolicyEnforcedExecutor(PermissionPolicyService()), reader=reader
    )
    arguments = GetWebhookDeliveryStatusInput(delivery_id="same_id")
    with pytest.raises(PolicyDeniedError):
        handler.execute(arguments=arguments, context=context(permissions=()))
    reader.read.assert_not_called()
    reader.read.return_value = found(delivery="different")
    with pytest.raises(WebhookResponseInvalid):
        handler.execute(arguments=arguments, context=context())
    reader.read.return_value = {}
    with pytest.raises(WebhookResponseInvalid):
        handler.execute(arguments=arguments, context=context())


def test_handler_uses_remaining_budget_and_safe_failure_logs(monkeypatch, caplog):
    control = Mock()
    control.tool_remaining.return_value = 0.25
    monkeypatch.setattr(
        "crewos.application.use_cases.get_webhook_delivery_status.get_execution_control",
        lambda: control,
    )
    reader = Mock()
    reader.read.side_effect = WebhookServiceUnavailable("private-exception-body")
    handler = GetWebhookDeliveryStatusHandler(
        executor=PolicyEnforcedExecutor(PermissionPolicyService()), reader=reader
    )
    with pytest.raises(WebhookServiceUnavailable):
        handler.execute(
            arguments=GetWebhookDeliveryStatusInput(delivery_id="id"), context=context()
        )
    assert reader.read.call_args.kwargs["timeout_seconds"] == 0.25
    assert "webhook_lookup_failed" in caplog.text
    assert "private-exception-body" not in caplog.text


def test_model_arguments_cannot_change_scope_or_url():
    reader = Mock()
    dispatcher = build_tool_dispatcher(reader=Mock(), webhook_reader=reader)
    for extra in ({"tenant_id": "tenant_b"}, {"url": "https://example.com"}, {"token": "private"}):
        with pytest.raises(ValidationError):
            dispatcher.dispatch(
                name="get_webhook_delivery_status",
                arguments={"delivery_id": "id", **extra},
                context=context(),
            )
    reader.read.assert_not_called()
    assert dispatcher.replay_safe("get_webhook_delivery_status")


def test_scripted_agent_loop_uses_real_http_and_observes_result():
    with webhook_server() as (origin, calls):
        dispatcher = build_tool_dispatcher(reader=Mock(), webhook_reader=http_reader(origin))
        provider = ScriptedProvider(
            [
                ToolCallDecision(
                    type="tool_call",
                    name="get_webhook_delivery_status",
                    arguments={"delivery_id": "only_a"},
                ),
                FinalAnswerDecision(
                    type="final_answer", answer="The service reports a succeeded delivery."
                ),
            ]
        )
        result = AgentLoop(provider=provider, dispatcher=dispatcher, max_decisions=2).run(
            messages=[DecisionMessage(role="user", content="Inspect delivery only_a")],
            context=context(),
        )
        assert "succeeded" in result.answer
        observation = json.loads(provider.calls[1][0][-1].content)
        assert observation["result"]["delivery_id"] == "only_a"
        assert observation["result"]["status"] == "succeeded"
        assert len(calls) == 1 and calls[0]["method"] == "GET"


def test_configured_registration_is_opt_in_and_remains_lazy(monkeypatch):
    from crewos.core.config import settings
    from crewos.application.services.tool_registry import UnknownToolError

    monkeypatch.setattr(settings, "WEBHOOK_INTEGRATION_ENABLED", False)
    dispatcher = build_tool_dispatcher(reader=Mock())
    with pytest.raises(UnknownToolError):
        dispatcher.dispatch(
            name="get_webhook_delivery_status", arguments={"delivery_id": "id"}, context=context()
        )
    monkeypatch.setattr(settings, "WEBHOOK_INTEGRATION_ENABLED", True)
    transport = Mock(side_effect=AssertionError("must not construct before authorization"))
    monkeypatch.setattr("crewos.core.webhook_setup.HttpWebhookDeliveryStatusReader", transport)
    dispatcher = build_tool_dispatcher(reader=Mock())
    with pytest.raises(PolicyDeniedError):
        dispatcher.dispatch(
            name="get_webhook_delivery_status",
            arguments={"delivery_id": "id"},
            context=context(permissions=()),
        )
    transport.assert_not_called()


def test_configured_http_reader_works_through_registered_tool(monkeypatch):
    from crewos.core.config import settings

    with webhook_server() as (origin, calls):
        monkeypatch.setattr(settings, "WEBHOOK_INTEGRATION_ENABLED", True)
        monkeypatch.setattr(
            settings,
            "WEBHOOK_HTTP_CONNECTIONS",
            {"tenant_a": HttpWebhookTenantConfig(base_url=origin, token="test-token-a")},
        )
        dispatcher = build_tool_dispatcher(reader=Mock())
        result = dispatcher.dispatch(
            name="get_webhook_delivery_status",
            arguments={"delivery_id": "only_a"},
            context=context(),
        )
        assert result.status == "succeeded"
        assert len(calls) == 1
        with pytest.raises(WebhookServiceUnavailable):
            dispatcher.dispatch(
                name="get_webhook_delivery_status",
                arguments={"delivery_id": "only_a"},
                context=context(tenant="tenant_c"),
            )
        assert len(calls) == 1
        monkeypatch.setattr(settings, "WEBHOOK_INTEGRATION_ENABLED", False)
        with pytest.raises(WebhookServiceUnavailable, match="disabled"):
            dispatcher.dispatch(
                name="get_webhook_delivery_status",
                arguments={"delivery_id": "only_a"},
                context=context(),
            )
        assert len(calls) == 1


def test_complete_scripted_smoke_reports_checked_boundaries():
    from crewos.evaluation.webhook_integration_demo import run_webhook_demo

    report = run_webhook_demo()
    assert report["passed"]
    assert len(report["checks"]) == 10
    assert all(report["checks"].values())
    assert report["decision_source"] == "scripted"
    assert report["http_request_count"] == 4
    assert "test-token-" not in json.dumps(report)


def test_wire_deadline_never_exceeds_caller_or_server_limit(monkeypatch):
    from email.message import Message
    from types import SimpleNamespace

    reader = HttpWebhookDeliveryStatusReader(
        {"tenant_a": HttpWebhookTenantConfig(base_url="https://example.com", token="private")},
        max_timeout_seconds=3,
    )
    headers = Message()
    headers["Content-Type"] = "application/json"
    wire = MagicMock()
    wire.__enter__.return_value = SimpleNamespace(
        status=200,
        headers=headers,
        read=lambda size: json.dumps(
            {
                "tenant_id": "tenant_a",
                "delivery_id": "id",
                "state": "sending",
                "observed_at": "2026-11-06T00:00:00Z",
            }
        ).encode(),
    )
    opener = Mock()
    opener.open.return_value = wire
    reader._opener = opener
    assert (
        reader.read(delivery_id="id", tenant_id="tenant_a", timeout_seconds=0.1).status
        == "in_progress"
    )
    assert opener.open.call_args.kwargs["timeout"] == 0.1
    reader.read(delivery_id="id", tenant_id="tenant_a", timeout_seconds=10)
    assert opener.open.call_args.kwargs["timeout"] == 3
