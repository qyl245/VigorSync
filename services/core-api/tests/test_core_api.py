from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
import tempfile
from datetime import datetime, timedelta
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_core.app import create_app
from vigorsync_core.config import Settings
from vigorsync_core.discovery import build_discovery_response
from vigorsync_core.db import Database


class FakeLive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, str | None]] = []

    async def start(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("start", session_id))
        return {
            "session_id": session_id,
            "status": "preparing",
            "stream_url": f"http://127.0.0.1:8020/live/{session_id}/stream.mjpg",
            "ws_url": f"ws://127.0.0.1:8020/ws/live/{session_id}",
        }

    async def pause(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("pause", session_id))
        return {"session_id": session_id, "status": "paused"}

    async def resume(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("resume", session_id))
        return {"session_id": session_id, "status": "active"}

    async def stop(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("stop", session_id))
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        return {
            "session_id": session_id,
            "status": "finalized",
            "artifacts": {
                "video_path": str(video),
                "landmarks_path": "/tmp/landmarks.jsonl",
                "events_path": "/tmp/events.jsonl",
                "summary_path": "/tmp/summary.json",
            },
            "summary": {
                "reps": 8,
                "duration_s": 12.5,
                "mean_confidence": 0.92,
                "alert_counts": {},
            },
        }

    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}


class FakeOffline:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    async def enqueue(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = payload["job_id"]
        self.calls.append(("enqueue", job_id))
        self.jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
        }
        return self.jobs[job_id]

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs[job_id]

    async def get_result(self, job_id: str) -> dict[str, Any]:
        self.calls.append(("result", job_id))
        return {
            "sample": {
                "status": "ok",
                "rep_count": 8,
                "rejected_rep_count": 0,
                "duration_seconds": 12.5,
                "quality_status": "pass",
                "active_issue_count": 1,
                "severity_counts": {"high": 1, "medium": 0, "low": 0},
            },
            "confidence": {"mean_confidence": 0.9},
            "issues": [
                {
                    "issue_code": "pushup_tempo_inconsistent",
                    "title": "节奏变化",
                    "detail": "测试问题",
                    "cue": "保持节奏",
                    "severity": "high",
                    "confidence": 0.8,
                    "anchor_s": 3.2,
                    "evidence_interval_s": [2.8, 3.8],
                    "suppressed": False,
                }
            ],
        }

    async def cancel(self, job_id: str) -> None:
        self.calls.append(("cancel", job_id))

    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}


class CoreApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            project_root=root,
            db_path=root / "core.db",
            session_root=root / "sessions",
            port=8011,
            live_url="http://127.0.0.1:8020",
            offline_url="http://127.0.0.1:8030",
            offline_data_root=root / "offline",
            mirror_token="mirror-test",
            pairing_code="123456",
            discovery_enabled=False,
        )
        self.live = FakeLive(root)
        self.offline = FakeOffline()
        self.app = create_app(self.settings, self.live, self.offline)
        self.client = TestClient(self.app)
        response = self.client.post(
            "/api/v1/pairing/claim",
            json={
                "pairing_code": "123456",
                "device_name": "Test Phone",
                "platform": "android",
            },
        )
        self.assertEqual(200, response.status_code)
        self.token = response.json()["token"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "create-1",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_session(self) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/sessions",
            headers=self.headers,
            json={
                "exercise": "squat",
                "view": "front",
                "target_reps": 10,
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_session_control_and_idempotency(self) -> None:
        session = self.create_session()
        duplicate = self.client.post(
            "/api/v1/sessions",
            headers=self.headers,
            json={
                "exercise": "squat",
                "view": "front",
                "target_reps": 10,
            },
        ).json()
        self.assertEqual(session["session_id"], duplicate["session_id"])
        session_id = session["session_id"]

        command_headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "start-1",
        }
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers=command_headers,
        )
        self.assertEqual(200, started.status_code, started.text)
        self.assertEqual("preparing", started.json()["status"])
        started_again = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers=command_headers,
        )
        self.assertEqual(started.json(), started_again.json())

        state = self.client.post(
            f"/api/v1/internal/sessions/{session_id}/state",
            json={
                "status": "active",
                "t_ms": 100,
                "payload": {
                    "exercise": "squat",
                    "phase": "up",
                    "reps": 0,
                    "confidence": 0.9,
                    "fps": 18.0,
                    "paused": False,
                },
            },
        )
        self.assertEqual(200, state.status_code, state.text)
        events_after_state = self.client.get(
            f"/api/v1/sessions/{session_id}/events",
            headers={"Authorization": f"Bearer {self.token}"},
        ).json()["items"]
        active_states = [
            item
            for item in events_after_state
            if item["type"] == "session.state"
            and item["payload"].get("status") == "active"
        ]
        self.assertEqual(1, len(active_states))
        self.assertEqual(
            "preparing",
            active_states[0]["payload"].get("previous_status"),
        )
        detail = self.client.get(f"/dev/v1/sessions/{session_id}").json()
        self.assertTrue(detail["stream_url"].endswith("/stream.mjpg"))
        self.assertTrue(detail["ws_url"].endswith(f"/ws/live/{session_id}"))

        paused = self.client.post(
            f"/api/v1/sessions/{session_id}/pause",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "pause-1",
            },
        )
        self.assertEqual(200, paused.status_code, paused.text)
        self.assertEqual("paused", paused.json()["status"])

        resumed = self.client.post(
            f"/api/v1/sessions/{session_id}/resume",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "resume-1",
            },
        )
        self.assertEqual(200, resumed.status_code, resumed.text)
        self.assertEqual("active", resumed.json()["status"])

        stopped = self.client.post(
            f"/api/v1/sessions/{session_id}/stop",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "stop-1",
            },
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        self.assertEqual("analyzing", stopped.json()["status"])

        events = self.client.get(
            f"/api/v1/sessions/{session_id}/events",
            headers={"Authorization": f"Bearer {self.token}"},
        ).json()
        self.assertGreaterEqual(len(events["items"]), 5)
        self.assertTrue(
            any(item["type"] == "session.state" for item in events["items"])
        )

    def test_pairing_devices_are_deduplicated_by_name(self) -> None:
        again = self.client.post(
            "/api/v1/pairing/claim",
            json={
                "pairing_code": "123456",
                "device_name": "Test Phone",
                "platform": "android",
            },
        )
        self.assertEqual(200, again.status_code)
        second = self.client.post(
            "/api/v1/pairing/claim",
            json={
                "pairing_code": "123456",
                "device_name": "Second Phone",
                "platform": "android",
            },
        )
        self.assertEqual(200, second.status_code)

        devices = self.client.get(
            "/api/v1/pairing/devices",
            headers={"Authorization": f"Bearer {second.json()['token']}"},
        ).json()["items"]

        self.assertEqual(
            ["Second Phone", "Test Phone"],
            [item["device_name"] for item in devices],
        )

    def test_pairing_identity_and_current_token_revoke(self) -> None:
        identity = self.client.get(
            "/api/v1/pairing/me",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(200, identity.status_code)
        self.assertEqual("Test Phone", identity.json()["device_name"])

        revoked = self.client.delete(
            "/api/v1/pairing/current",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(200, revoked.status_code)
        self.assertTrue(revoked.json()["revoked"])
        self.assertEqual(
            401,
            self.client.get(
                "/api/v1/pairing/me",
                headers={"Authorization": f"Bearer {self.token}"},
            ).status_code,
        )

    def test_revoke_pairing_broadcasts_disconnected_state(self) -> None:
        def receive_pairing_state(socket: Any) -> dict[str, Any]:
            for _ in range(12):
                message = socket.receive_json()
                if message.get("type") == "pairing.state":
                    return message
            self.fail("mirror websocket did not receive pairing.state")

        with self.client.websocket_connect(
            "/api/v1/ws/mirror?token=mirror-test",
        ) as mirror:
            self.assertFalse(
                receive_pairing_state(mirror)["payload"]["connected"],
            )
            with self.client.websocket_connect(
                f"/api/v1/ws/client?token={self.token}",
            ) as client_socket:
                client_socket.receive_json()
                self.assertTrue(
                    receive_pairing_state(mirror)["payload"]["connected"],
                )

                revoked = self.client.delete(
                    "/api/v1/pairing/current",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                self.assertEqual(200, revoked.status_code)
                self.assertTrue(revoked.json()["revoked"])
                self.assertFalse(
                    receive_pairing_state(mirror)["payload"]["connected"],
                )

    def test_discovery_response_contains_no_secret(self) -> None:
        response = build_discovery_response(
            core_ip="192.168.1.20",
            core_port=8011,
            device_name="客厅镜端",
        )
        self.assertEqual("http://192.168.1.20:8011", response["core_url"])
        self.assertEqual("vigorsync-core", response["service"])
        self.assertNotIn("token", response)

    def test_cancel_startup_removes_history_and_artifacts(self) -> None:
        session = self.create_session()
        session_id = session["session_id"]
        auth_headers = {"Authorization": f"Bearer {self.token}"}
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers={**auth_headers, "Idempotency-Key": "cancel-start"},
        )
        self.assertEqual(200, started.status_code, started.text)
        self.assertEqual("preparing", started.json()["status"])

        session_dir = self.settings.session_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "partial.mp4").write_bytes(b"partial")

        cancel_headers = {
            **auth_headers,
            "Idempotency-Key": "cancel-preparing",
        }
        cancelled = self.client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers=cancel_headers,
        )
        self.assertEqual(200, cancelled.status_code, cancelled.text)
        self.assertEqual("cancelled", cancelled.json()["status"])
        self.assertFalse(session_dir.exists())

        missing = self.client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers,
        )
        self.assertEqual(404, missing.status_code, missing.text)
        sessions = self.client.get(
            "/api/v1/sessions",
            headers=auth_headers,
        ).json()["items"]
        self.assertNotIn(
            session_id,
            {item["session_id"] for item in sessions},
        )
        trend = self.client.get(
            "/api/v1/stats/trend",
            headers=auth_headers,
        ).json()["days"]
        self.assertTrue(all(day["sessions"] == 0 for day in trend))

        repeated = self.client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers=cancel_headers,
        )
        self.assertEqual(cancelled.json(), repeated.json())
        self.assertEqual(
            1,
            sum(call == ("stop", session_id) for call in self.live.calls),
        )
    def test_cancel_after_training_starts_is_rejected(self) -> None:
        session = self.create_session()
        session_id = session["session_id"]
        auth_headers = {"Authorization": f"Bearer {self.token}"}
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers={**auth_headers, "Idempotency-Key": "active-cancel-start"},
        )
        self.assertEqual(200, started.status_code, started.text)
        active = self.client.post(
            f"/api/v1/internal/sessions/{session_id}/state",
            json={
                "status": "active",
                "t_ms": 0,
                "payload": {
                    "exercise": "squat",
                    "phase": "up",
                    "reps": 0,
                    "confidence": 0.9,
                    "fps": 18.0,
                    "paused": False,
                },
            },
        )
        self.assertEqual(200, active.status_code, active.text)

        cancelled = self.client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers={**auth_headers, "Idempotency-Key": "active-cancel"},
        )
        self.assertEqual(409, cancelled.status_code, cancelled.text)
        self.assertEqual("invalid_transition", cancelled.json()["error"]["code"])
        detail = self.client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers,
        )
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual("active", detail.json()["status"])

    def test_create_and_start_is_one_idempotent_call(self) -> None:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "create-and-start-1",
        }
        payload = {
            "exercise": "pushup",
            "view": "side_right",
            "target_reps": 9,
        }
        started = self.client.post(
            "/api/v1/sessions/start",
            headers=headers,
            json=payload,
        )
        self.assertEqual(200, started.status_code, started.text)
        self.assertEqual("preparing", started.json()["status"])
        repeated = self.client.post(
            "/api/v1/sessions/start",
            headers=headers,
            json=payload,
        )
        self.assertEqual(started.json(), repeated.json())
        self.assertEqual(
            1,
            len([call for call in self.live.calls if call[0] == "start"]),
        )

    def test_mirror_websocket_snapshot(self) -> None:
        self.create_session()
        with self.client.websocket_connect(
            "/api/v1/ws/mirror",
            subprotocols=[
                "vigorsync.v1",
                "vigorsync.token.mirror-test",
            ],
        ) as websocket:
            snapshot = websocket.receive_json()
        self.assertEqual("session.snapshot", snapshot["type"])
        self.assertIsNotNone(snapshot["payload"])

    def test_mirror_theme_is_persisted_and_replayed(self) -> None:
        response = self.client.post(
            "/api/v1/control/theme",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"mode": "light"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            {"mode": "light", "delivered": True},
            response.json(),
        )

        with self.client.websocket_connect(
            "/api/v1/ws/mirror",
            subprotocols=[
                "vigorsync.v1",
                "vigorsync.token.mirror-test",
            ],
        ) as websocket:
            messages = [websocket.receive_json() for _ in range(6)]
        themes = [
            message for message in messages if message["type"] == "mirror.theme"
        ]
        self.assertEqual(1, len(themes))
        self.assertEqual({"mode": "light"}, themes[0]["payload"])

    def test_mobile_websocket_accepts_bearer_header(self) -> None:
        self.create_session()
        with self.client.websocket_connect(
            "/api/v1/ws/client",
            headers={"Authorization": f"Bearer {self.token}"},
        ) as websocket:
            snapshot = websocket.receive_json()
        self.assertEqual("session.snapshot", snapshot["type"])
        self.assertIsNotNone(snapshot["payload"])

    def test_missing_idempotency_key_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"exercise": "squat", "view": "front", "target_reps": 5},
        )
        self.assertEqual(422, response.status_code)

    def test_control_config_and_mirror_dismiss(self) -> None:
        updated = self.client.post(
            "/api/v1/control/config",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "exercise": "pushup",
                "view": "side_right",
                "target_reps": 18,
            },
        )
        self.assertEqual(200, updated.status_code, updated.text)
        self.assertEqual(
            {
                "exercise": "pushup",
                "view": "side_right",
                "target_reps": 18,
            },
            self.client.get("/dev/v1/config").json(),
        )

        session = self.create_session()
        session_id = session["session_id"]
        premature = self.client.post(
            f"/api/v1/sessions/{session_id}/mirror/dismiss",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(409, premature.status_code)

        started = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "start-dismiss-1",
            },
        )
        self.assertEqual(200, started.status_code, started.text)
        active = self.client.post(
            f"/api/v1/internal/sessions/{session_id}/state",
            json={
                "status": "active",
                "t_ms": 0,
                "payload": {
                    "exercise": "pushup",
                    "phase": "up",
                    "reps": 0,
                    "confidence": 0.9,
                    "fps": 18.0,
                    "paused": False,
                },
            },
        )
        self.assertEqual(200, active.status_code, active.text)
        stopped = self.client.post(
            f"/api/v1/sessions/{session_id}/stop",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "stop-dismiss-1",
            },
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        dismissed = self.client.post(
            f"/api/v1/sessions/{session_id}/mirror/dismiss",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(200, dismissed.status_code, dismissed.text)
        self.assertTrue(dismissed.json()["dismissed"])

    def test_delete_finished_session_removes_record_and_directory(self) -> None:
        session = self.create_session()
        session_id = session["session_id"]
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "start-delete-1",
            },
        )
        self.assertEqual(200, started.status_code, started.text)
        session_dir = self.settings.session_root / session_id
        (session_dir / "input").mkdir(parents=True)
        (session_dir / "live").mkdir(parents=True)
        (session_dir / "input" / "source.mp4").write_bytes(b"video")
        (session_dir / "input" / "landmarks.jsonl").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (session_dir / "live" / "summary.json").write_text(
            "{}",
            encoding="utf-8",
        )

        active = self.client.post(
            f"/api/v1/internal/sessions/{session_id}/state",
            json={
                "status": "active",
                "t_ms": 0,
                "payload": {
                    "exercise": "squat",
                    "phase": "up",
                    "reps": 0,
                    "confidence": 0.9,
                    "fps": 18.0,
                    "paused": False,
                },
            },
        )
        self.assertEqual(200, active.status_code, active.text)
        refused = self.client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(409, refused.status_code)
        self.assertTrue(session_dir.is_dir())

        stopped = self.client.post(
            f"/api/v1/sessions/{session_id}/stop",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "stop-delete-1",
            },
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        Database(self.settings.db_path).update_session(
            session_id,
            status="completed",
            analysis_stage="completed",
            analysis_progress=1.0,
        )
        deleted = self.client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse(session_dir.exists())
        listed = self.client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {self.token}"},
        ).json()["items"]
        self.assertNotIn(session_id, {item["session_id"] for item in listed})

    def test_daily_trend_uses_summary_duration_and_timestamp_fallback(
        self,
    ) -> None:
        database = Database(self.settings.db_path)
        common = {
            "exercise": "squat",
            "view": "front",
            "target_reps": 10,
            "notes": None,
        }
        summary_duration = database.create_session(
            session_id="s_trend_summary",
            **common,
        )
        elapsed_duration = database.create_session(
            session_id="s_trend_elapsed",
            **common,
        )
        timestamp_duration = database.create_session(
            session_id="s_trend_timestamp",
            **common,
        )
        database.update_session(
            summary_duration["session_id"],
            status="completed",
            live_summary={
                "reps": 3,
                "duration_s": 10,
                "elapsed_s": 99,
            },
        )
        database.update_session(
            elapsed_duration["session_id"],
            status="completed",
            live_summary={"reps": 4, "elapsed_s": 12.5},
        )
        ended = datetime.now().astimezone().replace(microsecond=0)
        started = ended - timedelta(seconds=65)
        database.update_session(
            timestamp_duration["session_id"],
            status="completed",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            live_summary={"reps": 5},
        )

        today = ended.date().isoformat()
        today_bucket = next(
            item
            for item in database.daily_trend(7)
            if item["date"] == today
        )
        self.assertEqual(3, today_bucket["sessions"])
        self.assertEqual(12, today_bucket["reps"])
        self.assertAlmostEqual(87.5, today_bucket["duration_s"])

    def test_todo_crud_and_two_day_retention(self) -> None:
        create_headers = {"Authorization": f"Bearer {self.token}"}
        created = self.client.post(
            "/api/v1/todos",
            headers=create_headers,
            json={"title": "  喝水  "},
        )
        self.assertEqual(200, created.status_code, created.text)
        todo = created.json()
        self.assertEqual("喝水", todo["title"])
        self.assertFalse(todo["completed"])

        listed = self.client.get(
            "/api/v1/todos",
            headers=create_headers,
        ).json()
        self.assertEqual(1, listed["total"])
        self.assertEqual(0, listed["completed"])

        completed = self.client.patch(
            f"/api/v1/todos/{todo['id']}",
            headers=create_headers,
            json={"completed": True},
        )
        self.assertEqual(200, completed.status_code, completed.text)
        listed = self.client.get(
            "/api/v1/todos",
            headers=create_headers,
        ).json()
        self.assertEqual(1, listed["completed"])

        database = Database(self.settings.db_path)
        today = datetime.now().astimezone().date()
        yesterday = (today - timedelta(days=1)).isoformat()
        expired = (today - timedelta(days=2)).isoformat()
        database.create_todo(
            todo_id="t_retained_yesterday",
            title="昨日",
            todo_date=yesterday,
        )
        now = "2026-11-18T00:00:00.000Z"
        with closing(sqlite3.connect(self.settings.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO todos (
                    id, title, completed, todo_date, sort_order,
                    created_at, updated_at
                ) VALUES (?, ?, 0, ?, 0, ?, ?)
                """,
                ("t_expired", "过期", expired, now, now),
            )
            connection.commit()
        database.list_todos()
        with closing(sqlite3.connect(self.settings.db_path)) as connection:
            ids = {
                row[0]
                for row in connection.execute("SELECT id FROM todos")
            }
        self.assertIn("t_retained_yesterday", ids)
        self.assertNotIn("t_expired", ids)

        deleted = self.client.delete(
            f"/api/v1/todos/{todo['id']}",
            headers=create_headers,
        )
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertEqual(
            0,
            self.client.get(
                "/api/v1/todos",
                headers=create_headers,
            ).json()["total"],
        )

    def test_weekly_plan_crud_and_atomic_replace(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        created = self.client.post(
            "/api/v1/weekly-plans",
            headers=headers,
            json={
                "weekday": 1,
                "exercise": "squat",
                "view": "front",
                "sets": 4,
                "target_reps": 12,
                "note": "动作放慢",
            },
        )
        self.assertEqual(200, created.status_code, created.text)
        self.assertEqual("squat", created.json()["exercise"])

        replaced = self.client.put(
            "/api/v1/weekly-plans",
            headers=headers,
            json={
                "items": [
                    {
                        "weekday": 1,
                        "exercise": "pushup",
                        "target_reps": 10,
                    },
                    {
                        "weekday": 1,
                        "exercise": "squat",
                        "target_reps": 15,
                        "sort_order": 1,
                    },
                    {
                        "weekday": 7,
                        "exercise": "squat",
                        "view": "side_left",
                        "sets": 3,
                        "target_reps": 8,
                    },
                ]
            },
        )
        self.assertEqual(200, replaced.status_code, replaced.text)
        items = replaced.json()["items"]
        self.assertEqual(3, len(items))
        self.assertEqual(
            ["pushup", "squat", "squat"],
            [item["exercise"] for item in items],
        )

        plan_id = items[0]["id"]
        updated = self.client.patch(
            f"/api/v1/weekly-plans/{plan_id}",
            headers=headers,
            json={
                "weekday": 2,
                "exercise": "pushup",
                "view": "side_right",
                "sets": 5,
                "target_reps": 20,
                "note": "最后一组降速",
            },
        )
        self.assertEqual(200, updated.status_code, updated.text)
        self.assertEqual(2, updated.json()["weekday"])

        cleared = self.client.patch(
            f"/api/v1/weekly-plans/{plan_id}",
            headers=headers,
            json={
                "weekday": 2,
                "exercise": "pushup",
                "view": None,
                "sets": None,
                "target_reps": 20,
                "note": None,
            },
        )
        self.assertEqual(200, cleared.status_code, cleared.text)
        self.assertIsNone(cleared.json()["view"])
        self.assertIsNone(cleared.json()["sets"])
        self.assertIsNone(cleared.json()["note"])

        deleted = self.client.delete(
            f"/api/v1/weekly-plans/{plan_id}",
            headers=headers,
        )
        self.assertEqual(200, deleted.status_code, deleted.text)
        current = self.client.get(
            "/api/v1/weekly-plans",
            headers=headers,
        ).json()
        self.assertEqual(2, len(current["items"]))

    def test_legacy_layout_is_migrated_once(self) -> None:
        legacy = {
            "modules": [
                {"id": "todo", "size": "1x2", "column": "left", "order": 0},
                {"id": "trend", "size": "1x2", "column": "right", "order": 0},
                {"id": "weather", "size": "1x2", "column": "right", "order": 1},
            ]
        }
        with closing(sqlite3.connect(self.settings.db_path)) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO mirror_layout (id, payload, updated_at)
                VALUES (1, ?, ?)
                """,
                (
                    json.dumps(legacy),
                    "2026-11-17T00:00:00.000Z",
                ),
            )
            connection.commit()
        headers = {"Authorization": f"Bearer {self.token}"}
        migrated = self.client.get(
            "/api/v1/mirror/layout",
            headers=headers,
        ).json()
        self.assertEqual(2, migrated["version"])
        self.assertEqual(
            {"weather", "trend", "todo", "weekly_plan"},
            {module["id"] for module in migrated["modules"]},
        )
        weekly = next(
            module for module in migrated["modules"]
            if module["id"] == "weekly_plan"
        )
        self.assertEqual("left", weekly["column"])
        self.assertEqual(1, weekly["order"])

        reduced = self.client.put(
            "/api/v1/mirror/layout",
            headers=headers,
            json={
                "modules": [
                    {
                        "id": "weather",
                        "size": "1x2",
                        "column": "left",
                        "order": 0,
                    }
                ]
            },
        ).json()
        self.assertEqual(["weather"], [item["id"] for item in reduced["modules"]])
        reread = self.client.get(
            "/api/v1/mirror/layout",
            headers=headers,
        ).json()
        self.assertEqual(["weather"], [item["id"] for item in reread["modules"]])

    def test_default_layout_contains_all_four_modules(self) -> None:
        layout = self.client.get(
            "/api/v1/mirror/layout",
            headers={"Authorization": f"Bearer {self.token}"},
        ).json()
        self.assertEqual(2, layout["version"])
        self.assertEqual(
            {"weather", "trend", "todo", "weekly_plan"},
            {module["id"] for module in layout["modules"]},
        )

    def test_offline_result_is_published_and_report_is_served(self) -> None:
        session = self.create_session()
        session_id = session["session_id"]
        command_headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "start-offline-1",
        }
        started = self.client.post(
            f"/api/v1/sessions/{session_id}/start",
            headers=command_headers,
        )
        self.assertEqual(200, started.status_code, started.text)
        self.client.post(
            f"/api/v1/internal/sessions/{session_id}/state",
            json={
                "status": "active",
                "t_ms": 0,
                "payload": {
                    "exercise": "squat",
                    "phase": "up",
                    "reps": 8,
                    "confidence": 0.9,
                    "fps": 18.0,
                    "paused": False,
                },
            },
        )
        stopped = self.client.post(
            f"/api/v1/sessions/{session_id}/stop",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "stop-offline-1",
            },
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        job_id = stopped.json()["job_id"]
        ticket = "report-ticket-test"
        summary = {
            "rep_count": 8,
            "quality_status": "pass",
            "active_issue_count": 1,
        }
        Database(self.settings.db_path).update_session(
            session_id,
            status="completed",
            analysis_stage="completed",
            analysis_progress=1.0,
            analysis_summary=summary,
            report_ticket=ticket,
            report_url=f"/api/v1/reports/{ticket}/report_v2.html",
        )
        current = self.client.get(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {self.token}"},
        ).json()
        self.assertEqual("completed", current["status"])
        report = self.client.get(
            f"/api/v1/sessions/{session_id}/report",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(200, report.status_code, report.text)
        self.assertEqual(8, report.json()["summary"]["rep_count"])

        report_dir = (
            self.settings.offline_data_root
            / "jobs"
            / job_id
            / "artifacts"
            / job_id
        )
        (report_dir / "media").mkdir(parents=True)
        (report_dir / "annotated_frames").mkdir()
        (report_dir / "report_v2.html").write_text(
            (
                f'<video src="/api/v1/jobs/{job_id}/media"></video>'
                f'<img src="/api/v1/jobs/{job_id}/frames/evidence.png">'
            ),
            encoding="utf-8",
        )
        public = self.client.get(current["report_url"])
        self.assertEqual(200, public.status_code, public.text)
        self.assertIn(
            f"/api/v1/reports/{current['report_url'].split('/')[4]}/media",
            public.text,
        )


if __name__ == "__main__":
    unittest.main()
