FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INGESTION_DATABASE_PATH=/app/var/sources.sqlite3
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /app/var \
    && chown 65532:65532 /app/var \
    && chmod 700 /app/var

USER 65532:65532
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "crewos_ingestion.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
