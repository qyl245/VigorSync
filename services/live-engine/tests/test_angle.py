from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.angle import Landmark, calculate_primary_angle


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


if __name__ == "__main__":
    unittest.main()
