import os
import sys
import signal
from crewos.core.config import settings
from crewos.infrastructure.logging import get_logger

logger = get_logger(__name__)


def handle_sigterm(*args):
    """
    Called when Kubernetes sends SIGTERM to stop the pod.
    Logs the shutdown event then exits cleanly — Celery's
    task_acks_late=True ensures the current task finishes
    before the process actually stops.
    """
    logger.info({
        "event": "worker_shutdown_initiated",
        "signal": "SIGTERM",
        "reason": "Kubernetes pod termination"
    })
    sys.exit(0)


def main():
    """
    Poetry entry-point for celery worker.
    :return:
    """
    os.environ.setdefault("C_FORCE_ROOT", "true")

    # Register BEFORE starting worker — must be in place
    # from the first moment the process is alive
    signal.signal(signal.SIGTERM, handle_sigterm)

    from crewos.infrastructure.celery_app import celery_app

    argv = [
        "worker",
        "--loglevel=info",
        "-Q",
        f"{settings.CELERY_QUEUE_NAME}.{settings.TENANT_ID}",  # match what routes.py enqueues to
        "--concurrency",
        str(settings.CELERY_CONCURRENCY),
    ]
    celery_app.worker_main(argv)

if __name__ == "__main__":
    main()