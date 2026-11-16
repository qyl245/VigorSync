"""MediaPipe pose inference and normalized-landmark serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import mediapipe as mp
import numpy as np

from .angle import AngleObservation, Landmark, calculate_primary_angle


CORE_LANDMARKS = (
    11,
    12,
    13,
    14,
    15,
    16,
    23,
    24,
    25,
    26,
    27,
    28,
)


@dataclass(frozen=True, slots=True)
class PoseFrame:
    detected: bool
    confidence: float
    landmarks: tuple[Landmark, ...]
    angle: AngleObservation


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


class PoseEstimator:
    """CPU MediaPipe pose estimator using the package-bundled Pose model.

    Model complexity 0 selects the lite model, matching the M1 performance
    budget. Instances are intentionally created inside the processing thread.
    """

    def __init__(
        self,
        exercise: str,
        view: str,
        model_complexity: int = 0,
        min_visibility: float = 0.5,
    ) -> None:
        self.exercise = exercise
        self.view = view
        self.min_visibility = min_visibility
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=model_complexity,
            smooth_landmarks=True,
            enable_segmentation=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def process(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        t_ms: int,
    ) -> PoseFrame:
        del frame_index, t_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        result = self._pose.process(rgb)
        if result.pose_landmarks is None:
            return PoseFrame(
                detected=False,
                confidence=0.0,
                landmarks=(),
                angle=AngleObservation(None, 0.0, None, None),
            )

        raw = result.pose_landmarks.landmark
        landmarks = tuple(
            Landmark(
                id=index,
                x=_clamp(point.x, 0.0, 1.0),
                y=_clamp(point.y, 0.0, 1.0),
                z=float(point.z),
                visibility=_clamp(point.visibility, 0.0, 1.0),
            )
            for index, point in enumerate(raw)
        )
        confidence = float(
            np.mean(
                [
                    landmarks[index].visibility
                    for index in CORE_LANDMARKS
                    if index < len(landmarks)
                ]
            )
        )
        angle = calculate_primary_angle(
            landmarks,
            self.exercise,
            self.view,
            min_visibility=self.min_visibility,
        )
        return PoseFrame(
            detected=True,
            confidence=round(_clamp(confidence, 0.0, 1.0), 6),
            landmarks=landmarks,
            angle=angle,
        )

    def close(self) -> None:
        self._pose.close()

    def __enter__(self) -> "PoseEstimator":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
