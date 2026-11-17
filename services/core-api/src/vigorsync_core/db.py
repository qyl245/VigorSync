"""SQLite persistence for sessions, events, pairing and idempotency."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    exercise TEXT NOT NULL,
                    view TEXT NOT NULL,
                    target_reps INTEGER NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    job_id TEXT,
                    report_url TEXT,
                    live_summary TEXT,
                    error TEXT,
                    artifacts TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    version TEXT NOT NULL,
                    t_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS pairing_tokens (
                    token_hash TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create_session(
        self,
        *,
        session_id: str,
        exercise: str,
        view: str,
        target_reps: int,
        notes: str | None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, status, exercise, view, target_reps, notes,
                    created_at, updated_at
                ) VALUES (?, 'created', ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    exercise,
                    view,
                    target_reps,
                    notes,
                    now,
                    now,
                ),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._session_row(row)

    def list_sessions(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._session_row(row) for row in rows]

    def update_session(self, session_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "started_at",
            "ended_at",
            "job_id",
            "report_url",
            "live_summary",
            "error",
            "artifacts",
        }
        if not set(changes) <= allowed:
            raise ValueError("unsupported session fields")
        converted: dict[str, Any] = {}
        for key, value in changes.items():
            converted[key] = (
                json.dumps(value, ensure_ascii=False)
                if key in {"live_summary", "error", "artifacts"}
                else value
            )
        converted["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in converted)
        values = [*converted.values(), session_id]
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE sessions SET {assignments} WHERE session_id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
        return self.get_session(session_id)

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        t_ms: int = 0,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO session_events (
                    session_id, type, version, t_ms, created_at, payload
                ) VALUES (?, ?, 'v1', ?, ?, ?)
                """,
                (
                    session_id,
                    event_type,
                    max(0, t_ms),
                    created_at,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            seq = int(cursor.lastrowid)
        return {
            "seq": seq,
            "session_id": session_id,
            "type": event_type,
            "version": "v1",
            "t_ms": max(0, t_ms),
            "created_at": created_at,
            "payload": payload,
        }

    def list_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM session_events
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC LIMIT ?
                """,
                (session_id, after_seq, limit),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "session_id": row["session_id"],
                "type": row["type"],
                "version": row["version"],
                "t_ms": row["t_ms"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def create_token(
        self,
        *,
        token: str,
        device_name: str,
        platform: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pairing_tokens (
                    token_hash, device_name, platform, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    token_hash(token),
                    device_name,
                    platform,
                    utc_now(),
                ),
            )

    def token_is_valid(self, token: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM pairing_tokens WHERE token_hash = ?",
                (token_hash(token),),
            ).fetchone()
        return row is not None

    def get_idempotent(self, scope: str, key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT response FROM idempotency
                WHERE scope = ? AND idempotency_key = ?
                """,
                (scope, key),
            ).fetchone()
        return None if row is None else json.loads(row["response"])

    def save_idempotent(
        self,
        scope: str,
        key: str,
        response: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO idempotency (
                    scope, idempotency_key, response, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    scope,
                    key,
                    json.dumps(response, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
            connection.execute(
                "DELETE FROM session_events WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM idempotency
                WHERE scope LIKE ?
                """,
                (f"%/sessions/{session_id}/%",),
            )

    @staticmethod
    def _session_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "status": row["status"],
            "exercise": row["exercise"],
            "view": row["view"],
            "target_reps": row["target_reps"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "job_id": row["job_id"],
            "report_url": row["report_url"],
            "live_summary": (
                json.loads(row["live_summary"])
                if row["live_summary"]
                else None
            ),
            "error": json.loads(row["error"]) if row["error"] else None,
            "artifacts": (
                json.loads(row["artifacts"]) if row["artifacts"] else None
            ),
            "updated_at": row["updated_at"],
        }
