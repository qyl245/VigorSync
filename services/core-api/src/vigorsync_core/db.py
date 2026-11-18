"""SQLite persistence for sessions, events, pairing and idempotency."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
                CREATE TABLE IF NOT EXISTS mirror_layout (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    todo_date TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_todos_date_order
                ON todos (todo_date, sort_order);
                CREATE TABLE IF NOT EXISTS weekly_plans (
                    id TEXT PRIMARY KEY,
                    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
                    exercise TEXT NOT NULL,
                    view TEXT,
                    sets INTEGER,
                    target_reps INTEGER NOT NULL,
                    note TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_weekly_plans_weekday_order
                ON weekly_plans (weekday, sort_order);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            }
            for name, definition in {
                "analysis_stage": "TEXT",
                "analysis_progress": "REAL NOT NULL DEFAULT 0",
                "analysis_summary": "TEXT",
                "report_ticket": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE sessions ADD COLUMN {name} {definition}"
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
            "analysis_stage",
            "analysis_progress",
            "analysis_summary",
            "report_ticket",
        }
        if not set(changes) <= allowed:
            raise ValueError("unsupported session fields")
        converted: dict[str, Any] = {}
        for key, value in changes.items():
            converted[key] = (
                json.dumps(value, ensure_ascii=False)
                if key
                in {
                    "live_summary",
                    "error",
                    "artifacts",
                    "analysis_summary",
                }
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
                "DELETE FROM pairing_tokens WHERE device_name = ? AND platform = ?",
                (device_name, platform),
            )
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

    def get_device_for_token(self, token: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT device_name, platform, created_at
                FROM pairing_tokens
                WHERE token_hash = ?
                """,
                (token_hash(token),),
            ).fetchone()
        if row is None:
            return None
        return {
            "device_name": row["device_name"],
            "platform": row["platform"],
            "created_at": row["created_at"],
        }

    def revoke_token(self, token: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM pairing_tokens WHERE token_hash = ?",
                (token_hash(token),),
            )
        return cursor.rowcount > 0

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

    def get_mirror_layout(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload, updated_at FROM mirror_layout WHERE id = 1",
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload"])
            modules = list(payload.get("modules") or [])
            version = int(payload.get("version") or 1)
            if version < 2:
                def append_with_capacity(
                    module_id: str,
                    preferred_column: str,
                ) -> None:
                    if any(module.get("id") == module_id for module in modules):
                        return
                    used_rows: dict[str, int] = {"left": 0, "right": 0}
                    max_order: dict[str, int] = {"left": -1, "right": -1}
                    for module in modules:
                        column = str(module.get("column") or "left")
                        rows = 2 if module.get("size") == "1x2" else 1
                        used_rows[column] = used_rows.get(column, 0) + rows
                        max_order[column] = max(
                            max_order.get(column, -1),
                            int(module.get("order") or 0),
                        )
                    other_column = "right" if preferred_column == "left" else "left"
                    column = next(
                        (
                            candidate
                            for candidate in (preferred_column, other_column)
                            if used_rows.get(candidate, 0) + 2 <= 4
                        ),
                        preferred_column,
                    )
                    modules.append(
                        {
                            "id": module_id,
                            "size": "1x2",
                            "column": column,
                            "order": max_order.get(column, -1) + 1,
                        }
                    )

                append_with_capacity("todo", "left")
                append_with_capacity("weekly_plan", "right")
                updated_at = utc_now()
                payload = {"version": 2, "modules": modules}
                connection.execute(
                    "UPDATE mirror_layout SET payload = ?, updated_at = ? WHERE id = 1",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        updated_at,
                    ),
                )
                row = {"updated_at": updated_at}
            payload["version"] = 2
            payload["updated_at"] = row["updated_at"]
            return payload

    def set_mirror_layout(self, modules: list[dict[str, Any]]) -> dict[str, Any]:
        updated_at = utc_now()
        payload = {"version": 2, "modules": modules}
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO mirror_layout (id, payload, updated_at)
                VALUES (1, ?, ?)
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    updated_at,
                ),
            )
        return {"version": 2, "modules": modules, "updated_at": updated_at}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def list_paired_devices(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT device_name, platform, created_at
                FROM pairing_tokens AS current
                WHERE current.rowid = (
                    SELECT candidate.rowid
                    FROM pairing_tokens AS candidate
                    WHERE candidate.device_name = current.device_name
                    ORDER BY candidate.created_at DESC, candidate.rowid DESC
                    LIMIT 1
                )
                ORDER BY created_at DESC
                """,
            ).fetchall()
        return [
            {
                "device_name": row["device_name"],
                "platform": row["platform"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def daily_trend(self, days: int) -> list[dict[str, Any]]:
        from datetime import timedelta

        today = datetime.now().astimezone().date()
        start = today - timedelta(days=days - 1)
        buckets: dict[str, dict[str, Any]] = {
            (start + timedelta(days=offset)).isoformat(): {
                "date": (start + timedelta(days=offset)).isoformat(),
                "sessions": 0,
                "duration_s": 0.0,
                "reps": 0,
            }
            for offset in range(days)
        }
        def parse_timestamp(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT started_at, ended_at, created_at, live_summary
                FROM sessions
                WHERE status IN ('analyzing', 'completed')
                """,
            ).fetchall()
        for row in rows:
            stamp = row["started_at"] or row["created_at"]
            parsed_stamp = parse_timestamp(stamp)
            if parsed_stamp is None:
                continue
            local_date = parsed_stamp.astimezone().date().isoformat()
            bucket = buckets.get(local_date)
            if bucket is None:
                continue
            try:
                summary = (
                    json.loads(row["live_summary"])
                    if row["live_summary"]
                    else None
                ) or {}
            except (TypeError, ValueError):
                summary = {}
            duration = summary.get("duration_s")
            if duration is None:
                # Snapshot metrics historically stored live elapsed time here.
                duration = summary.get("elapsed_s")
            try:
                duration_s = max(0.0, float(duration or 0.0))
            except (TypeError, ValueError):
                duration_s = 0.0
            if duration_s == 0.0:
                started = parse_timestamp(row["started_at"])
                ended = parse_timestamp(row["ended_at"])
                if started is not None and ended is not None:
                    duration_s = max(
                        0.0,
                        (ended - started).total_seconds(),
                    )
            try:
                reps = int(summary.get("reps") or 0)
            except (TypeError, ValueError):
                reps = 0
            bucket["sessions"] += 1
            bucket["duration_s"] += duration_s
            bucket["reps"] += reps
        return list(buckets.values())

    @staticmethod
    def _local_date() -> str:
        return datetime.now().astimezone().date().isoformat()

    @classmethod
    def _retained_from(cls) -> str:
        return (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()

    def _prune_todos(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM todos WHERE todo_date < ?",
            (self._retained_from(),),
        )

    @staticmethod
    def _todo_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "completed": bool(row["completed"]),
            "todo_date": row["todo_date"],
            "sort_order": row["sort_order"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_todos(self, todo_date: str | None = None) -> dict[str, Any]:
        target = todo_date or self._local_date()
        with self._lock, self._connect() as connection:
            self._prune_todos(connection)
            rows = connection.execute(
                """
                SELECT * FROM todos
                WHERE todo_date = ?
                ORDER BY sort_order ASC, created_at ASC
                """,
                (target,),
            ).fetchall()
        items = [self._todo_row(row) for row in rows]
        return {
            "date": target,
            "items": items,
            "completed": sum(1 for item in items if item["completed"]),
            "total": len(items),
        }

    def create_todo(
        self,
        *,
        todo_id: str,
        title: str,
        todo_date: str | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        target = todo_date or self._local_date()
        if target < self._retained_from():
            raise ValueError("todo_date_expired")
        now = utc_now()
        with self._lock, self._connect() as connection:
            self._prune_todos(connection)
            if sort_order is None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM todos WHERE todo_date = ?",
                    (target,),
                ).fetchone()
                sort_order = int(row["next_order"])
            connection.execute(
                """
                INSERT INTO todos (
                    id, title, completed, todo_date, sort_order, created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, ?, ?)
                """,
                (todo_id, title, target, sort_order, now, now),
            )
            row = connection.execute(
                "SELECT * FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
        return self._todo_row(row)

    def update_todo(self, todo_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"title", "completed", "sort_order"}
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            raise ValueError("no_todo_changes")
        if "completed" in updates:
            updates["completed"] = int(bool(updates["completed"]))
        updates["updated_at"] = utc_now()
        with self._lock, self._connect() as connection:
            self._prune_todos(connection)
            existing = connection.execute(
                "SELECT * FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(todo_id)
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(
                f"UPDATE todos SET {assignments} WHERE id = ?",
                (*updates.values(), todo_id),
            )
            row = connection.execute(
                "SELECT * FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
        return self._todo_row(row)

    def delete_todo(self, todo_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            self._prune_todos(connection)
            row = connection.execute(
                "SELECT * FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
            if row is None:
                raise KeyError(todo_id)
            connection.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        return self._todo_row(row)

    @staticmethod
    def _weekly_plan_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "weekday": row["weekday"],
            "exercise": row["exercise"],
            "view": row["view"],
            "sets": row["sets"],
            "target_reps": row["target_reps"],
            "note": row["note"],
            "sort_order": row["sort_order"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_weekly_plans(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM weekly_plans
                ORDER BY weekday ASC, sort_order ASC, created_at ASC
                """,
            ).fetchall()
        items = [self._weekly_plan_row(row) for row in rows]
        updated_at = max(
            (item["updated_at"] for item in items),
            default=utc_now(),
        )
        return {"items": items, "updated_at": updated_at}

    def create_weekly_plan(
        self,
        *,
        plan_id: str,
        weekday: int,
        exercise: str,
        view: str | None,
        sets: int | None,
        target_reps: int,
        note: str | None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            if sort_order is None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM weekly_plans WHERE weekday = ?",
                    (weekday,),
                ).fetchone()
                sort_order = int(row["next_order"])
            connection.execute(
                """
                INSERT INTO weekly_plans (
                    id, weekday, exercise, view, sets, target_reps, note,
                    sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    weekday,
                    exercise,
                    view,
                    sets,
                    target_reps,
                    note,
                    sort_order,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM weekly_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        return self._weekly_plan_row(row)

    def update_weekly_plan(self, plan_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "weekday",
            "exercise",
            "view",
            "sets",
            "target_reps",
            "note",
            "sort_order",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            raise ValueError("no_weekly_plan_changes")
        updates["updated_at"] = utc_now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM weekly_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(plan_id)
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(
                f"UPDATE weekly_plans SET {assignments} WHERE id = ?",
                (*updates.values(), plan_id),
            )
            row = connection.execute(
                "SELECT * FROM weekly_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        return self._weekly_plan_row(row)

    def delete_weekly_plan(self, plan_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM weekly_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            connection.execute("DELETE FROM weekly_plans WHERE id = ?", (plan_id,))
        return self._weekly_plan_row(row)

    def replace_weekly_plans(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            existing_rows = connection.execute(
                "SELECT id, created_at FROM weekly_plans",
            ).fetchall()
            created_at_by_id = {
                row["id"]: row["created_at"] for row in existing_rows
            }
            connection.execute("DELETE FROM weekly_plans")
            next_order: dict[int, int] = {}
            used_ids: set[str] = set()
            for item in items:
                weekday = int(item["weekday"])
                plan_id = item.get("id") or f"wp_{secrets.token_hex(8)}"
                if plan_id in used_ids:
                    plan_id = f"wp_{secrets.token_hex(8)}"
                used_ids.add(plan_id)
                sort_order = item.get("sort_order")
                if sort_order is None:
                    sort_order = next_order.get(weekday, 0)
                next_order[weekday] = max(next_order.get(weekday, 0), int(sort_order) + 1)
                created_at = created_at_by_id.get(plan_id, now)
                connection.execute(
                    """
                    INSERT INTO weekly_plans (
                        id, weekday, exercise, view, sets, target_reps, note,
                        sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        weekday,
                        item["exercise"],
                        item.get("view"),
                        item.get("sets"),
                        int(item["target_reps"]),
                        item.get("note"),
                        int(sort_order),
                        created_at,
                        now,
                    ),
                )
        return self.list_weekly_plans()

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

    def get_session_by_report_ticket(
        self,
        report_ticket: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE report_ticket = ?",
                (report_ticket,),
            ).fetchone()
        return self._session_row(row) if row is not None else None

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
            "analysis_stage": row["analysis_stage"],
            "analysis_progress": row["analysis_progress"],
            "analysis_summary": (
                json.loads(row["analysis_summary"])
                if row["analysis_summary"]
                else None
            ),
            "report_ticket": row["report_ticket"],
            "updated_at": row["updated_at"],
        }
