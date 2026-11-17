from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_offline import app as adapter  # noqa: F401 - initializes vendor path
from app.repchain import analysis_v1


def frame_quality(count: int, first_bad: int | None = None) -> list[dict[str, object]]:
    rows = []
    for index in range(count):
        good = first_bad is None or index < first_bad
        rows.append(
            {
                "clip_frame_index": index,
                "detected": True,
                "mean_visibility": 0.95 if good else 0.6,
                "visible_joint_fraction": 1.0 if good else 0.4,
                "all_joints_visible": good,
                "in_frame": good,
            }
        )
    return rows


class QualityScopeTests(unittest.TestCase):
    def test_quality_gate_ignores_setup_and_stop_transition(self) -> None:
        rows = frame_quality(40, first_bad=31)
        pose_quality = {
            "expected_frames": 40,
            "frame_coverage": 1.0,
            **analysis_v1.summarize_pose_frame_quality(rows),
            "processing_fps": 30.0,
        }
        segmentation = {
            "phase_cycle_detection": {
                "boundary_frames": [0, 10, 20, 30],
            },
            "posture_gate": {
                "rejected_rep_count": 0,
                "reps": [
                    {"accepted": True, "bottom_frame": 5},
                    {"accepted": True, "bottom_frame": 15},
                    {"accepted": True, "bottom_frame": 25},
                ],
            },
            "reps": [{} for _ in range(3)],
            "rejected_reps": [],
            "signal_selection": {"selected_angle": "right_elbow_flexion"},
        }
        quality = analysis_v1.build_quality(
            {
                "view": "side_right",
                "usable_metrics": ["elbow_flexion_rom"],
                "disabled_metrics": [],
            },
            pose_quality,
            segmentation,
            rows,
        )
        self.assertEqual([], quality["failures"])
        self.assertEqual("pass", quality["status"])
        self.assertEqual(1.0, quality["checks"]["body_in_frame_rate"])
        self.assertEqual(
            0.775,
            quality["checks"]["full_clip_body_in_frame_rate"],
        )
        self.assertEqual(
            [{"start_frame": 0, "end_frame": 30}],
            quality["scope"]["review_intervals"],
        )

    def test_quality_gate_still_rejects_bad_exercise_frames(self) -> None:
        rows = frame_quality(40)
        for row in rows[:31]:
            row["in_frame"] = False
        pose_quality = {
            "expected_frames": 40,
            "frame_coverage": 1.0,
            **analysis_v1.summarize_pose_frame_quality(rows),
            "processing_fps": 30.0,
        }
        segmentation = {
            "phase_cycle_detection": {"boundary_frames": [0, 10, 20, 30]},
            "posture_gate": {
                "rejected_rep_count": 0,
                "reps": [
                    {"accepted": True, "bottom_frame": 5},
                    {"accepted": True, "bottom_frame": 15},
                    {"accepted": True, "bottom_frame": 25},
                ],
            },
            "reps": [{} for _ in range(3)],
            "rejected_reps": [],
            "signal_selection": {"selected_angle": "right_elbow_flexion"},
        }
        quality = analysis_v1.build_quality(
            {
                "view": "side_right",
                "usable_metrics": ["elbow_flexion_rom"],
                "disabled_metrics": [],
            },
            pose_quality,
            segmentation,
            rows,
        )
        self.assertIn(
            "body_in_frame_rate_below_reject_threshold",
            quality["failures"],
        )


if __name__ == "__main__":
    unittest.main()
