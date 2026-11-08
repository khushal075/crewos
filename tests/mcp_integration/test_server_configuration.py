"""Server configuration is explicit data, with no process/network/permission effects."""

import pytest
from pydantic import ValidationError

from crewos.mcp_integration.server_configuration import (
    ApprovedMcpTool,
    McpStdioTransport,
    McpServerConfiguration,
    McpServerConfigurations,
)


def approved(**changes):
    return ApprovedMcpTool.model_validate(
        {
            "remote_name": "read_delivery",
            "local_name": "get_webhook_delivery_status",
            "required_permission": "webhook_delivery.read",
            **changes,
        }
    )


def server(**changes):
    return McpServerConfiguration.model_validate(
        {
            "server_id": "webhook_local",
            "tenant_id": "tenant_a",
            "transport": {"command": "/usr/bin/python3", "args": ["server.py"]},
            **changes,
        }
    )


def test_defaults_are_disabled_and_expose_no_tools_or_parent_environment():
    config = server()
    assert config.enabled is False and config.allowed_tools == ()
    assert config.transport.environment == {}
    assert config.timeout_seconds == 5
    assert McpServerConfigurations().servers == ()
    assert approved().replay_safe is False


def test_explicit_mapping_and_literal_command_arguments_are_preserved():
    config = server(
        enabled=True,
        allowed_tools=(approved(),),
        transport={
            "command": "/opt/My App/python",
            "args": ["-m", "webhook_mcp_server.server", "$(echo literal)", ";literal"],
            "cwd": "/opt/server",
        },
    )
    assert config.allowed_tools[0].remote_name == "read_delivery"
    assert config.allowed_tools[0].required_permission == "webhook_delivery.read"
    assert config.transport.args[-2:] == ("$(echo literal)", ";literal")
    assert McpServerConfiguration.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError):
        config.enabled = True


@pytest.mark.parametrize(
    "values",
    [
        {"command": "python3"},
        {"command": "~/bin/python"},
        {"command": "/bin/python\x00"},
        {"command": "/bin/python", "cwd": "relative"},
        {"command": "/bin/python", "args": ["bad\x00arg"]},
        {"command": "/bin/python", "args": "-m server"},
        {"kind": "streamable_http", "command": "/bin/python"},
    ],
)
def test_invalid_paths_arguments_and_unsupported_transport_are_rejected(values):
    with pytest.raises(ValidationError):
        McpStdioTransport.model_validate(values)


def test_environment_is_explicit_bounded_and_redacted_in_repr():
    transport = McpStdioTransport(
        command="/bin/python",
        environment={"SERVICE_TOKEN": "private-token", "TENANT_ID": "tenant_a"},
    )
    assert transport.environment["SERVICE_TOKEN"].get_secret_value() == "private-token"
    assert "private-token" not in repr(transport)
    assert "private-token" not in transport.model_dump_json()
    # Trusted Python-mode copies preserve SecretStr values; redacted JSON is not a secret backup.
    restored = McpStdioTransport.model_validate(transport.model_dump())
    assert restored.environment["SERVICE_TOKEN"].get_secret_value() == "private-token"


@pytest.mark.parametrize(
    "environment",
    [
        {"BAD=KEY": "x"},
        {"BAD-KEY": "x"},
        {"KEY": "a\x00b"},
        {"KEY": "x" * 4097},
        {f"K{i}": "x" for i in range(33)},
    ],
)
def test_invalid_environment_is_rejected_without_echoing_inputs(environment):
    with pytest.raises(ValidationError) as error:
        McpStdioTransport(command="/bin/python", environment=environment)
    assert "input_value=" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"remote_name": "*"},
        {"local_name": "*"},
        {"local_name": "has space"},
        {"required_permission": ""},
        {"required_permission": "*"},
        {"required_permission": "admin role"},
        {"replay_safe": "true"},
    ],
)
def test_allowlist_has_no_wildcards_or_implicit_safety(changes):
    with pytest.raises(ValidationError):
        approved(**changes)


def test_mapping_requires_an_explicit_permission():
    with pytest.raises(ValidationError):
        ApprovedMcpTool(remote_name="read_delivery", local_name="read_delivery")


def test_duplicate_remote_names_and_local_aliases_are_rejected():
    for other in (approved(local_name="another_alias"), approved(remote_name="different_remote")):
        with pytest.raises(ValidationError):
            server(allowed_tools=(approved(), other))


def test_same_server_and_alias_names_can_be_used_by_different_tenants():
    a = server(allowed_tools=(approved(),))
    b = server(tenant_id="tenant_b", allowed_tools=(approved(),))
    assert len(McpServerConfigurations(servers=(a, b)).servers) == 2


def test_duplicate_server_identity_or_alias_within_tenant_fails():
    a = server(allowed_tools=(approved(),))
    with pytest.raises(ValidationError):
        McpServerConfigurations(servers=(a, server()))
    with pytest.raises(ValidationError):
        McpServerConfigurations(
            servers=(a, server(server_id="second", allowed_tools=(approved(),)))
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant/a"},
        {"server_id": "*"},
        {"enabled": 1},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": True},
        {"timeout_seconds": 31},
        {"permission_granted": True},
    ],
)
def test_scope_and_timeout_admission(changes):
    with pytest.raises(ValidationError):
        server(**changes)


def test_schema_annotations_cannot_be_used_as_operator_configuration():
    with pytest.raises(ValidationError):
        approved(readOnlyHint=True)
    with pytest.raises(ValidationError):
        server(annotations={"readOnlyHint": True})
