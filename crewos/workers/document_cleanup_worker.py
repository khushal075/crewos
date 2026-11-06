"""Tenant-scoped durable-outbox polling; does not require Celery or Redis."""

import argparse
from collections import Counter
import json
from time import sleep

from crewos.application.dtos.document_cleanup_result import CleanupOutcome
from crewos.core.config import settings
from crewos.core.document_cleanup_setup import require_cleanup_tenant, run_cleanup_pass
from crewos.infrastructure.logging import get_logger

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--once", action="store_true", help="Process one bounded pass and exit")
    args = parser.parse_args(argv)
    try:
        require_cleanup_tenant(args.tenant_id)
    except (PermissionError, ValueError) as error:
        logger.error(
            {"event": "document_cleanup_scope_rejected", "error_type": type(error).__name__}
        )
        return 1
    try:
        while True:
            try:
                outcomes = run_cleanup_pass(tenant_id=args.tenant_id)
                counts = dict(Counter(result.outcome.value for result in outcomes))
                logger.info(
                    {
                        "event": "document_cleanup_pass_finished",
                        "tenant_id": args.tenant_id,
                        "outcomes": counts,
                    }
                )
                if args.once:
                    print(json.dumps({"tenant_id": args.tenant_id, "outcomes": counts}))
                    return 1 if counts.get(CleanupOutcome.RETRY_SCHEDULED.value, 0) else 0
            except Exception as error:
                logger.error(
                    {
                        "event": "document_cleanup_pass_failed",
                        "tenant_id": args.tenant_id,
                        "error_type": type(error).__name__,
                    }
                )
                if args.once or isinstance(error, PermissionError):
                    return 1
            sleep(settings.DOCUMENT_CLEANUP_POLL_SECONDS)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
