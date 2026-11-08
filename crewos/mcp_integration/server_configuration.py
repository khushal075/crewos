"""Trusted operator configuration, never accepted from model/server descriptions."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from crewos.mcp_integration.contracts import McpToolName

McpConfigurationId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
]
McpLocalToolName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
McpRequiredPermission = Annotated[
    str,
    StringConstraints(
        strict=True, max_length=128, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
    ),
]
McpEnvironmentName = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
]
McpProcessArgument = Annotated[str, StringConstraints(strict=True, max_length=2048)]


class McpStdioTransport(BaseModel):
    """Local executable and literal arguments; no process is started by this model.

    'stdio' means a future client exchanges protocol messages through a child
    process's stdin/stdout. Running configured executables requires operator trust.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kind: Literal["stdio"] = "stdio"
    command: str = Field(strict=True, min_length=1, max_length=2048)
    args: tuple[McpProcessArgument, ...] = Field(default=(), max_length=64)
    cwd: str | None = Field(default=None, strict=True, min_length=1, max_length=2048)
    environment: dict[McpEnvironmentName, SecretStr] = Field(default_factory=dict, max_length=32)

    @field_validator("command", "cwd")
    @classmethod
    def absolute_path(cls, value):
        if value is not None and ("\x00" in value or not Path(value).is_absolute()):
            raise ValueError("MCP executable and working directory must use absolute host paths")
        return value

    @field_validator("args")
    @classmethod
    def literal_arguments(cls, args):
        if any("\x00" in value for value in args):
            raise ValueError("MCP arguments cannot contain NUL")
        return args

    @field_validator("environment")
    @classmethod
    def explicit_environment(cls, environment):
        if any(
            "\x00" in value.get_secret_value() or len(value.get_secret_value()) > 4096
            for value in environment.values()
        ):
            raise ValueError("MCP environment values cannot contain NUL or exceed 4096 characters")
        return environment


class ApprovedMcpTool(BaseModel):
    """An operator's explicit mapping, not a grant derived from discovery metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    remote_name: McpToolName
    local_name: McpLocalToolName
    required_permission: McpRequiredPermission
    replay_safe: bool = Field(default=False, strict=True)


class McpServerConfiguration(BaseModel):
    """A tenant-scoped local server definition; defaults expose no tools.

    This does not establish authenticated identity or enforce permissions. Only
    trusted application configuration should construct/use it for connections.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    server_id: McpConfigurationId
    tenant_id: McpConfigurationId
    transport: McpStdioTransport
    enabled: bool = Field(default=False, strict=True)
    allowed_tools: tuple[ApprovedMcpTool, ...] = Field(default=(), max_length=16)
    timeout_seconds: float = Field(default=5, strict=True, gt=0, le=30, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_tool_mappings(self) -> Self:
        remote = [tool.remote_name for tool in self.allowed_tools]
        local = [tool.local_name for tool in self.allowed_tools]
        if len(set(remote)) != len(remote) or len(set(local)) != len(local):
            raise ValueError("Each server must have unique remote names and local aliases")
        return self


class McpServerConfigurations(BaseModel):
    """Bounded configuration set; identities and aliases are scoped per tenant."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    servers: tuple[McpServerConfiguration, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_scoped_configuration(self) -> Self:
        identities = [(server.tenant_id, server.server_id) for server in self.servers]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate MCP server identity within a tenant")
        aliases = [
            (server.tenant_id, tool.local_name)
            for server in self.servers
            for tool in server.allowed_tools
        ]
        if len(set(aliases)) != len(aliases):
            raise ValueError("MCP local tool aliases must be unique within a tenant")
        return self
