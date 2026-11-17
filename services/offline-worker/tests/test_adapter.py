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

    def payload(self, source: Path) -> dict[str, str]:
        return {
            "job_id": "job_0123456789abcdef",
            "session_id": "s_0123456789abcdef01234567",
            "source_path": str(source),
            "exercise": "pushup",
            "view": "side_right",
            "rotation": "none",
        }

    def test_internal_enqueue_is_authenticated_and_idempotent(self) -> None:
        session_dir = adapter._SESSION_ROOT / "s_0123456789abcdef01234567"
        (session_dir / "input").mkdir(parents=True)
        source = session_dir / "input" / "source.mp4"
        source.write_bytes(b"video")
        headers = {"X-Internal-Token": adapter._OFFLINE_TOKEN}

        denied = self.client.post(
            "/internal/v1/jobs",
            json=self.payload(source),
        )
        self.assertEqual(401, denied.status_code)

        first = self.client.post(
            "/internal/v1/jobs",
            headers=headers,
            json=self.payload(source),
        )
        second = self.client.post(
            "/internal/v1/jobs",
            headers=headers,
            json=self.payload(source),
        )
        self.assertEqual(202, first.status_code, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(
            (adapter.vls_config.JOBS_DIR / "job_0123456789abcdef" / "input").is_dir()
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


if __name__ == "__main__":
    unittest.main()
