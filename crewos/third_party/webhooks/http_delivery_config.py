"""Trusted tenant connection settings; no model-supplied URLs or credentials."""

from ipaddress import ip_address
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class HttpWebhookTenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    base_url: str = Field(strict=True, min_length=1, max_length=2048)
    token: SecretStr

    @field_validator("base_url")
    @classmethod
    def validate_origin(cls, value):
        try:
            parts = urlsplit(value)
            port = parts.port
            hostname = parts.hostname
        except ValueError:
            raise ValueError("Invalid webhook origin") from None
        if (
            parts.scheme not in {"https", "http"}
            or not hostname
            or port == 0
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError(
                "Webhook endpoint must be an origin without credentials, path or query"
            )
        if parts.scheme == "http":
            try:
                local = ip_address(hostname).is_loopback
            except ValueError:
                local = hostname == "localhost"
            if not local:
                raise ValueError("Webhook credentials require HTTPS outside loopback development")
        return value.rstrip("/")

    @field_validator("token")
    @classmethod
    def validate_token(cls, token):
        value = token.get_secret_value()
        if not 1 <= len(value) <= 4096 or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError(
                "Webhook bearer token must be nonempty printable ASCII without whitespace"
            )
        return token
