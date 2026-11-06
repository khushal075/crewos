"""Tenant-local FTS5 indexes maintained inside the source transaction."""

import hashlib
import re

from crewos.ingestion.document_store import DocumentStorageCorruption
from crewos.ingestion.document_corpus_reader import DocumentCorpusLimitExceeded


class SQLiteDocumentSearchIndex:
    @staticmethod
    def _search_table(tenant_id):
        if (
            not isinstance(tenant_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id) is None
        ):
            raise ValueError("Invalid tenant identity")
        # Identifier contains only a fixed prefix and a backend-generated hex digest.
        return "document_fts_" + hashlib.sha256(tenant_id.encode()).hexdigest()

    def _initialize_search_index(self, db):
        if (
            db.execute("PRAGMA user_version").fetchone()[0] >= 5
            and not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='document_search_rows'"
            ).fetchone()
        ):
            raise DocumentStorageCorruption("Search row mapping is missing")
        db.execute(
            """CREATE TABLE IF NOT EXISTS document_search_rows (
            tenant_id TEXT NOT NULL, document_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
            fts_rowid INTEGER NOT NULL,
            PRIMARY KEY(tenant_id,document_id,chunk_index))"""
        )

    def _index_document(self, db, record):
        table = self._search_table(record.tenant_id)
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5(document_id UNINDEXED, version UNINDEXED, chunk_index UNINDEXED, text, tokenize='unicode61 remove_diacritics 0')"
        )
        self._remove_search_document(db, record.tenant_id, record.document_id)
        for chunk in record.chunking.chunks:
            cursor = db.execute(
                f"INSERT INTO {table}(document_id,version,chunk_index,text) VALUES(?,?,?,?)",
                (str(record.document_id), record.version, chunk.index, chunk.text),
            )
            db.execute(
                "INSERT INTO document_search_rows VALUES(?,?,?,?)",
                (record.tenant_id, str(record.document_id), chunk.index, cursor.lastrowid),
            )

    def _remove_search_document(self, db, tenant_id, document_id):
        table = self._search_table(tenant_id)
        identity = (tenant_id, str(document_id))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            db.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT fts_rowid FROM document_search_rows WHERE tenant_id=? AND document_id=?)",
                identity,
            )
        db.execute("DELETE FROM document_search_rows WHERE tenant_id=? AND document_id=?", identity)

    def rebuild_search_index(self, *, tenant_id):
        """Trusted maintenance only; atomically rebuild one tenant's derived index."""
        table = self._search_table(tenant_id)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("DELETE FROM document_search_rows WHERE tenant_id=?", (tenant_id,))
            for row in db.execute(
                """SELECT current.* FROM document_versions current WHERE tenant_id=?
                AND NOT EXISTS (SELECT 1 FROM document_versions newer WHERE newer.tenant_id=current.tenant_id
                AND newer.document_id=current.document_id AND newer.version>current.version)""",
                (tenant_id,),
            ):
                self._index_document(db, self._decode(row))

    def _backfill_search_index(self, db):
        # Stream latest snapshots only, not historical source versions.
        for row in db.execute(
            """SELECT current.* FROM document_versions current WHERE NOT EXISTS (
            SELECT 1 FROM document_versions newer WHERE newer.tenant_id=current.tenant_id
            AND newer.document_id=current.document_id AND newer.version>current.version)"""
        ):
            self._index_document(db, self._decode(row))

    def search_current_chunks(self, *, tenant_id, terms, limit, max_snapshot_bytes=8_000_000):
        table = self._search_table(tenant_id)
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(max_snapshot_bytes) is not int
            or max_snapshot_bytes < 1
        ):
            raise ValueError("Invalid indexed-search admission")
        if (
            not isinstance(terms, tuple)
            or len(terms) > 64
            or any(not isinstance(t, str) or re.fullmatch(r"\w{1,2048}", t) is None for t in terms)
        ):
            raise ValueError("Invalid indexed-search terms")
        if not terms:
            return ()
        expression = " OR ".join('"' + term + '"' for term in terms)
        with self._connection() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                if db.execute(
                    "SELECT 1 FROM document_versions WHERE tenant_id=? LIMIT 1", (tenant_id,)
                ).fetchone():
                    raise DocumentStorageCorruption("Source exists but search index is missing")
                return ()
            rows = db.execute(
                f"SELECT document_id,version,chunk_index,text FROM {table} WHERE {table} MATCH ? ORDER BY rank,document_id,CAST(version AS INTEGER),CAST(chunk_index AS INTEGER) LIMIT ?",
                (expression, limit),
            ).fetchall()
            snapshots, output, used = {}, [], 0
            for row in rows:
                document_id = row["document_id"]
                if document_id not in snapshots:
                    size = db.execute(
                        "SELECT length(CAST(snapshot AS BLOB)) FROM document_versions WHERE tenant_id=? AND document_id=? ORDER BY version DESC LIMIT 1",
                        (tenant_id, document_id),
                    ).fetchone()
                    if size is None:
                        raise DocumentStorageCorruption("Search index refers to a missing source")
                    used += size[0]
                    if used > max_snapshot_bytes:
                        raise DocumentCorpusLimitExceeded(
                            "Indexed hits exceed source-byte admission"
                        )
                    source = db.execute(
                        "SELECT * FROM document_versions WHERE tenant_id=? AND document_id=? ORDER BY version DESC LIMIT 1",
                        (tenant_id, document_id),
                    ).fetchone()
                    snapshots[document_id] = self._decode(source)
                source = snapshots[document_id]
                index = row["chunk_index"]
                if (
                    source.version != row["version"]
                    or type(index) is not int
                    or not 0 <= index < len(source.chunking.chunks)
                    or source.chunking.chunks[index].text != row["text"]
                ):
                    raise DocumentStorageCorruption("Search index differs from current source")
                output.append((source, index))
            return tuple(output)
