"""Encrypted SQLite persistence for interview history and settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.core.logging_setup import get_logger
from src.core.paths import db_path
from src.data.encryption import ENCRYPTOR, Encryptor
from src.data.models import DocumentRecord, MessageRecord, SessionRecord, SettingRecord

log = get_logger("database")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title_enc   BLOB NOT NULL,
    company_enc BLOB,
    role_enc    BLOB,
    notes_enc   BLOB,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    meta_json   TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    speaker     TEXT DEFAULT '',
    source      TEXT DEFAULT '',
    content_enc BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    meta_json   TEXT DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    doc_type     TEXT NOT NULL,
    chunk_count  INTEGER DEFAULT 0,
    checksum     TEXT DEFAULT '',
    indexed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value_enc   BLOB NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embedded_paths (
    key         TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    description TEXT DEFAULT '',
    updated_at  TEXT NOT NULL
);
"""


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Database:
    """Thread-safe encrypted SQLite store."""

    def __init__(self, path: Path | None = None, encryptor: Encryptor | None = None) -> None:
        self.path = path or db_path()
        self.encryptor = encryptor or ENCRYPTOR
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._cursor() as cur:
            cur.executescript(SCHEMA_SQL)
            cur.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
                ("version", "1"),
            )
        log.info("Database ready at %s", self.path)

    # ---- sessions ----

    def create_session(
        self,
        title: str = "Interview Session",
        company: str = "",
        role: str = "",
        notes: str = "",
        meta: dict[str, Any] | None = None,
    ) -> SessionRecord:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        enc = self.encryptor
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions(id, title_enc, company_enc, role_enc, notes_enc, started_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    enc.encrypt(title),
                    enc.encrypt(company),
                    enc.encrypt(role),
                    enc.encrypt(notes),
                    _iso(now),
                    json.dumps(meta or {}),
                ),
            )
        return SessionRecord(
            id=sid,
            title=title,
            started_at=now,
            company=company,
            role=role,
            notes=notes,
            meta=meta or {},
        )

    def end_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (_iso(now), session_id),
            )

    def list_sessions(self, limit: int = 50) -> list[SessionRecord]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
        return self._row_to_session(row) if row else None

    def _row_to_session(self, row: sqlite3.Row) -> SessionRecord:
        enc = self.encryptor
        return SessionRecord(
            id=row["id"],
            title=enc.decrypt(row["title_enc"]),
            company=enc.decrypt(row["company_enc"]) if row["company_enc"] else "",
            role=enc.decrypt(row["role_enc"]) if row["role_enc"] else "",
            notes=enc.decrypt(row["notes_enc"]) if row["notes_enc"] else "",
            started_at=_parse_iso(row["started_at"]) or datetime.now(timezone.utc),
            ended_at=_parse_iso(row["ended_at"]),
            meta=json.loads(row["meta_json"] or "{}"),
        )

    # ---- messages ----

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        speaker: str = "",
        source: str = "",
        meta: dict[str, Any] | None = None,
    ) -> MessageRecord:
        mid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO messages(id, session_id, role, speaker, source, content_enc, created_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    session_id,
                    role,
                    speaker,
                    source,
                    self.encryptor.encrypt(content),
                    _iso(now),
                    json.dumps(meta or {}),
                ),
            )
        return MessageRecord(
            id=mid,
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
            speaker=speaker,
            source=source,
            meta=meta or {},
        )

    def list_messages(self, session_id: str, limit: int = 500) -> list[MessageRecord]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
        enc = self.encryptor
        return [
            MessageRecord(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=enc.decrypt(r["content_enc"]),
                created_at=_parse_iso(r["created_at"]) or datetime.now(timezone.utc),
                speaker=r["speaker"] or "",
                source=r["source"] or "",
                meta=json.loads(r["meta_json"] or "{}"),
            )
            for r in rows
        ]

    # ---- documents ----

    def upsert_document(
        self,
        filename: str,
        path: str,
        doc_type: str,
        chunk_count: int,
        checksum: str = "",
        doc_id: str | None = None,
    ) -> DocumentRecord:
        did = doc_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents(id, filename, path, doc_type, chunk_count, checksum, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename=excluded.filename,
                    path=excluded.path,
                    doc_type=excluded.doc_type,
                    chunk_count=excluded.chunk_count,
                    checksum=excluded.checksum,
                    indexed_at=excluded.indexed_at
                """,
                (did, filename, path, doc_type, chunk_count, checksum, _iso(now)),
            )
        return DocumentRecord(
            id=did,
            filename=filename,
            path=path,
            doc_type=doc_type,
            chunk_count=chunk_count,
            indexed_at=now,
            checksum=checksum,
        )

    def list_documents(self) -> list[DocumentRecord]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM documents ORDER BY indexed_at DESC")
            rows = cur.fetchall()
        return [
            DocumentRecord(
                id=r["id"],
                filename=r["filename"],
                path=r["path"],
                doc_type=r["doc_type"],
                chunk_count=r["chunk_count"],
                indexed_at=_parse_iso(r["indexed_at"]) or datetime.now(timezone.utc),
                checksum=r["checksum"] or "",
            )
            for r in rows
        ]

    def delete_document(self, doc_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    # ---- settings ----

    def set_setting(self, key: str, value: str) -> SettingRecord:
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings(key, value_enc, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_enc=excluded.value_enc,
                    updated_at=excluded.updated_at
                """,
                (key, self.encryptor.encrypt(value), _iso(now)),
            )
        return SettingRecord(key=key, value=value, updated_at=now)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._cursor() as cur:
            cur.execute("SELECT value_enc FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
        if not row:
            return default
        return self.encryptor.decrypt(row["value_enc"])

    # ---- embedded paths (tesseract, tessdata, etc.) ----

    def set_embedded_path(self, key: str, path: str, description: str = "") -> None:
        now = datetime.now(timezone.utc)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO embedded_paths(key, path, description, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    path=excluded.path,
                    description=excluded.description,
                    updated_at=excluded.updated_at
                """,
                (key, path, description, _iso(now)),
            )

    def get_embedded_path(self, key: str) -> str | None:
        with self._cursor() as cur:
            cur.execute("SELECT path FROM embedded_paths WHERE key = ?", (key,))
            row = cur.fetchone()
        return row["path"] if row else None


# Lazy singleton
_DB: Database | None = None


def get_db() -> Database:
    global _DB
    if _DB is None:
        _DB = Database()
    return _DB
