"""Trusted configuration for an OpenAI-compatible secondary endpoint."""

from decimal import Decimal
from urllib.parse import urlsplit

from openai import APIConnectionError, APIStatusError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class FallbackModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    base_url: str = Field(strict=True, min_length=1, max_length=2048)
    model: str = Field(strict=True, min_length=1, max_length=256)
    api_key: SecretStr
    primary_timeout_seconds: float = Field(default=10, gt=0, le=120, allow_inf_nan=False)
    input_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        parts = urlsplit(value)
        if (
            any(ord(c) <= 32 or ord(c) >= 127 or c in "\\\\?#" for c in value)
            or not parts.hostname
            or parts.username is not None
            or "%" in parts.netloc
            or (
                parts.scheme != "https"
                and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "::1"})
            )
        ):
            raise ValueError("Fallback endpoint must use HTTPS or literal loopback HTTP")
        return value

    @field_validator("api_key")
    @classmethod
    def credential(cls, value):
        token = value.get_secret_value()
        if not token or len(token) > 4096 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError("Invalid fallback credential")
        return value

    @model_validator(mode="after")
    def paired_rates(self):
        if (self.input_usd_per_million is None) != (self.output_usd_per_million is None):
            raise ValueError("Fallback pricing requires both rates")
        return self


def eligible_provider_failure(error: Exception) -> bool:
    if isinstance(error, APIConnectionError):
        return True
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 429, 500, 502, 503, 504}
    return False
