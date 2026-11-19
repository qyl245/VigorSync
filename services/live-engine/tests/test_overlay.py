from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.angle import AngleObservation, Landmark
from vigorsync_live.overlay import draw_overlay, mirror_pose_for_display
from vigorsync_live.pose import PoseFrame
from vigorsync_live.state_machine import Phase


class OverlayTests(unittest.TestCase):
    def test_preview_mirror_happens_before_rendering(self) -> None:
        points = [
            Landmark(index, 0.5, 0.5, 0.0, 0.9)
            for index in range(28)
        ]
        points[0] = Landmark(0, 0.2, 0.3, 0.0, 0.9)
        points[1] = Landmark(1, 0.8, 0.3, 0.0, 0.9)
        points[25] = Landmark(25, 0.45, 0.65, 0.0, 0.9)
        points[27] = Landmark(27, 0.45, 0.85, 0.0, 0.9)
        pose = PoseFrame(
            detected=True,
            confidence=0.95,
            landmarks=tuple(points),
            angle=AngleObservation(170.0, 0.9, 170.0, None),
        )
        mirrored = mirror_pose_for_display(pose)
        self.assertAlmostEqual(mirrored.landmarks[0].x, 0.8)
        self.assertAlmostEqual(mirrored.landmarks[1].x, 0.2)
        self.assertEqual(pose.landmarks[0].x, 0.2)

        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        output = draw_overlay(
            frame,
            mirrored,
            exercise="squat",
            phase=Phase.UP,
            reps=1,
            target_reps=10,
            fps=20.0,
            paused=False,
            show_metrics=False,
        )
        self.assertEqual(frame.shape, output.shape)
        self.assertGreater(int(output.sum()), 0)
        highlighted = draw_overlay(
            frame,
            mirrored,
            exercise="squat",
            phase=Phase.UP,
            reps=1,
            target_reps=10,
            fps=20.0,
            paused=False,
            show_metrics=False,
            highlight_joints=("left_knee",),
        )
        self.assertFalse(np.array_equal(output, highlighted))


if __name__ == "__main__":
    unittest.main()
