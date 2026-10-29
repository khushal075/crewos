FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=true POETRY_VIRTUALENVS_IN_PROJECT=true
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir poetry==2.2.1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction --no-ansi
COPY crewos ./crewos
COPY README.md ./
RUN poetry install --only-root --no-interaction --no-ansi \
    && useradd --create-home crewos
USER crewos
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"
CMD ["uvicorn", "crewos.main:app", "--host", "0.0.0.0", "--port", "8000"]
