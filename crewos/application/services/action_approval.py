"""Approval gate for trusted tool registrations that can change state."""

import json
from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.tool_log_context import get_tool_log_context


class ApprovalUnavailable(LookupError):
    """Missing or another tenant's action."""


class ApprovalConflict(RuntimeError):
    """The action is expired or cannot transition from its current state."""


class ApprovalRequested(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["approval_required"] = "approval_required"
    approval_id: UUID


def canonical_arguments(arguments: BaseModel) -> str:
    encoded = json.dumps(
        arguments.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > 32768:
        raise ValueError("Approved tool arguments exceed size limit")
    return encoded


class ActionApprovalStore(Protocol):
    def propose(self, *, tenant, tool, version, payload, run_id): ...
    def get(self, approval_id, tenant): ...
    def claim(self, approval_id, tenant): ...
    def finish(self, approval_id, tenant, *, state): ...


class ActionApprovalGate:
    def __init__(self, *, store: ActionApprovalStore, executor, logger=None):
        self.store, self.executor, self.logger = store, executor, logger

    def propose(self, *, tool, arguments: BaseModel, context: ToolExecutionContext):
        correlation = get_tool_log_context()

        def record():
            approval_id = self.store.propose(
                tenant=context.tenant_id,
                tool=tool.name,
                version=tool.approval_version,
                payload=canonical_arguments(arguments),
                run_id=correlation.agent_run_id if correlation else None,
            )
            self._log("agent_action_proposed", approval_id, context.tenant_id)
            return ApprovalRequested(approval_id=approval_id)

        return self.executor.execute(
            context=context, action=tool.approval_permission, operation=record
        )

    def execute(
        self,
        *,
        approval_id: str,
        context: ToolExecutionContext,
        resolve: Callable,
    ) -> BaseModel:
        row = self.store.get(approval_id, context.tenant_id)
        tool = resolve(row["tool"])
        if (
            tool.name != row["tool"]
            or not tool.approval_permission
            or tool.approval_version != row["version"]
        ):
            raise ApprovalConflict("Approved action no longer matches tool registration")
        arguments = tool.argument_schema.model_validate_json(row["payload"])
        if canonical_arguments(arguments) != row["payload"]:
            raise ApprovalConflict("Approved arguments no longer match tool contract")

        def consume_and_invoke():
            self.store.claim(approval_id, context.tenant_id)
            self._log("agent_action_claimed", approval_id, context.tenant_id)
            try:
                result = tool.handler(arguments=arguments, context=context)
                if not isinstance(result, BaseModel):
                    raise TypeError("Approved tool did not return a validated result")
            except Exception:
                self.store.finish(approval_id, context.tenant_id, state="uncertain")
                self._log("agent_action_uncertain", approval_id, context.tenant_id)
                raise
            self.store.finish(approval_id, context.tenant_id, state="completed")
            self._log("agent_action_completed", approval_id, context.tenant_id)
            return result

        return self.executor.execute(
            context=context, action=tool.approval_permission, operation=consume_and_invoke
        )

    def _log(self, event, approval_id, tenant):
        if self.logger:
            self.logger.info({"event": event, "approval_id": str(approval_id), "tenant_id": tenant})
