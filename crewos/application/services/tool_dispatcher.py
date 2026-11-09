"""Route registered tool calls after validating model-supplied arguments."""

from pydantic import BaseModel

from crewos.application.dtos.decision_request import ToolSchema
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.action_approval import ActionApprovalGate
from crewos.application.services.tool_registry import ToolRegistry


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry, *, approval_gate: ActionApprovalGate | None = None):
        self._registry = registry
        self._approval_gate = approval_gate

    def tool_schemas(self) -> tuple[ToolSchema, ...]:
        """Describe the same registrations used by dispatch; grant no access."""
        return self._registry.tool_schemas()

    def replay_safe(self, name: str) -> bool:
        return self._registry.get(name).replay_safe

    def dispatch(
        self,
        *,
        name: str,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> BaseModel:
        """Context must be established by trusted code, not tool arguments.

        Registered handlers remain responsible for authorization. Unknown tools,
        invalid arguments, and handler failures propagate to the caller.
        """
        tool = self._registry.get(name)
        validated_arguments = tool.argument_schema.model_validate(arguments)
        if tool.approval_permission is not None:
            if self._approval_gate is None:
                raise RuntimeError("Approval gate is unavailable")
            return self._approval_gate.propose(
                tool=tool, arguments=validated_arguments, context=context
            )
        return tool.handler(arguments=validated_arguments, context=context)
