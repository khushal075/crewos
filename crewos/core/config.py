from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, SecretStr, StringConstraints, model_validator
from decimal import Decimal
from typing import Annotated, Literal
from crewos.application.dtos.tool_execution_context import Permission
from crewos.mcp_integration.server_configuration import McpServerConfigurations
from crewos.third_party.webhooks.http_delivery_config import HttpWebhookTenantConfig


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
    AGENT_ACTIONS_ENABLED: bool = False
    AGENT_ACTION_DB_PATH: str = "var/agent_actions.sqlite3"
    AGENT_ACTION_APPROVAL_TTL: int = Field(default=900, ge=1, le=86400)
    AGENT_REVIEWER_KEYS: dict[str, SecretStr] = Field(default_factory=dict)
    API_KEY: str = ""

    # Existing search remains BM25 until another retrieval mode is explicitly selected.
    RETRIEVAL_STRATEGY: Literal["bm25", "vector", "hybrid", "indexed_bm25"] = "bm25"
    HYBRID_CANDIDATE_K: int = Field(default=20, ge=1, le=20)
    HYBRID_RRF_RANK_CONSTANT: int = Field(default=60, ge=1, le=10000)
    RERANKING_ENABLED: bool = False
    RERANKING_MODEL: str | None = Field(default=None, min_length=1, max_length=256)
    RERANKING_CANDIDATE_K: int = Field(default=20, ge=1, le=20)
    EMBEDDING_DB_PATH: str = "var/embeddings.sqlite3"
    EMBEDDING_DOCUMENT_PREFIX: str = ""
    EMBEDDING_QUERY_PREFIX: str = ""
    VECTOR_MAX_CHUNKS: int = Field(default=512, ge=1, le=4096)
    VECTOR_MAX_EMBEDDING_BYTES: int = Field(default=32_000_000, ge=1024, le=128_000_000)
    VECTOR_MAX_INPUT_BYTES: int = Field(default=262144, ge=1024, le=4_000_000)

    # Native embedding endpoint; separate from the chat /v1 endpoint. Disabled until configured.
    EMBEDDING_BASE_URL: str = "http://localhost:11434"
    EMBEDDING_MODEL: str | None = None
    EMBEDDING_MODEL_REVISION: str | None = None
    EMBEDDING_DIMENSIONS: int | None = Field(default=None, ge=1, le=16384)
    EMBEDDING_TIMEOUT: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)

    # Read-only external webhook integration, unavailable until explicitly enabled.
    WEBHOOK_INTEGRATION_ENABLED: bool = False
    MCP_WEBHOOK_ENABLED: bool = False
    MCP_WEBHOOK_SERVERS: McpServerConfigurations = Field(default_factory=McpServerConfigurations)
    WEBHOOK_HTTP_CONNECTIONS: dict[
        Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        HttpWebhookTenantConfig,
    ] = Field(default_factory=dict)
    WEBHOOK_TIMEOUT_SECONDS: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    WEBHOOK_MAX_RESPONSE_BYTES: int = Field(default=16384, ge=512, le=65536)

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
    DOCUMENT_REVIEW_MODEL: str | None = Field(default=None, min_length=1, max_length=256)
    DOCUMENT_JOB_LEASE_SECONDS: float = Field(default=180, gt=150, allow_inf_nan=False)
    DOCUMENT_JOB_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    DOCUMENT_MAX_REQUEST_BYTES: int = Field(default=8_100_000, ge=1024, le=16_000_000)

    # Internal cleanup service scope; independent of revoked user/API credentials.
    DOCUMENT_CLEANUP_TENANTS: frozenset[
        Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    ] = frozenset()
    DOCUMENT_CLEANUP_LEASE_SECONDS: float = Field(default=30, ge=10, le=300, allow_inf_nan=False)
    DOCUMENT_CLEANUP_RETRY_BASE_SECONDS: float = Field(
        default=10, gt=0, le=86400, allow_inf_nan=False
    )
    DOCUMENT_CLEANUP_RETRY_MAX_SECONDS: float = Field(
        default=300, gt=0, le=86400, allow_inf_nan=False
    )
    DOCUMENT_CLEANUP_POLL_SECONDS: float = Field(default=2, ge=0.1, le=60, allow_inf_nan=False)
    DOCUMENT_CLEANUP_BATCH_SIZE: int = Field(default=20, ge=1, le=100)
    DOCUMENT_CLEANUP_ALERT_AFTER_ATTEMPTS: int = Field(default=5, ge=1, le=1000000)

    @model_validator(mode="after")
    def cleanup_retry_bounds(self):
        if self.DOCUMENT_CLEANUP_RETRY_BASE_SECONDS > self.DOCUMENT_CLEANUP_RETRY_MAX_SECONDS:
            raise ValueError("Cleanup retry base cannot exceed its maximum")
        return self

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
        hide_input_in_errors=True,  # Connection validation must not echo secret-bearing inputs.
        extra="ignore",  # <- This allows unexpected keys like log_level
    )


# Create singleton instance
settings = Settings()
