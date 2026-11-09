"""Optional authenticated Prometheus scrape endpoint for the deployment."""

from hmac import compare_digest
import os

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess

from crewos.core.config import settings
from crewos.core.tenant import bearer
from crewos.infrastructure.agent_metrics import agent_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
def scrape_metrics(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    key = settings.AGENT_METRICS_TOKEN
    if key is None or not key.get_secret_value():
        raise HTTPException(404, "Metrics unavailable")
    if credentials is None or not compare_digest(
        credentials.credentials.encode(), key.get_secret_value().encode()
    ):
        raise HTTPException(403, "Metrics access denied")
    path = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if path:
        try:
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry, path=path)
            payload = generate_latest(registry)
        except (OSError, ValueError):
            raise HTTPException(503, "Metrics snapshot unavailable") from None
    else:
        payload = agent_metrics.render()
    return Response(payload, headers={"Content-Type": CONTENT_TYPE_LATEST})
