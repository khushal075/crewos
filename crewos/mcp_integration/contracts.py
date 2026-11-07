"""Normalized MCP discovery, invocation and observation data; no execution authority."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

McpToolName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.-]{1,128}$")]


class McpToolDescription(BaseModel):
    """CrewOS metadata admission contract, independent of an MCP SDK wire model.

    Schemas remain JSON data. This checks an object root declaration and size, not
    JSON Schema semantics, argument validity, safety, or permission. No references
    are resolved. Frozen prevents field reassignment, not nested dict mutation;
    consumers must revalidate/snapshot metadata at later trust boundaries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: McpToolName
    description: str | None = Field(default=None, strict=True, max_length=4096)
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def admit_metadata(self) -> Self:
        if self.description is not None and not self.description.strip():
            raise ValueError("Tool description must contain text when supplied")
        for schema in (self.input_schema, self.output_schema):
            if schema is not None and schema.get("type") != "object":
                raise ValueError("Tool schemas must explicitly declare an object root")
        _admit_json_payload(self.model_dump(mode="python"), max_bytes=65_536, label="Tool metadata")
        return self


def _admit_json_payload(payload: dict, *, max_bytes: int, label: str) -> None:
    """Count serialized JSON bytes; reject non-finite numbers without truncating data."""
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes}-byte admission")


class McpToolCallRequest(BaseModel):
    """Invocation data only; connection identity and authority stay outside arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    tool_name: McpToolName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def admit_call(self) -> Self:
        _admit_json_payload(self.model_dump(mode="python"), max_bytes=32_768, label="Tool call")
        return self


class McpTextContent(BaseModel):
    """One exact, untrusted text block; other MCP content kinds are not supported yet."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    type: Literal["text"] = "text"
    text: str = Field(strict=True, max_length=16_384)


class McpToolCallResult(BaseModel):
    """Normalized observation, not evidence of correctness, ownership or authorization.

    is_error must be populated explicitly by the future SDK adapter. Transport and
    protocol failures must not be fabricated as a successful or unavailable result.
    Content is limited to text blocks and optional structured JSON object data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    is_error: bool = Field(strict=True)
    content: tuple[McpTextContent, ...] = Field(default=(), max_length=16)
    structured_content: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def admit_result(self) -> Self:
        _admit_json_payload(self.model_dump(mode="python"), max_bytes=65_536, label="Tool result")
        return self
