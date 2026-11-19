from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.angle import AngleObservation, Landmark
from vigorsync_live.form_aggregator import FaultAggregator
from vigorsync_live.form_rules import FormFault, FormFaultDetector
from vigorsync_live.pose import PoseFrame
from vigorsync_live.state_machine import Phase


def pose_with(
    positions: dict[int, tuple[float, float]],
    *,
    visibility: float = 0.95,
) -> PoseFrame:
    points = [
        Landmark(index, 0.5, 0.5, 0.0, visibility)
        for index in range(33)
    ]
    for index, (x, y) in positions.items():
        points[index] = replace(points[index], x=x, y=y)
    return PoseFrame(
        detected=True,
        confidence=visibility,
        landmarks=tuple(points),
        angle=AngleObservation(110.0, visibility, 110.0, 110.0),
    )


class FormRuleTests(unittest.TestCase):
    def test_squat_knee_valgus_front_view(self) -> None:
        detector = FormFaultDetector("squat", "front")
        pose = pose_with(
            {
                23: (0.42, 0.50),
                24: (0.58, 0.50),
                25: (0.45, 0.70),
                26: (0.55, 0.70),
                27: (0.28, 0.90),
                28: (0.72, 0.90),
            }
        )
        result = detector.detect(pose, Phase.DOWN, 640, 480)
        self.assertIn("squat_knee_valgus", result.evaluated_codes)
        self.assertEqual(1, len(result.candidates))
        self.assertEqual(
            ("left_knee", "right_knee"),
            result.candidates[0].joints,
        )

    def test_squat_forward_lean_side_view(self) -> None:
        detector = FormFaultDetector("squat", "side_left")
        pose = pose_with(
            {
                11: (0.72, 0.34),
                12: (0.72, 0.36),
                23: (0.48, 0.64),
                24: (0.48, 0.66),
            }
        )
        result = detector.detect(pose, Phase.DOWN, 640, 480)
        self.assertEqual(
            ("squat_torso_lean_excess",),
            tuple(fault.code for fault in result.candidates),
        )

    def test_pushup_hip_sag_side_view(self) -> None:
        detector = FormFaultDetector("pushup", "side_right")
        pose = pose_with(
            {
                11: (0.72, 0.35),
                12: (0.70, 0.35),
                23: (0.50, 0.52),
                24: (0.48, 0.52),
                27: (0.25, 0.35),
                28: (0.23, 0.35),
            }
        )
        result = detector.detect(pose, Phase.DOWN, 640, 480)
        self.assertEqual(
            ("pushup_hip_sag",),
            tuple(fault.code for fault in result.candidates),
        )
        self.assertEqual(("left_hip", "right_hip"), result.candidates[0].joints)

    def test_pushup_mild_hip_sag_uses_lowered_threshold(self) -> None:
        detector = FormFaultDetector("pushup", "side_left")
        base = {
            11: (0.72, 0.35),
            12: (0.70, 0.35),
            27: (0.25, 0.35),
            28: (0.23, 0.35),
        }
        cases = (
            (0.394, ("pushup_hip_sag",)),
            (0.387, ()),
        )
        for hip_y, expected_codes in cases:
            with self.subTest(hip_y=hip_y):
                result = detector.detect(
                    pose_with(
                        {
                            **base,
                            23: (0.50, hip_y),
                            24: (0.48, hip_y),
                        }
                    ),
                    Phase.DOWN,
                    640,
                    480,
                )
                self.assertEqual(
                    expected_codes,
                    tuple(fault.code for fault in result.candidates),
                )

    def test_pushup_elbow_flare_front_view(self) -> None:
        detector = FormFaultDetector("pushup", "front")
        pose = pose_with(
            {
                11: (0.42, 0.42),
                12: (0.58, 0.42),
                13: (0.12, 0.42),
                14: (0.88, 0.42),
                23: (0.46, 0.75),
                24: (0.54, 0.75),
            }
        )
        result = detector.detect(pose, Phase.DOWN, 640, 480)
        self.assertEqual(
            ("pushup_elbow_flare",),
            tuple(fault.code for fault in result.candidates),
        )
        self.assertEqual(
            ("left_elbow", "right_elbow"),
            result.candidates[0].joints,
        )

    def test_low_visibility_is_not_evaluated(self) -> None:
        detector = FormFaultDetector("squat", "front")
        pose = pose_with({}, visibility=0.2)
        result = detector.detect(pose, Phase.DOWN, 640, 480)
        self.assertEqual(frozenset(), result.evaluated_codes)
        self.assertEqual((), result.candidates)


class FaultAggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fault = FormFault(
            code="squat_knee_valgus",
            severity="high",
            message="膝盖向外",
            confidence=0.9,
            joints=("left_knee", "right_knee"),
        )

    def test_alternating_frame_does_not_emit(self) -> None:
        aggregator = FaultAggregator()
        emitted = []
        for index in range(12):
            emitted.extend(
                aggregator.update(
                    [self.fault] if index % 2 == 0 else [],
                    {"squat_knee_valgus"},
                    index * 50,
                )
            )
        self.assertEqual([], emitted)

    def test_four_of_six_emits_once_then_cools_down(self) -> None:
        aggregator = FaultAggregator(cooldown_ms=4000)
        emitted = []
        for index in range(8):
            emitted.extend(
                aggregator.update(
                    [self.fault],
                    {"squat_knee_valgus"},
                    index * 50,
                )
            )
        self.assertEqual([self.fault], emitted)
        self.assertIn(
            "left_knee",
            aggregator.active_joints(350),
        )
        self.assertEqual(
            frozenset(),
            aggregator.active_joints(2000),
        )


if __name__ == "__main__":
    unittest.main()
