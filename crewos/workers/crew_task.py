# crewos/workers/crew_task.py

from typing import Dict, Any
import json
from openai import AuthenticationError

from crewos.infrastructure.celery_app import celery_app
from crewos.services.crew_runner import CrewRunner
from crewos.infrastructure.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    name="crewos.workers.crew_task.run_crew",
    bind=True,
    autoretry_for=(AuthenticationError,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    retry_backoff=True,
)
def run_crew(self, tenant_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Celery task to run a Crew asynchronously.
    Fully decoupled: delegates to CrewRunner which injects LLM provider and CrewFactory.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")

    # Normalize payload if passed as JSON string
    if isinstance(payload, str):
        payload = payload.strip()
        payload = json.loads(payload) if payload else {}

    # Ensure defaults
    payload.setdefault("agent_type", "research")
    payload.setdefault("input", {"message": ""})

    message = payload["input"].get("message", "").strip()

    if not message:
        raise ValueError("Message is required")

    logger.info({
        "event": "crew_task_started",
        "tenant_id": tenant_id,
        "agent_type": payload.get("agent_type"),
        "has_message": bool(message)
    })

    try:
        # ✅ Delegate everything to CrewRunner (handles LLM + CrewFactory)
        result = CrewRunner.run(tenant_id=tenant_id, payload=payload)

        logger.info({
            "event": "crew_task_completed",
            "tenant_id": tenant_id,
            "agent_type": payload.get("agent_type"),
            "output_summary": str(result.get("output"))[:200]
        })

        return result

    except AuthenticationError as e:
        logger.warning({
            "event": "crew_task_retry",
            "tenant_id": tenant_id,
            "error": str(e)
        })
        raise self.retry(exc=e)

    except Exception as e:
        logger.error({
            "event": "crew_task_failed",
            "tenant_id": tenant_id,
            "error": str(e)
        }, exc_info=True)
        raise