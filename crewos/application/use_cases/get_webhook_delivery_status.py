"""Authorize and audit a bounded, tenant-scoped read; no delivery mutations."""

import logging
from math import isfinite
from time import monotonic
from pydantic import TypeAdapter, ValidationError
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.dtos.webhook_delivery_status import (
    GetWebhookDeliveryStatusInput,
    GetWebhookDeliveryStatusResult,
    WebhookDeliveryFound,
    WebhookDeliveryUnavailable,
)
from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookDeliveryStatusReaderInterface,
    WebhookResponseInvalid,
)
from crewos.application.services.execution_control import get_execution_control
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.application.services.tool_log_context import get_tool_log_context

_RESULT: TypeAdapter[GetWebhookDeliveryStatusResult] = TypeAdapter(GetWebhookDeliveryStatusResult)


class GetWebhookDeliveryStatusHandler:
    def __init__(
        self,
        *,
        executor: PolicyEnforcedExecutor,
        reader: WebhookDeliveryStatusReaderInterface,
        timeout_seconds=5,
        logger=None,
    ):
        if (
            type(timeout_seconds) not in (int, float)
            or not isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("Invalid webhook handler timeout")
        self._executor, self._reader, self._timeout = executor, reader, timeout_seconds
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def execute(
        self, *, arguments: GetWebhookDeliveryStatusInput, context: ToolExecutionContext
    ) -> GetWebhookDeliveryStatusResult:
        arguments = GetWebhookDeliveryStatusInput.model_validate_json(arguments.model_dump_json())
        context = ToolExecutionContext.model_validate_json(context.model_dump_json())
        return self._executor.execute(
            context=context,
            action="webhook_delivery.read",
            operation=lambda: self._read(arguments=arguments, context=context),
        )

    def _read(
        self, *, arguments: GetWebhookDeliveryStatusInput, context: ToolExecutionContext
    ) -> GetWebhookDeliveryStatusResult:
        started = monotonic()
        fields: dict[str, object] = {
            "tenant_id": context.tenant_id,
            "delivery_id": arguments.delivery_id,
        }
        correlation = get_tool_log_context()
        if correlation is not None:
            fields.update(
                agent_run_id=correlation.agent_run_id,
                tool_call_id=correlation.tool_call_id,
                step=correlation.step,
            )
        self._logger.info({"event": "webhook_lookup_started", **fields})
        try:
            control = get_execution_control()
            timeout = (
                min(self._timeout, control.tool_remaining())
                if control is not None
                else self._timeout
            )
            raw = self._reader.read(
                delivery_id=arguments.delivery_id,
                tenant_id=context.tenant_id,
                timeout_seconds=timeout,
            )
            if control is not None:
                control.tool_remaining()
            if not isinstance(raw, (WebhookDeliveryFound, WebhookDeliveryUnavailable)):
                raise WebhookResponseInvalid("Webhook adapter returned an invalid result type")
            try:
                result = _RESULT.validate_json(raw.model_dump_json())
            except ValidationError:
                raise WebhookResponseInvalid("Webhook adapter returned an invalid result") from None
            if result.delivery_id != arguments.delivery_id:
                raise WebhookResponseInvalid(
                    "Webhook adapter returned a different delivery identity"
                )
        except Exception as error:
            self._logger.error(
                {
                    "event": "webhook_lookup_failed",
                    **fields,
                    "error_type": type(error).__name__,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                }
            )
            raise
        self._logger.info(
            {
                "event": "webhook_lookup_completed",
                **fields,
                "outcome": result.outcome.value,
                "status": result.status.value if isinstance(result, WebhookDeliveryFound) else None,
                "duration_ms": round((monotonic() - started) * 1000, 3),
            }
        )
        return result
