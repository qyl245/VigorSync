"""Per-frame exercise form rules.

Rules emit candidates only. Temporal stability, cooldown and user-facing
alerts are handled by :mod:`vigorsync_live.form_aggregator`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .angle import Landmark
from .pose import PoseFrame
from .state_machine import Phase


@dataclass(frozen=True, slots=True)
class FormFault:
    code: str
    severity: str
    message: str
    confidence: float
    joints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FormFaultEvaluation:
    candidates: tuple[FormFault, ...] = ()
    evaluated_codes: frozenset[str] = frozenset()


JOINT_IDS = {
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}

MOVING_PHASES = {Phase.DOWN, Phase.TRANSITION}
FRONT_VIEWS = {"front", "rear"}
SIDE_VIEWS = {"side_left", "side_right", "oblique_left", "oblique_right"}


class FormFaultDetector:
    """Evaluate conservative, camera-appropriate form faults."""

    def __init__(
        self,
        exercise: str,
        view: str,
        *,
        min_visibility: float = 0.5,
    ) -> None:
        if exercise not in {"squat", "pushup"}:
            raise ValueError(f"unsupported exercise: {exercise}")
        self.exercise = exercise
        self.view = view
        self.min_visibility = min_visibility

    def detect(
        self,
        pose: PoseFrame,
        phase: Phase,
        frame_width: int,
        frame_height: int,
    ) -> FormFaultEvaluation:
        if not pose.detected or phase not in MOVING_PHASES:
            return FormFaultEvaluation()

        if self.exercise == "squat":
            if self.view in FRONT_VIEWS:
                candidates, evaluated = self._squat_knee_valgus(
                    pose.landmarks,
                    frame_width,
                )
                return FormFaultEvaluation(
                    candidates=candidates,
                    evaluated_codes=(
                        frozenset({"squat_knee_valgus"})
                        if evaluated
                        else frozenset()
                    ),
                )
            if self.view in SIDE_VIEWS:
                candidates, evaluated = self._squat_torso_lean(
                    pose.landmarks,
                    frame_width,
                    frame_height,
                )
                return FormFaultEvaluation(
                    candidates=candidates,
                    evaluated_codes=(
                        frozenset({"squat_torso_lean_excess"})
                        if evaluated
                        else frozenset()
                    ),
                )

        if self.exercise == "pushup":
            if self.view in SIDE_VIEWS:
                candidates, evaluated = self._pushup_body_line(
                    pose.landmarks,
                    frame_width,
                    frame_height,
                )
                return FormFaultEvaluation(
                    candidates=candidates,
                    evaluated_codes=(
                        frozenset(
                            {"pushup_hip_sag", "pushup_hip_pike"}
                        )
                        if evaluated
                        else frozenset()
                    ),
                )
            if self.view in FRONT_VIEWS:
                candidates, evaluated = self._pushup_elbow_flare(
                    pose.landmarks,
                    frame_width,
                    frame_height,
                )
                return FormFaultEvaluation(
                    candidates=candidates,
                    evaluated_codes=(
                        frozenset({"pushup_elbow_flare"})
                        if evaluated
                        else frozenset()
                    ),
                )
        return FormFaultEvaluation()

    def _landmarks(
        self,
        landmarks: Iterable[Landmark],
        names: Iterable[str],
    ) -> dict[str, Landmark] | None:
        by_id = {landmark.id: landmark for landmark in landmarks}
        output = {}
        for name in names:
            landmark = by_id.get(JOINT_IDS[name])
            if landmark is None or landmark.visibility < self.min_visibility:
                return None
            output[name] = landmark
        return output

    @staticmethod
    def _confidence(points: Iterable[Landmark]) -> float:
        values = [point.visibility for point in points]
        return round(min(values), 6) if values else 0.0

    def _squat_knee_valgus(
        self,
        landmarks: Iterable[Landmark],
        frame_width: int,
    ) -> tuple[tuple[FormFault, ...], bool]:
        names = (
            "left_hip",
            "right_hip",
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
        )
        points = self._landmarks(landmarks, names)
        if points is None:
            return (), False
        center_x = (
            points["left_hip"].x + points["right_hip"].x
        ) / 2.0
        hip_width_px = abs(
            points["right_hip"].x - points["left_hip"].x
        ) * max(1, frame_width)
        if hip_width_px < 1e-6:
            return (), False

        affected = []
        for side in ("left", "right"):
            knee = points[f"{side}_knee"]
            ankle = points[f"{side}_ankle"]
            ankle_outward = abs(ankle.x - center_x) * frame_width
            knee_outward = abs(knee.x - center_x) * frame_width
            inward_ratio = (ankle_outward - knee_outward) / hip_width_px
            if inward_ratio > 0.22:
                affected.append(f"{side}_knee")
        if not affected:
            return (), True
        return (
            (
                FormFault(
                    code="squat_knee_valgus",
                    severity="high",
                    message="膝盖向外",
                    confidence=self._confidence(points.values()),
                    joints=tuple(affected),
                ),
            ),
            True,
        )

    def _squat_torso_lean(
        self,
        landmarks: Iterable[Landmark],
        frame_width: int,
        frame_height: int,
    ) -> tuple[tuple[FormFault, ...], bool]:
        points = self._landmarks(
            landmarks,
            (
                "left_shoulder",
                "right_shoulder",
                "left_hip",
                "right_hip",
            ),
        )
        if points is None:
            return (), False
        shoulder_x = (
            points["left_shoulder"].x + points["right_shoulder"].x
        ) / 2.0
        shoulder_y = (
            points["left_shoulder"].y + points["right_shoulder"].y
        ) / 2.0
        hip_x = (points["left_hip"].x + points["right_hip"].x) / 2.0
        hip_y = (points["left_hip"].y + points["right_hip"].y) / 2.0
        dx = abs(shoulder_x - hip_x) * max(1, frame_width)
        dy = abs(shoulder_y - hip_y) * max(1, frame_height)
        if dy < 1e-6:
            return (), False
        lean_deg = math.degrees(math.atan2(dx, dy))
        if lean_deg <= 45.0:
            return (), True
        return (
            (
                FormFault(
                    code="squat_torso_lean_excess",
                    severity="medium",
                    message="胸口抬起",
                    confidence=self._confidence(points.values()),
                    joints=(
                        "left_shoulder",
                        "right_shoulder",
                        "left_hip",
                        "right_hip",
                    ),
                ),
            ),
            True,
        )

    def _pushup_body_line(
        self,
        landmarks: Iterable[Landmark],
        frame_width: int,
        frame_height: int,
    ) -> tuple[tuple[FormFault, ...], bool]:
        points = self._landmarks(
            landmarks,
            (
                "left_shoulder",
                "right_shoulder",
                "left_hip",
                "right_hip",
                "left_ankle",
                "right_ankle",
            ),
        )
        if points is None:
            return (), False
        shoulder_x = (
            points["left_shoulder"].x + points["right_shoulder"].x
        ) / 2.0
        shoulder_y = (
            points["left_shoulder"].y + points["right_shoulder"].y
        ) / 2.0
        hip_x = (points["left_hip"].x + points["right_hip"].x) / 2.0
        hip_y = (points["left_hip"].y + points["right_hip"].y) / 2.0
        ankle_x = (
            points["left_ankle"].x + points["right_ankle"].x
        ) / 2.0
        ankle_y = (
            points["left_ankle"].y + points["right_ankle"].y
        ) / 2.0
        body_x = (ankle_x - shoulder_x) * frame_width
        body_y = (ankle_y - shoulder_y) * frame_height
        body_length = math.hypot(body_x, body_y)
        if body_length < max(1, frame_height) * 0.15:
            return (), False
        span = body_x * body_x + body_y * body_y
        hip_dx = (hip_x - shoulder_x) * frame_width
        hip_dy = (hip_y - shoulder_y) * frame_height
        projection = (hip_dx * body_x + hip_dy * body_y) / span
        if not 0.12 <= projection <= 0.88:
            return (), False
        expected_y = shoulder_y + projection * (ankle_y - shoulder_y)
        vertical_ratio = (hip_y - expected_y) * frame_height / body_length
        confidence = self._confidence(points.values())
        if vertical_ratio > 0.065:
            return (
                (
                    FormFault(
                        code="pushup_hip_sag",
                        severity="high",
                        message="收紧核心",
                        confidence=confidence,
                        joints=("left_hip", "right_hip"),
                    ),
                ),
                True,
            )
        if vertical_ratio < -0.08:
            return (
                (
                    FormFault(
                        code="pushup_hip_pike",
                        severity="medium",
                        message="髋部放低",
                        confidence=confidence,
                        joints=("left_hip", "right_hip"),
                    ),
                ),
                True,
            )
        return (), True

    def _pushup_elbow_flare(
        self,
        landmarks: Iterable[Landmark],
        frame_width: int,
        frame_height: int,
    ) -> tuple[tuple[FormFault, ...], bool]:
        names = (
            "left_shoulder",
            "right_shoulder",
            "left_elbow",
            "right_elbow",
            "left_hip",
            "right_hip",
        )
        points = self._landmarks(landmarks, names)
        if points is None:
            return (), False
        affected = []
        for side in ("left", "right"):
            angle = _angle_deg_pixels(
                points[f"{side}_elbow"],
                points[f"{side}_shoulder"],
                points[f"{side}_hip"],
                frame_width,
                frame_height,
            )
            if angle is not None and angle > 80.0:
                affected.append(f"{side}_elbow")
        if not affected:
            return (), True
        return (
            (
                FormFault(
                    code="pushup_elbow_flare",
                    severity="high",
                    message="手肘内收",
                    confidence=self._confidence(points.values()),
                    joints=tuple(affected),
                ),
            ),
            True,
        )


def _angle_deg_pixels(
    first: Landmark,
    center: Landmark,
    last: Landmark,
    frame_width: int,
    frame_height: int,
) -> float | None:
    ax = (first.x - center.x) * frame_width
    ay = (first.y - center.y) * frame_height
    bx = (last.x - center.x) * frame_width
    by = (last.y - center.y) * frame_height
    denominator = math.hypot(ax, ay) * math.hypot(bx, by)
    if denominator < 1e-8:
        return None
    cosine = (ax * bx + ay * by) / denominator
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
