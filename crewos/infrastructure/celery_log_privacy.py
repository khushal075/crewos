"""Redact Celery trace failure records for CrewOS tasks, not task results."""

import logging

TASK_NAMES = frozenset(
    {
        "crewos.workers.agent_task.run_agent",
        "crewos.workers.document_task.run_document_job",
        "crewos.workers.crew_task.run_crew",
    }
)


class CrewTaskFailurePrivacyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        data = getattr(record, "data", None)
        if not isinstance(data, dict) or data.get("name") not in TASK_NAMES or "exc" not in data:
            return True
        error_type = (
            record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else "TaskError"
        )
        disposition = (
            data.get("description")
            if data.get("description") in {"ignored", "rejected"}
            else "failed"
        )
        safe = {
            "disposition": disposition,
            "name": data["name"],
            "id": data.get("id"),
            "error_type": error_type,
            "event": (
                "celery_task_failed" if disposition == "failed" else "celery_task_not_executed"
            ),
        }
        record.msg = "Task %(name)s[%(id)s] %(disposition)s (%(error_type)s)"
        record.args = safe
        record.data = safe
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def install_celery_failure_privacy() -> None:
    logger = logging.getLogger("celery.app.trace")
    if not any(isinstance(item, CrewTaskFailurePrivacyFilter) for item in logger.filters):
        logger.addFilter(CrewTaskFailurePrivacyFilter())
