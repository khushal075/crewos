"""Route registered tool calls after validating model-supplied arguments."""

from pydantic import BaseModel

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.tool_registry import ToolRegistry


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

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
        return tool.handler(arguments=validated_arguments, context=context)
