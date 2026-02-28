from pydantic_settings import BaseSettings
from pydantic import ConfigDict

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

    # Default tenant
    DEFAULT_TENANT_ID: int = 1
    TENANT_ID: str = "1"  # Set per worker pod in K8s: TENANT_ID=tenant_a


    # Logging level
    LOGGING_LEVEL: str = "INFO"

    # Pydantic v2: allow extra env variables
    model_config = ConfigDict(
        env_file="k8crew.env",
        env_file_encoding="utf-8",
        extra="ignore"   # <- This allows unexpected keys like log_level
    )

# Create singleton instance
settings = Settings()