from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, SecretStr, model_validator
from decimal import Decimal
from crewos.application.dtos.tool_execution_context import Permission


class Settings(BaseSettings):
    # App
    APP_NAME: str = "crewai_platform"
    REDIS_BROKER_URL: str = ""
    REDIS_RESULT_BACKEND: str = ""
    REDIS_OPERATION_TIMEOUT: float = Field(default=2.0, gt=0, le=30)

    CELERY_QUEUE_NAME: str = "crewai"
    CELERY_CONCURRENCY: int = 10

    # LLM
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = "llama3"
    API_KEY: str = ""

    # Tenant-scoped API tokens. No configured token means access is denied.
    TENANT_API_KEYS: dict[str, SecretStr] = Field(default_factory=dict)
    TENANT_PERMISSIONS: dict[str, frozenset[Permission]] = Field(default_factory=dict)
    TASK_OWNER_TTL: int = Field(default=86400, ge=10800)
    TASK_RESULT_TTL: int = Field(default=3600, ge=60, le=3600)
    TASK_QUEUE_TTL: int = Field(default=3600, ge=60, le=3600)
    LLM_TIMEOUT: float = Field(default=30, gt=0, le=120)
    LLM_MAX_RETRIES: int = Field(default=3, ge=0, le=5)

    # Single-host document lifecycle and durable submissions.
    DOCUMENT_DB_PATH: str = "var/documents.sqlite3"
    DOCUMENT_JOB_LEASE_SECONDS: float = Field(default=180, gt=150, allow_inf_nan=False)
    DOCUMENT_JOB_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    DOCUMENT_MAX_REQUEST_BYTES: int = Field(default=8_100_000, ge=1024, le=16_000_000)

    # Single-host durable agent runtime. Shared multi-host storage is a separate adapter.
    AGENT_RUN_DB_PATH: str = "var/agent_runs.sqlite3"
    AGENT_LEASE_SECONDS: float = Field(default=45, gt=0, allow_inf_nan=False)
    AGENT_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    AGENT_MAX_DURATION: float = Field(default=120, gt=0, le=180)
    AGENT_MAX_DECISIONS: int = Field(default=20, ge=1, le=100)
    AGENT_MAX_TOKENS: int = Field(default=100000, ge=1)
    AGENT_MAX_OUTPUT_TOKENS: int = Field(default=512, ge=1, le=8192)
    AGENT_MAX_RECOVERIES: int = Field(default=1, ge=0, le=3)
    AGENT_MAX_COST_USD: Decimal | None = Field(default=None, ge=0)
    AGENT_INPUT_USD_PER_MILLION: Decimal | None = Field(default=None, ge=0)
    AGENT_OUTPUT_USD_PER_MILLION: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def cost_rates_required(self):
        if (self.AGENT_INPUT_USD_PER_MILLION is None) != (
            self.AGENT_OUTPUT_USD_PER_MILLION is None
        ):
            raise ValueError("Both input/output cost rates must be configured")
        if self.AGENT_MAX_COST_USD is not None and (
            self.AGENT_INPUT_USD_PER_MILLION is None or self.AGENT_OUTPUT_USD_PER_MILLION is None
        ):
            raise ValueError("Cost budgets require configured input/output rates")
        return self

    # Default tenant
    DEFAULT_TENANT_ID: int = 1
    TENANT_ID: str = "1"  # Set per worker pod in K8s: TENANT_ID=tenant_a

    # Logging level
    LOGGING_LEVEL: str = "INFO"

    # Pydantic v2: allow extra env variables
    model_config = ConfigDict(
        env_file=("k8crew.env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",  # <- This allows unexpected keys like log_level
    )


# Create singleton instance
settings = Settings()
