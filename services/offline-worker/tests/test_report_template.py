from __future__ import annotations

import json
import math
import sys
import numpy as np
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_offline import app as adapter  # noqa: F401 - initializes vendor path
from motion_metrics import build_process_metrics
from app.repchain.feedback_builder import (
    MOTION_JOINT_IDS,
    _predict_side_view_far_limbs,
    _smooth_landmarks,
    build_motion_payload,
    build_report_html,
    build_report_timeline,
)


def synthetic_track():
    frame_count = 61
    points = np.zeros((frame_count, 33, 3), dtype=np.float64)
    visibility = np.ones((frame_count, 33), dtype=np.float64)
    predicted = np.zeros((frame_count, 33), dtype=bool)
    times = np.arange(frame_count, dtype=np.float64) / 30.0
    rep_ids: list[int | None] = [1 if index < 30 else 2 for index in range(frame_count)]
    reps = [
        {"rep_id": 1, "start_s": 0.0, "end_s": 1.0, "bottom_s": 0.5},
        {"rep_id": 2, "start_s": 1.0, "end_s": 2.0, "bottom_s": 1.5},
    ]
    base = {
        0: (0.0, 1.72, 0.0), 2: (-0.03, 1.69, 0.0), 5: (0.03, 1.69, 0.0),
        7: (-0.07, 1.68, 0.0), 8: (0.07, 1.68, 0.0),
        11: (-0.20, 1.42, 0.0), 12: (0.20, 1.42, 0.0),
        13: (-0.30, 1.15, 0.0), 14: (0.30, 1.15, 0.0),
        15: (-0.32, 0.92, 0.0), 16: (0.32, 0.92, 0.0),
        23: (-0.12, 0.95, 0.0), 24: (0.12, 0.95, 0.0),
        25: (-0.13, 0.52, -0.04), 26: (0.13, 0.52, 0.04),
        27: (-0.13, 0.08, 0.0), 28: (0.13, 0.08, 0.0),
        29: (-0.13, 0.03, -0.07), 30: (0.13, 0.03, -0.07),
        31: (-0.13, 0.03, 0.20), 32: (0.13, 0.03, 0.20),
    }
    for frame_index, time_s in enumerate(times):
        phase = np.sin(2.0 * np.pi * (time_s % 1.0))
        for joint_id, (x, y, z) in base.items():
            points[frame_index, joint_id] = (x, y, z)
        points[frame_index, 0, 2] += 0.06 * phase
        points[frame_index, 11, 2] += 0.10 * phase
        points[frame_index, 12, 2] += 0.10 * phase
        points[frame_index, 25, 2] += 0.10 * phase
        points[frame_index, 26, 2] += 0.18 * phase
    return points, visibility, predicted, times, rep_ids, reps


