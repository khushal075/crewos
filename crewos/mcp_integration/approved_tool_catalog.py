"""Match untrusted discovery to operator mappings; no execution or permission grant."""

import json
from dataclasses import dataclass

from crewos.mcp_integration.contracts import McpToolDescription
from crewos.mcp_integration.server_configuration import McpServerConfiguration


class McpCatalogError(ValueError):
    """Discovery cannot satisfy configuration; never include remote payloads."""


@dataclass(frozen=True)
class ApprovedMcpCatalogEntry:
    """Immutable metadata snapshot; schema approval and caller authorization remain separate."""

    tenant_id: str
    server_id: str
    local_name: str
    remote_name: str
    required_permission: str
    replay_safe: bool
    _description_json: str

    @property
    def description(self) -> McpToolDescription:
        """Return a fresh object so nested mutation cannot change the catalog."""
        return McpToolDescription.model_validate_json(self._description_json)


class ApprovedMcpToolCatalog:
    """One tenant/server snapshot, containing only explicitly mapped tools.

    This is name admission, not full JSON Schema validation, schema pinning,
    registration or authorization. Consumers must check schema compatibility
    before constructing executable ToolDefinitions and enforce policy per call.
    """

    def __init__(
        self,
        configuration: McpServerConfiguration,
        discovered_tools: tuple[McpToolDescription, ...],
    ) -> None:
        # Python mode preserves non-finite mutations for validation to reject.
        config = McpServerConfiguration.model_validate(configuration.model_dump(mode="python"))
        self._entries: tuple[ApprovedMcpCatalogEntry, ...] = ()
        if not config.enabled:
            return
        if len(discovered_tools) > 128:
            raise McpCatalogError("MCP discovery exceeds tool count limit")
        discovered: dict[str, tuple[McpToolDescription, str]] = {}
        total_bytes = 0
        for candidate in discovered_tools:
            try:
                tool = McpToolDescription.model_validate(candidate.model_dump(mode="python"))
            except ValueError:
                raise McpCatalogError("Invalid MCP tool metadata") from None
            encoded = json.dumps(
                tool.model_dump(mode="python"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            total_bytes += len(encoded.encode("utf-8"))
            if total_bytes > 1_048_576:
                raise McpCatalogError("MCP discovery exceeds metadata size limit")
            if tool.name in discovered:
                raise McpCatalogError("Duplicate discovered MCP tool name")
            discovered[tool.name] = (tool, encoded)
        entries = []
        for mapping in config.allowed_tools:
            if mapping.remote_name not in discovered:
                raise McpCatalogError("Configured MCP tool is missing from discovery")
            _, encoded = discovered[mapping.remote_name]
            entries.append(
                ApprovedMcpCatalogEntry(
                    tenant_id=config.tenant_id,
                    server_id=config.server_id,
                    local_name=mapping.local_name,
                    remote_name=mapping.remote_name,
                    required_permission=mapping.required_permission,
                    replay_safe=mapping.replay_safe,
                    _description_json=encoded,
                )
            )
        self._entries = tuple(entries)

    @property
    def entries(self) -> tuple[ApprovedMcpCatalogEntry, ...]:
        return self._entries

    def resolve(self, local_name: str) -> ApprovedMcpCatalogEntry:
        """Resolve within this catalog's scope; caller must select scope from trusted context."""
        for entry in self._entries:
            if entry.local_name == local_name:
                return entry
        raise McpCatalogError("MCP tool is not admitted in this catalog")
