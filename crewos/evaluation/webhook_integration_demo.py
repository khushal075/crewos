"""End-to-end webhook demo: loopback HTTP, isolated contexts, optional local model."""

import argparse
from datetime import datetime, timezone
import io
import json
import logging
import sys
from time import monotonic

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision, ToolCallDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.decision_provider import DecisionProviderInterface
from crewos.application.interfaces.webhook_delivery_status_reader import WebhookServiceUnavailable
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.use_cases.agent_loop import AgentLoop
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.evaluation.webhook_reference_service import webhook_server
from crewos.infrastructure.logging import JSONFormatter
from crewos.third_party.webhooks.http_delivery_config import HttpWebhookTenantConfig
from crewos.third_party.webhooks.http_delivery_reader import HttpWebhookDeliveryStatusReader


class _UnusedTaskReader:
    def read(self, **kwargs):
        raise AssertionError("This demo must not read task state")


class _ScriptedDecisions(DecisionProviderInterface):
    def __init__(self):
        self.count = 0

    def decide(self, *, messages, tools, timeout_seconds):
        self.count += 1
        if self.count == 1:
            return ToolCallDecision(
                type="tool_call",
                name="get_webhook_delivery_status",
                arguments={"delivery_id": "only_a"},
            )
        return FinalAnswerDecision(
            type="final_answer",
            answer="The service reports delivery succeeded; this does not prove downstream business processing.",
        )


class _CountingDecisions(DecisionProviderInterface):
    def __init__(self, provider):
        self.provider = provider
        self.tool_names = []
        self.observation_seen = False

    def decide(self, *, messages, tools, timeout_seconds):
        self.observation_seen |= any(m.role == "tool" for m in messages)
        decision = self.provider.decide(
            messages=messages, tools=tools, timeout_seconds=timeout_seconds
        )
        if isinstance(decision, ToolCallDecision):
            self.tool_names.append(decision.name)
        return decision


def run_webhook_demo(*, decision_provider=None, model_id=None):
    started = monotonic()
    checks = {}
    logs = io.StringIO()
    capture = logging.StreamHandler(logs)
    capture.setFormatter(JSONFormatter())
    # Reserve stdout for the report; capture metadata for privacy/correlation checks.
    loggers = [logging.getLogger(name) for name in ("crewos.webhook", "crewos.policy")]
    # Create the dispatcher once inside the server block before attaching capture,
    # because its factory configures the named loggers on first use.
    with webhook_server() as (origin, calls):
        reader = HttpWebhookDeliveryStatusReader(
            {
                tenant: HttpWebhookTenantConfig(base_url=origin, token=token)
                for tenant, token in (("tenant_a", "test-token-a"), ("tenant_b", "test-token-b"))
            }
        )
        dispatcher = build_tool_dispatcher(reader=_UnusedTaskReader(), webhook_reader=reader)
        streams = []
        for logger in loggers:
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler):
                    streams.append((handler, handler.stream))
                    handler.setStream(sys.stderr)
            logger.addHandler(capture)
        context = ToolExecutionContext(
            tenant_id="tenant_a", permissions=frozenset({"webhook_delivery.read"})
        )
        try:
            provider = _CountingDecisions(
                decision_provider if decision_provider is not None else _ScriptedDecisions()
            )
            result = AgentLoop(
                provider=provider, dispatcher=dispatcher, max_decisions=4, max_duration_seconds=90
            ).run(
                messages=[
                    DecisionMessage(
                        role="user",
                        content="Read webhook delivery only_a using get_webhook_delivery_status. Call that tool before answering. Report its observed state and acknowledge that delivery success does not prove business processing. Do not call other tools.",
                    )
                ],
                context=context,
            )
            checks["agent_selected_registered_tool"] = provider.tool_names == [
                "get_webhook_delivery_status"
            ]
            checks["tool_observation_reached_next_decision"] = provider.observation_seen
            checks["agent_used_requested_external_delivery"] = len(calls) == 1 and calls[0][
                "path"
            ].endswith("/tenant_a/deliveries/only_a")
            before = len(calls)
            denied = False
            try:
                dispatcher.dispatch(
                    name="get_webhook_delivery_status",
                    arguments={"delivery_id": "only_a"},
                    context=ToolExecutionContext(tenant_id="tenant_a"),
                )
            except PolicyDeniedError:
                denied = True
            checks["denial_before_http"] = denied and len(calls) == before
            other = dispatcher.dispatch(
                name="get_webhook_delivery_status",
                arguments={"delivery_id": "only_a"},
                context=context.model_copy(update={"tenant_id": "tenant_b"}),
            )
            missing = dispatcher.dispatch(
                name="get_webhook_delivery_status",
                arguments={"delivery_id": "missing"},
                context=context.model_copy(update={"tenant_id": "tenant_b"}),
            )
            checks["other_tenant_and_missing_unavailable"] = (
                other.outcome == missing.outcome == "unavailable"
            )
            checks["other_tenant_uses_own_credential"] = (
                calls[-1]["authorization"] == "Bearer test-token-b"
            )
            failed = False
            try:
                dispatcher.dispatch(
                    name="get_webhook_delivery_status",
                    arguments={"delivery_id": "error503"},
                    context=context,
                )
            except WebhookServiceUnavailable:
                failed = True
            checks["upstream_failure_not_missing_record"] = failed
            checks["http_read_only"] = all(call["method"] == "GET" for call in calls)
            text = logs.getvalue()
            checks["correlated_tool_logs"] = all(
                key in text for key in ("agent_run_id", "tool_call_id", "webhook_lookup_completed")
            )
            checks["logs_exclude_credentials_and_response_bodies"] = all(
                value not in text
                for value in ("test-token-a", "test-token-b", "must-never-be-logged")
            )
        finally:
            for logger in loggers:
                logger.removeHandler(capture)
            for handler, stream in streams:
                handler.setStream(stream)
    return {
        "check": "webhook_integration_end_to_end",
        "passed": all(checks.values()),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision_source": "local_model" if decision_provider is not None else "scripted",
        "model_id": model_id,
        "checks": checks,
        "http_request_count": len(calls),
        "elapsed_seconds": round(monotonic() - started, 6),
        "final_answer": result.answer,
        "scope": "Real loopback HTTP implementing the documented contract, synthetic tenant contexts and development-only tokens. No API authentication or third-party vendor connection is proven. No existing tenant data, permissions or private environment file is changed. Final-answer truthfulness beyond observing tool use is not evaluated by these checks.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", help="Optional already-installed model; otherwise use scripted decisions"
    )
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    args = parser.parse_args(argv)
    provider = None
    if args.model:
        from langchain_openai import ChatOpenAI
        from crewos.third_party.llm.json_decision_adapter import JsonDecisionAdapter

        provider = JsonDecisionAdapter(
            ChatOpenAI(
                base_url=args.base_url,
                api_key="local-demo",
                model=args.model,
                temperature=0,
                max_retries=0,
            ),
            max_call_timeout_seconds=30,
        )
    report = run_webhook_demo(decision_provider=provider, model_id=args.model)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
