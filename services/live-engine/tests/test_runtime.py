from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np


SERVICE_ROOT = Path(__file__).resolve().parents[1]
SRC = SERVICE_ROOT / "src"
PROJECT_ROOT = SERVICE_ROOT.parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import validate_contracts as contracts
from vigorsync_live.angle import AngleObservation, Landmark
from vigorsync_live.config import Settings
from vigorsync_live.pose import PoseFrame
from vigorsync_live.runtime import SessionRequest, SessionRuntime


class FakeSource:
    def __init__(self, count: int = 70) -> None:
        self.count = count
        self.index = 0

    def open(self) -> None:
        time.sleep(0.3)
        return None

    def read(self):
        if self.index >= self.count:
            return None
        self.index += 1
        time.sleep(0.02)
        return np.zeros((48, 64, 3), dtype=np.uint8)

    def close(self) -> None:
        return None


class BurstSource(FakeSource):
    def read(self):
        if self.index >= self.count:
            return None
        self.index += 1
        time.sleep(0.005)
        return np.zeros((48, 64, 3), dtype=np.uint8)


class FakePose:
    def __init__(self) -> None:
        self.index = 0

    def process(self, frame, frame_index: int, t_ms: int) -> PoseFrame:
        del frame, frame_index, t_ms
        if self.index < 8:
            angle = 175.0
        elif self.index < 48:
            angle = 75.0
        else:
            angle = 175.0
        self.index += 1
        landmarks = tuple(
            Landmark(
                id=index,
                x=0.5,
                y=0.5,
                z=0.0,
                visibility=0.95,
            )
            for index in range(33)
        )
        return PoseFrame(
            detected=True,
            confidence=0.95,
            landmarks=landmarks,
            angle=AngleObservation(angle, 0.95, angle, angle),
        )

    def close(self) -> None:
        return None


class RuntimeTests(unittest.TestCase):
    def test_runtime_writes_contract_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(
                project_root=root,
                session_root=root / "sessions",
                host="127.0.0.1",
                port=8020,
                camera_index=0,
                camera_width=64,
                camera_height=48,
                camera_fps=30,
                camera_backend="auto",
                jpeg_quality=80,
                pose_model_complexity=0,
                min_visibility=0.5,
                metric_hz=5,
                internal_token=None,
                enable_dev_api=True,
                allowed_origins=(),
                inference_fps=120.0,
                preview_fps=15.0,
            )
            request = SessionRequest(
                session_id="s_runtime_test",
                exercise="squat",
                view="side_left",
                target_reps=3,
                session_dir=root / "sessions" / "s_runtime_test",
                source_kind="video",
            )
            runtime = SessionRuntime(
                request,
                settings,
                source_factory=lambda: FakeSource(),
                estimator_factory=FakePose,
                recording_fps=30,
            )
            runtime.start()
            deadline = time.monotonic() + 3
            while runtime.status != "active" and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(1.85)
            result = runtime.stop()

            self.assertEqual("finalized", result["status"])
            self.assertGreaterEqual(runtime.latest_metrics["reps"], 1)
            for artifact in result["artifacts"].values():
                path = Path(artifact)
                self.assertTrue(path.is_file(), artifact)
                self.assertGreater(path.stat().st_size, 0, artifact)

            schemas = contracts.load_schemas()
            registry = contracts.build_registry(schemas)
            summary = json.loads(
                Path(result["artifacts"]["summary_path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [],
                contracts.validate_payload(
                    summary,
                    schemas["session-summary.schema.json"],
                    registry,
                ),
            )
            capture = cv2.VideoCapture(result["artifacts"]["video_path"])
            video_duration = (
                capture.get(cv2.CAP_PROP_FRAME_COUNT)
                / max(capture.get(cv2.CAP_PROP_FPS), 1.0)
            )
            capture.release()
            self.assertLess(
                abs(video_duration - summary["duration_s"]),
                0.75,
                "recording timeline must stay aligned with session duration",
            )

            landmark_lines = (
                Path(result["artifacts"]["landmarks_path"])
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertGreater(len(landmark_lines), 0)
            first_landmark = json.loads(landmark_lines[0])
            self.assertLess(
                first_landmark["t_ms"],
                100,
                "landmark time must start at the first recording frame",
            )
            self.assertEqual(
                [],
                contracts.validate_payload(
                    json.loads(landmark_lines[0]),
                    schemas["landmark-frame.schema.json"],
                    registry,
                ),
            )

            event_lines = (
                Path(result["artifacts"]["events_path"])
                .read_text(encoding="utf-8")
                .splitlines()
            )
            event_types = set()
            for line in event_lines:
                event = json.loads(line)
                event_types.add(event["type"])
                self.assertEqual(
                    [],
                    contracts.validate_payload(
                        event,
                        schemas["live-event.schema.json"],
                        registry,
                    ),
                )
            self.assertIn("rep.completed", event_types)
            self.assertIn("session.state", event_types)

    def test_runtime_caps_inference_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(
                project_root=root,
                session_root=root / "sessions",
                host="127.0.0.1",
                port=8020,
                camera_index=0,
                camera_width=64,
                camera_height=48,
                camera_fps=30,
                camera_backend="auto",
                jpeg_quality=80,
                pose_model_complexity=0,
                min_visibility=0.5,
                metric_hz=5,
                internal_token=None,
                enable_dev_api=True,
                allowed_origins=(),
                inference_fps=10.0,
                preview_fps=15.0,
            )
            request = SessionRequest(
                session_id="s_throttle_test",
                exercise="squat",
                view="front",
                target_reps=3,
                session_dir=root / "sessions" / "s_throttle_test",
                source_kind="video",
            )
            runtime = SessionRuntime(
                request,
                settings,
                source_factory=lambda: BurstSource(count=1000),
                estimator_factory=FakePose,
                recording_fps=30,
            )
            runtime.start()
            time.sleep(0.55)
            counts = runtime.counts
            runtime.stop()

            self.assertGreater(counts["throttle_waits"], 0)
            self.assertLessEqual(counts["processed_frames"], 7)

    def test_training_timer_excludes_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(
                project_root=root,
                session_root=root / "sessions",
                host="127.0.0.1",
                port=8020,
                camera_index=0,
                camera_width=64,
                camera_height=48,
                camera_fps=30,
                camera_backend="auto",
                jpeg_quality=80,
                pose_model_complexity=0,
                min_visibility=0.5,
                metric_hz=10,
                internal_token=None,
                enable_dev_api=True,
                allowed_origins=(),
                inference_fps=20.0,
                preview_fps=15.0,
            )
            request = SessionRequest(
                session_id="s_timer_test",
                exercise="squat",
                view="front",
                target_reps=3,
                session_dir=root / "sessions" / "s_timer_test",
                source_kind="video",
            )
            runtime = SessionRuntime(
                request,
                settings,
                source_factory=lambda: BurstSource(count=1000),
                estimator_factory=FakePose,
                recording_fps=30,
            )
            runtime.start()
            deadline = time.monotonic() + 3
            while runtime.status != "active" and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.25)
            runtime.pause()
            time.sleep(0.15)
            paused_at = runtime.latest_metrics["elapsed_s"]
            time.sleep(0.15)
            paused_later = runtime.latest_metrics["elapsed_s"]
            self.assertLess(abs(paused_later - paused_at), 0.08)

            runtime.resume()
            time.sleep(0.2)
            result = runtime.stop()
            self.assertGreater(result["summary"]["elapsed_s"], paused_later + 0.1)


if __name__ == "__main__":
    unittest.main()
