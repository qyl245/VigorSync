from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_core.app import create_app
from vigorsync_core.config import Settings


class FakeLive:
    def __init__(self) -> None:
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
        return {
            "session_id": session_id,
            "status": "finalized",
            "artifacts": {
                "video_path": "/tmp/video.mp4",
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
            mirror_token="mirror-test",
            pairing_code="123456",
        )
        self.live = FakeLive()
        self.app = create_app(self.settings, self.live)
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


if __name__ == "__main__":
    unittest.main()
