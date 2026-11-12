# CrewOS ingestion

The standalone ingestion service accepts a plain-text source over HTTP and
stores version 1 in SQLite. Its acceptance receipt identifies the stored
source; content processing and indexing are downstream steps.

## Run in Docker

From the CrewOS repository root:

```sh
test -e services/ingestion/.env.local || \
  cp services/ingestion/.env.example services/ingestion/.env.local
docker build -t crewos-ingestion:local services/ingestion
docker volume create crewos-ingestion-data
```

The copy command leaves an existing ingestion `.env.local` in place. For a
new file, generate a token with
`python -c 'import secrets; print(secrets.token_urlsafe(32))'` and set
`INGESTION_API_TOKEN`. Keep `.env.local` out of Git. Each instance currently
maps one configured bearer token to one configured tenant and set of grants.

```sh
docker run --rm --name crewos-ingestion \
  -p 127.0.0.1:8001:8001 \
  --env-file services/ingestion/.env.local \
  -e INGESTION_DATABASE_PATH=/app/var/sources.sqlite3 \
  --mount source=crewos-ingestion-data,target=/app/var \
  crewos-ingestion:local
```

The service listens on port 8001. The host binding keeps this example local.
Docker passes configuration through the process environment, and the named
volume keeps the database when the container is replaced. Leave the volume in
place to retain your data.

## Submit a source

In another terminal, set `INGESTION_API_TOKEN` to the same token from your local
file, then submit:

```sh
curl -sS http://127.0.0.1:8001/sources \
  -H "Authorization: Bearer ${INGESTION_API_TOKEN}" \
  -H "Idempotency-Key: upload-42" \
  -H "Content-Type: application/json" \
  --data '{"title":"Webhook Recovery","content":"Check delivery logs."}'
```

The body accepts `title`, `content`, and an optional `source_reference`.
A successful request returns HTTP 200 with `source_id`, `version`, and
`status: "accepted"`. Repeating the same submission with the same key returns
the original receipt, including after replacing the container with the same
volume. Reusing the key with different content returns 409. Missing or invalid
credentials return 401, and callers without `source.create` receive 403.

## Run the tests

From the repository root, install the test extra and run the ingestion suite:

```sh
python -m pip install -e './services/ingestion[test]'
cd services/ingestion
python -m pytest
```

The tests use synthetic credentials and temporary databases.
