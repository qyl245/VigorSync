from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_offline import app as adapter


class OfflineWorkerAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_session_root = adapter._SESSION_ROOT
        self.old_jobs_dir = adapter.vls_config.JOBS_DIR
        self.old_uploads_dir = adapter.vls_config.UPLOADS_DIR
        self.old_db_path = adapter.vls_config.DB_PATH
        self.old_enqueue = adapter.vls_manager.enqueue

        adapter._SESSION_ROOT = self.root / "sessions"
        adapter.vls_config.JOBS_DIR = self.root / "jobs"
        adapter.vls_config.UPLOADS_DIR = self.root / "uploads"
        adapter.vls_config.DB_PATH = self.root / "app.db"
        adapter._SESSION_ROOT.mkdir()
        adapter.vls_config.JOBS_DIR.mkdir()
        adapter.vls_config.UPLOADS_DIR.mkdir()
        adapter.vls_db.init_db()
        adapter.vls_manager.enqueue = lambda _: None
        self.client = TestClient(adapter.app)

    def tearDown(self) -> None:
        self.client.close()
        adapter._SESSION_ROOT = self.old_session_root
        adapter.vls_config.JOBS_DIR = self.old_jobs_dir
        adapter.vls_config.UPLOADS_DIR = self.old_uploads_dir
        adapter.vls_config.DB_PATH = self.old_db_path
        adapter.vls_manager.enqueue = self.old_enqueue
        self.temp.cleanup()

    def payload(
        self,
        source: Path,
        realtime_events: Path | None = None,
    ) -> dict[str, str]:
        payload = {
            "job_id": "job_0123456789abcdef",
            "session_id": "s_0123456789abcdef01234567",
            "source_path": str(source),
            "exercise": "pushup",
            "view": "side_right",
            "rotation": "none",
        }
        if realtime_events is not None:
            payload["realtime_events_path"] = str(realtime_events)
        return payload

    def test_internal_enqueue_is_authenticated_and_idempotent(self) -> None:
        session_dir = adapter._SESSION_ROOT / "s_0123456789abcdef01234567"
        (session_dir / "input").mkdir(parents=True)
        source = session_dir / "input" / "source.mp4"
        source.write_bytes(b"video")
        events = session_dir / "live" / "events.jsonl"
        events.parent.mkdir()
        events.write_text(
            '{"type":"form.alert","t_ms":1200,"payload":'
            '{"code":"pushup_hip_sag","severity":"high",'
            '"message":"收紧核心"}}\n',
            encoding="utf-8",
        )
        headers = {"X-Internal-Token": adapter._OFFLINE_TOKEN}

        denied = self.client.post(
            "/internal/v1/jobs",
            json=self.payload(source),
        )
        self.assertEqual(401, denied.status_code)

        first = self.client.post(
            "/internal/v1/jobs",
            headers=headers,
            json=self.payload(source, events),
        )
        second = self.client.post(
            "/internal/v1/jobs",
            headers=headers,
            json=self.payload(source, events),
        )
        self.assertEqual(202, first.status_code, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(
            (adapter.vls_config.JOBS_DIR / "job_0123456789abcdef" / "input").is_dir()
        )
        self.assertEqual(
            events.read_bytes(),
            (
                adapter.vls_config.JOBS_DIR
                / "job_0123456789abcdef"
                / "input"
                / "realtime-events.jsonl"
            ).read_bytes(),
        )

    def test_source_must_stay_inside_session_root(self) -> None:
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"video")
        response = self.client.post(
            "/internal/v1/jobs",
            headers={"X-Internal-Token": adapter._OFFLINE_TOKEN},
            json=self.payload(outside),
        )
        self.assertEqual(400, response.status_code)

    def test_realtime_events_must_stay_inside_session(self) -> None:
        session_dir = adapter._SESSION_ROOT / "s_0123456789abcdef01234567"
        (session_dir / "input").mkdir(parents=True)
        source = session_dir / "input" / "source.mp4"
        source.write_bytes(b"video")
        outside = self.root / "outside-events.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        response = self.client.post(
            "/internal/v1/jobs",
            headers={"X-Internal-Token": adapter._OFFLINE_TOKEN},
            json=self.payload(source, outside),
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual(
            "realtime_events_outside_session",
            response.json()["detail"],
        )


if __name__ == "__main__":
    unittest.main()
