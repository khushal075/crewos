from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, SecretStr


class Settings(BaseSettings):
    # App
    APP_NAME: str = "crewai_platform"
    REDIS_BROKER_URL: str = ""
    REDIS_RESULT_BACKEND: str = ""

    CELERY_QUEUE_NAME: str = "crewai"
    CELERY_CONCURRENCY: int = 10

    # LLM
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = "llama3"
    API_KEY: str = ""

    # Tenant-scoped API tokens. No configured token means access is denied.
    TENANT_API_KEYS: dict[str, SecretStr] = Field(default_factory=dict)
    TASK_OWNER_TTL: int = Field(default=86400, ge=10800)
    TASK_RESULT_TTL: int = Field(default=3600, ge=60, le=3600)
    TASK_QUEUE_TTL: int = Field(default=3600, ge=60, le=3600)
    LLM_TIMEOUT: float = Field(default=30, gt=0, le=120)
    LLM_MAX_RETRIES: int = Field(default=3, ge=0, le=5)

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
