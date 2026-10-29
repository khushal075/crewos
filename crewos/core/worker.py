"""Worker entrypoint: let Celery manage its own shutdown signals."""

import argparse
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue


def main():
    parser = argparse.ArgumentParser(description="CrewOS tenant worker")
    parser.add_argument("--tenant-id", default=settings.TENANT_ID)
    args = parser.parse_args()
    from crewos.infrastructure.celery_app import celery_app

    celery_app.worker_main(
        [
            "worker",
            "--loglevel=info",
            "-Q",
            tenant_queue(args.tenant_id),
            "--concurrency",
            str(settings.CELERY_CONCURRENCY),
            "--hostname",
            f"crewos-{args.tenant_id}@%h",
        ]
    )


if __name__ == "__main__":
    main()
