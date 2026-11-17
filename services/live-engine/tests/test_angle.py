from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.angle import (
    Landmark,
    calculate_primary_angle,
    pushup_posture_valid,
)


def landmarks_with_knees(
    left_ankle: tuple[float, float],
    right_ankle: tuple[float, float],
):
    values = [
        Landmark(index, 0.5, 0.5, 0.0, 0.99)
        for index in range(33)
    ]
    values[23] = Landmark(23, 0.4, 0.4, 0.0, 0.99)
    values[25] = Landmark(25, 0.5, 0.5, 0.0, 0.99)
    values[27] = Landmark(27, left_ankle[0], left_ankle[1], 0.0, 0.99)
    values[24] = Landmark(24, 0.4, 0.4, 0.0, 0.99)
    values[26] = Landmark(26, 0.5, 0.5, 0.0, 0.99)
    values[28] = Landmark(28, right_ankle[0], right_ankle[1], 0.0, 0.99)
    return values


class AngleTests(unittest.TestCase):
    def test_squat_straight_knee(self) -> None:
        landmarks = landmarks_with_knees(
            (0.6, 0.6),
            (0.6, 0.6),
        )
        observation = calculate_primary_angle(landmarks, "squat", "side_left")
        self.assertAlmostEqual(observation.angle_deg or 0, 180.0, places=5)

    def test_squat_bent_knee(self) -> None:
        landmarks = landmarks_with_knees(
            (0.6, 0.4),
            (0.6, 0.4),
        )
        observation = calculate_primary_angle(landmarks, "squat", "side_left")
        self.assertAlmostEqual(observation.angle_deg or 0, 90.0, places=5)

    def test_low_visibility_is_rejected(self) -> None:
        landmarks = landmarks_with_knees(
            (0.6, 0.6),
            (0.6, 0.6),
        )
        landmarks[25] = Landmark(25, 0.4, 0.5, 0.0, 0.1)
        observation = calculate_primary_angle(
            landmarks,
            "squat",
            "side_left",
            min_visibility=0.5,
        )
        self.assertIsNone(observation.left_deg)
        self.assertIsNotNone(observation.right_deg)

    def test_pushup_posture_rejects_vertical_torso(self) -> None:
        landmarks = [
            Landmark(index, 0.5, 0.5, 0.0, 0.99)
            for index in range(33)
        ]
        landmarks[11] = Landmark(11, 0.4, 0.5, 0.0, 0.99)
        landmarks[27] = Landmark(27, 0.6, 0.5, 0.0, 0.99)
        landmarks[12] = Landmark(12, 0.4, 0.5, 0.0, 0.99)
        landmarks[28] = Landmark(28, 0.6, 0.5, 0.0, 0.99)
        self.assertTrue(
            pushup_posture_valid(landmarks, "side_left", 640, 480)
        )

        landmarks[11] = Landmark(11, 0.5, 0.2, 0.0, 0.99)
        landmarks[27] = Landmark(27, 0.5, 0.7, 0.0, 0.99)
        landmarks[12] = Landmark(12, 0.5, 0.2, 0.0, 0.99)
        landmarks[28] = Landmark(28, 0.5, 0.7, 0.0, 0.99)
        self.assertFalse(
            pushup_posture_valid(landmarks, "side_left", 640, 480)
        )


if __name__ == "__main__":
    unittest.main()