class ReportTemplateTests(unittest.TestCase):
    def test_evidence_frame_font_renders_distinct_chinese_glyphs(self) -> None:
        from PIL import Image, ImageDraw
        from render_clip_report import load_font

        font = load_font(28)

        def glyph(char: str) -> np.ndarray:
            image = Image.new("L", (64, 64))
            ImageDraw.Draw(image).text((4, 4), char, font=font, fill=255)
            return np.asarray(image)

        self.assertFalse(np.array_equal(glyph("膝"), glyph("盖")))

    def test_branded_report_contains_summary_and_issue(self) -> None:
        feedback = {
            "clip_id": "job_test",
            "exercise": "squat",
            "view": "front",
            "state_message": "研究候选反馈",
            "assessment_summary": {
                "rep_count": 10,
                "evaluable_metric_count": 6,
                "quality_status": "warn",
            },
            "cards": [
                {
                    "issue_id": "squat_knee_valgus_01",
                    "severity": "medium",
                    "confidence": 0.65,
                    "title": "膝盖内扣",
                    "detail": "rep 8 存在偏差",
                    "cue": "让膝盖朝向脚尖",
                    "limitation": "仅正面视角评估",
                    "representative_rep_id": 8,
                    "highlight_joints": ["left_knee", "right_knee"],
                    "frequency": "4/10 reps",
                    "anchor_time_s": 3.2,
                    "evidence_interval_s": [3.0, 3.4],
                    "anchor_fallback": False,
                    "frame_available": False,
                    "frame_filename": None,
                }
            ],
            "limitations": [],
        }
        timeline = build_report_timeline(
            {
                "source_measurement": {
                    "input_contract": {
                        "fps": 30.0,
                        "frame_count": 762,
                    }
                },
                "reps": [
                    {
                        "rep_id": 8,
                        "start_frame": 470,
                        "end_frame": 534,
                        "bottom_frame": 487,
                        "boundary_confidence": 0.74439,
                        "status": "complete",
                    }
                ],
            }
        )
        html = build_report_html(
            feedback,
            media_url="/api/v1/report/media",
            frame_url_template="/api/v1/report/frames/{frame_name}",
            timeline=timeline,
        )
        self.assertIn("VIGORSYNC", html)
        self.assertIn("离线复盘", html)
        self.assertIn("膝盖内扣", html)
        self.assertIn("class=\"issue-card medium\"", html)
        self.assertIn("object-fit:contain", html)
        self.assertIn("REP TIMELINE", html)
        self.assertIn("\"rep_id\":8", html)
        self.assertIn("\"start_s\":15.666667", html)
        self.assertIn("player.addEventListener('timeupdate'", html)
        self.assertIn("function playEvidenceInterval", html)
        self.assertIn("function revealPlayback", html)
        self.assertIn("visual.scrollIntoView", html)
        self.assertIn("requestAnimationFrame(monitorEvidencePlayback)", html)
        self.assertIn("证据区间播放完成", html)
        self.assertIn("3D POSE REVIEW", html)
        self.assertIn("3D 骨架", html)
        self.assertNotIn('id="motion-trace"', html)
        self.assertIn('data-joints="25,26"', html)
        self.assertIn("const data = null", html)
        self.assertIn("脚底地面坐标 · Y=0 · 单位 m", html)
        self.assertIn("坐标：X 左右 · Y 垂直向上 · Z 前后", html)
        self.assertIn("虚线部分为遮挡期间的姿态预测", html)
        self.assertLess(html.index('class="visual"'), html.index('class="motion-card"'))
        self.assertLess(html.index('class="motion-card"'), html.index('class="snapshot"'))
        self.assertIn("PROCESS METRICS", html)
        self.assertIn("过程指标", html)
        self.assertIn("工程阈值 · 非临床", html)

    def test_motion_payload_uses_body_frame_and_visibility(self) -> None:
        analysis = {
            "clip_id": "job_motion",
            "exercise": "squat",
            "fps": 30.0,
        }
        timeline = {
            "duration_s": 1.0,
            "reps": [
                {
                    "rep_id": 2,
                    "start_s": 0.2,
                    "end_s": 0.8,
                    "bottom_s": 0.5,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            analysis_dir = Path(temporary)
            angle_dir = analysis_dir / "job_motion" / "angles"
            angle_dir.mkdir(parents=True)
            body_frames = []
            pose_frames = []
            for frame_id in range(31):
                body_points = []
                world_points = []
                visibility = []
                ground_y = 0.55 + 0.2 * math.sin(frame_id * 2 * math.pi / 20)
                for joint_id in range(33):
                    body_points.append([joint_id * 0.001, frame_id * 0.002, -0.1])
                    point_y = ground_y if joint_id in (27, 28, 29, 30, 31, 32) else 0.1
                    world_points.append([joint_id * 0.002, point_y, -0.2])
                    visibility.append(0.9 if joint_id in MOTION_JOINT_IDS or joint_id in (29, 30, 31, 32) else 0.0)
                body_frames.append(
                    {
                        "frame_id": frame_id,
                        "timestamp_ms": round(frame_id * 1000 / 30),
                        "landmarks_body_frame": body_points,
                        "detected": True,
                    }
                )
                normalized_points = [
                    [0.25 + joint_id * 0.01, 0.2 + joint_id * 0.015, 0.0]
                    for joint_id in range(33)
                ]
                pose_frames.append(
                    {
                        "frame_id": frame_id,
                        "landmarks_normalized": normalized_points,
                        "landmarks_world": world_points,
                        "visibility": visibility,
                        "detected": True,
                    }
                )
            (angle_dir / "landmarks_body_frame.json").write_text(
                json.dumps(body_frames),
                encoding="utf-8",
            )
            (angle_dir / "landmarks.json").write_text(
                json.dumps(pose_frames),
                encoding="utf-8",
            )

            payload = build_motion_payload(analysis_dir, analysis, timeline)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual("mediapipe_world_stabilized", payload["source"])
        self.assertTrue(payload["video_connections"])
        self.assertEqual("foot_ground", payload["coordinate_system"]["name"])
        self.assertEqual(0.0, payload["coordinate_system"]["floor_y"])
        self.assertEqual(list(MOTION_JOINT_IDS), payload["joints"])
        self.assertIn(0, payload["joints"])
        self.assertIn(7, payload["joints"])
        self.assertIn(8, payload["joints"])
        self.assertIn([2, 7], payload["bones"])
        self.assertNotIn([7, 11], payload["bones"])
        self.assertGreater(len(payload["frames"]), 2)
        self.assertLess(len(payload["frames"]), 31)
        rep_frames = [frame for frame in payload["frames"] if frame[1] == 2]
        self.assertTrue(rep_frames)
        self.assertAlmostEqual(0.9, max(rep_frames[0][3]) / 255, places=1)
        self.assertEqual(len(MOTION_JOINT_IDS) * 3, len(rep_frames[0][2]))
        foot_indices = [MOTION_JOINT_IDS.index(joint_id) for joint_id in (27, 28, 29, 30, 31, 32)]
        foot_minimum = min(rep_frames[0][2][index * 3 + 1] for index in foot_indices)
        self.assertLess(abs(foot_minimum), 0.01)
        for frame in payload["frames"]:
            if not frame[2]:
                continue
            frame_foot_minimum = min(frame[2][index * 3 + 1] for index in foot_indices)
            self.assertLess(abs(frame_foot_minimum), 0.01)
        self.assertEqual(66, len(rep_frames[0][4]))
        self.assertEqual(33, len(rep_frames[0][5]))
        self.assertEqual(len(MOTION_JOINT_IDS), len(rep_frames[0][6]))


    def test_process_metric_payload_and_curves_render(self) -> None:
        points, visibility, predicted, times, rep_ids, reps = synthetic_track()
        process_metrics = build_process_metrics(
            points,
            visibility,
            predicted,
            times,
            rep_ids,
            reps,
            fps=30.0,
            exercise="squat",
            view="front",
        )
        self.assertEqual("vls-process-metrics-v1", process_metrics["schema_version"])
        self.assertEqual(
            {"com_ap_drift", "torso_variability", "symmetry_index"},
            set(process_metrics["metrics"]),
        )
        com = process_metrics["metrics"]["com_ap_drift"]
        self.assertGreater(com["chart"]["warn"], 0)
        self.assertGreater(com["chart"]["alarm"], com["chart"]["warn"])
        self.assertTrue(com["chart"]["series"]["t"])
        self.assertEqual(2, len(com["reps"]))
        self.assertIn(com["status"], {"normal", "attention", "abnormal"})
        self.assertTrue(process_metrics["metrics"]["torso_variability"]["chart"]["series"]["t"])
        self.assertTrue(process_metrics["metrics"]["symmetry_index"]["chart"]["series"]["v"])
        pushup_metrics = build_process_metrics(
            points,
            visibility,
            predicted,
            times,
            rep_ids,
            reps,
            fps=30.0,
            exercise="pushup",
            view="side_right",
        )
        self.assertEqual("pushup", pushup_metrics["exercise"])
        self.assertEqual(3, len(pushup_metrics["metrics"]))

        feedback = {
            "clip_id": "job_process",
            "exercise": "squat",
            "view": "front",
            "state_message": "过程指标测试",
            "assessment_summary": {"rep_count": 2, "quality_status": "pass"},
            "cards": [],
            "limitations": [],
        }
        html = build_report_html(
            feedback,
            media_url="/media",
            frame_url_template="/frames/{frame_name}",
            motion={"duration_s": 2.0, "process_metrics": process_metrics},
        )
        self.assertIn('data-process-metric="com_ap_drift"', html)
        self.assertIn('data-process-metric="torso_variability"', html)
        self.assertIn('data-process-metric="symmetry_index"', html)
        self.assertIn("重心前后漂移", html)
        self.assertIn("躯干角波动", html)
        self.assertIn("左右对称性指数", html)
        self.assertIn("4% 起进入关注、7% 判为异常", html)
        self.assertIn("vls-process-metrics-v1", html)
        self.assertIn("点击曲线可跳转视频", html)

    def test_occlusion_gap_is_predicted_and_long_gap_is_hidden(self) -> None:
        frame_count = 31
        points = np.zeros((frame_count, 33, 3), dtype=np.float64)
        visibility = np.ones((frame_count, 33), dtype=np.float64)
        for frame_id in range(frame_count):
            points[frame_id, :, 1] = frame_id * 0.01
            points[frame_id, :, 0] = np.arange(33) * 0.001
        visibility[6:11, 25] = 0.0
        visibility[12:31, 26] = 0.0
        smoothed, predicted = _smooth_landmarks(points, visibility)
        self.assertTrue(predicted[7, 25])
        self.assertTrue(np.isfinite(smoothed[7, 25]).all())
        self.assertFalse(predicted[20, 26])
        self.assertFalse(np.isfinite(smoothed[20, 26]).all())
        self.assertFalse(predicted[5, 25])


    def test_side_view_predicts_occluded_far_limbs(self) -> None:
        points = np.zeros((3, 33, 3), dtype=np.float64)
        visibility = np.ones((3, 33), dtype=np.float64)
        predicted = np.zeros((3, 33), dtype=bool)
        for frame_id in range(3):
            points[frame_id, 11] = [0.0, 0.0, 0.0]
            points[frame_id, 12] = [1.0, 0.0, 0.0]
            points[frame_id, 13] = [0.1, 0.5, 0.0]
            points[frame_id, 15] = [0.1, 1.0, 0.0]
            points[frame_id, 23] = [0.0, 0.0, 0.0]
            points[frame_id, 24] = [1.0, 0.0, 0.0]
            points[frame_id, 25] = [0.1, 0.5, 0.0]
            points[frame_id, 27] = [0.1, 1.0, 0.0]
            points[frame_id, 29] = [0.1, 1.1, 0.0]
            points[frame_id, 31] = [0.2, 1.1, 0.0]
        visibility[:, 14] = 0.1
        visibility[:, 16] = 0.1
        visibility[:, 26] = 0.1
        visibility[:, 28] = 0.4
        result, predicted_result = _predict_side_view_far_limbs(
            points,
            visibility,
            predicted,
            "side_left",
            "pushup",
        )
        self.assertTrue(predicted_result[0, 14])
        self.assertTrue(predicted_result[0, 16])
        self.assertAlmostEqual(0.9, result[0, 14, 0], places=3)
        self.assertAlmostEqual(0.5, result[0, 14, 1], places=3)
        self.assertAlmostEqual(0.9, result[0, 16, 0], places=3)
        self.assertAlmostEqual(1.0, result[0, 16, 1], places=3)
        self.assertTrue(predicted_result[0, 26])
        self.assertTrue(predicted_result[0, 28])
        self.assertTrue(predicted_result[0, 30])
        self.assertTrue(predicted_result[0, 32])
        self.assertAlmostEqual(0.9, result[0, 26, 0], places=3)
        self.assertAlmostEqual(0.9, result[0, 28, 0], places=3)


if __name__ == "__main__":
    unittest.main()
