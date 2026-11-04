"""Constrain document-mode proposals to registered tool names and argument shapes."""

from copy import deepcopy
from collections.abc import Sequence
from crewos.application.dtos.decision_request import ToolSchema


def build_document_decision_schema(*, decision_schema: dict, tools: Sequence[ToolSchema]) -> dict:
    schema = deepcopy(decision_schema)
    if not tools:
        schema["oneOf"] = [
            item for item in schema["oneOf"] if item.get("$ref") != "#/$defs/ToolCallDecision"
        ]
        schema["discriminator"]["mapping"].pop("tool_call", None)
        return schema
    variants = []
    definitions = schema.setdefault("$defs", {})
    for number, tool in enumerate(tools):
        arguments = deepcopy(tool.parameters)
        prefix = f"RegisteredTool{number}_"

        def relocate(value):
            if isinstance(value, dict):
                return {
                    key: (
                        item.replace("#/$defs/", f"#/$defs/{prefix}", 1)
                        if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/")
                        else relocate(item)
                    )
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [relocate(item) for item in value]
            return value

        nested_definitions = arguments.pop("$defs", {})
        for name, definition in nested_definitions.items():
            definitions[prefix + name] = relocate(definition)
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"const": "tool_call", "type": "string"},
                    "name": {"const": tool.name, "type": "string"},
                    "arguments": relocate(arguments),
                },
                "required": ["type", "name", "arguments"],
            }
        )
    definitions["ToolCallDecision"] = {"oneOf": variants}
    return schema
