from celery import Celery
from crewos.core.config import settings

celery_app = Celery(
    "crewai",
    broker=settings.REDIS_BROKER_URL,
    backend=settings.REDIS_RESULT_BACKEND,  # <-- add this
    include=["crewos.workers.crew_task"],
)


celery_app.autodiscover_tasks(["crewos.workers"])

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_acks_late=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    result_expires=settings.TASK_RESULT_TTL,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_soft_time_limit=240,
    task_time_limit=270,
)

celery_app.conf.task_default_queue = settings.CELERY_QUEUE_NAME
celery_app.conf.worker_concurrency = settings.CELERY_CONCURRENCY
