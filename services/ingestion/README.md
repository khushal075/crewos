# CrewOS ingestion

The standalone ingestion service accepts a plain-text source over HTTP and
stores version 1 in SQLite. It also supports reading revisions, replacing content,
and deleting a source. Content processing and indexing are downstream steps.

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

## Read, update, and delete sources

Set the instance's `INGESTION_PERMISSIONS` to the grants it needs. For a local
lifecycle demo, use `source.create,source.read,source.update,source.delete`.

| Request | Required grant | Result |
| --- | --- | --- |
| `GET /sources/{source_id}` | `source.read` | Latest revision |
| `GET /sources/{source_id}?version=1` | `source.read` | Specific revision |
| `PUT /sources/{source_id}` | `source.update` | Receipt for the new revision |
| `DELETE /sources/{source_id}` | `source.delete` | Deletion receipt |

PUT accepts the same fields as registration plus an integer
`expected_version`, for example
`{"title":"Webhook Recovery","content":"Retry later.","expected_version":1}`.
It replaces the content and metadata, so an omitted `source_reference`
clears it. The version check and insert happen in one transaction. A stale
expected version returns 409; previous revisions remain readable until deletion.

DELETE hides all revisions and retains the content and registration keys in
SQLite. It is a logical deletion, not permanent erasure or downstream index
cleanup. Once deleted, reads, updates, and repeated deletes return 404. A
registration retry using the original key returns 409 even after restart;
a new submission with a new key can create a different source.

All lifecycle requests use the same bearer authentication. Missing sources,
deleted sources, and sources owned by another tenant return 404 to authorized
callers. Missing permissions return 403.

## Run the tests

From the repository root, install the test extra and run the ingestion suite:

```sh
python -m pip install -e './services/ingestion[test]'
cd services/ingestion
python -m pytest
```

The tests use synthetic credentials and temporary databases.
