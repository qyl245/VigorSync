"""Joint-angle calculation used by the real-time rep state machines."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class Landmark:
    id: int
    x: float
    y: float
    z: float
    visibility: float


@dataclass(frozen=True, slots=True)
class AngleObservation:
    angle_deg: float | None
    confidence: float
    left_deg: float | None
    right_deg: float | None


JOINT_TRIPLETS = {
    "squat": {
        "left": (23, 25, 27),
        "right": (24, 26, 28),
    },
    "pushup": {
        "left": (11, 13, 15),
        "right": (12, 14, 16),
    },
}

PUSHUP_POSTURE_LANDMARKS = {
    "side_left": ((11, 27), (12, 28), (11, 23), (12, 24)),
    "oblique_left": ((11, 27), (12, 28), (11, 23), (12, 24)),
    "side_right": ((12, 28), (11, 27), (12, 24), (11, 23)),
    "oblique_right": ((12, 28), (11, 27), (12, 24), (11, 23)),
}


def angle_deg(first: np.ndarray, center: np.ndarray, last: np.ndarray) -> float | None:
    """Return the geometric angle at ``center`` in degrees."""
    first_vector = first - center
    second_vector = last - center
    denominator = float(
        np.linalg.norm(first_vector) * np.linalg.norm(second_vector)
    )
    if denominator < 1e-8:
        return None
    cosine = float(
        np.dot(first_vector, second_vector) / denominator
    )
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def _side_angle(
    by_id: dict[int, Landmark],
    indices: tuple[int, int, int],
    min_visibility: float,
) -> tuple[float | None, float]:
    try:
        points = [by_id[index] for index in indices]
    except KeyError:
        return None, 0.0
    confidence = min(point.visibility for point in points)
    if confidence < min_visibility:
        return None, confidence
    value = angle_deg(
        np.array([points[0].x, points[0].y, points[0].z]),
        np.array([points[1].x, points[1].y, points[1].z]),
        np.array([points[2].x, points[2].y, points[2].z]),
    )
    return value, confidence


def calculate_primary_angle(
    landmarks: Sequence[Landmark],
    exercise: str,
    view: str,
    min_visibility: float = 0.5,
) -> AngleObservation:
    """Select the camera-appropriate knee or elbow angle.

    Side and oblique views prefer their corresponding image side. Front/rear
    views use the mean of both valid sides. If the preferred side has low
    visibility, the other valid side is used so the UI can keep functioning.
    """
    if exercise not in JOINT_TRIPLETS:
        raise ValueError(f"unsupported exercise: {exercise}")
    by_id = {landmark.id: landmark for landmark in landmarks}
    left, left_confidence = _side_angle(
        by_id, JOINT_TRIPLETS[exercise]["left"], min_visibility
    )
    right, right_confidence = _side_angle(
        by_id, JOINT_TRIPLETS[exercise]["right"], min_visibility
    )

    candidates = [
        ("left", left, left_confidence),
        ("right", right, right_confidence),
    ]
    valid = [item for item in candidates if item[1] is not None]
    if not valid:
        return AngleObservation(None, max(left_confidence, right_confidence), left, right)

    preferred_side = {
        "side_left": "left",
        "oblique_left": "left",
        "side_right": "right",
        "oblique_right": "right",
    }.get(view)
    if preferred_side in {"left", "right"} and view not in {"front", "rear"}:
        preferred = next(
            (item for item in valid if item[0] == preferred_side),
            None,
        )
        if preferred is not None:
            return AngleObservation(
                round(float(preferred[1]), 6),
                min(1.0, float(preferred[2])),
                left,
                right,
            )

    angle = float(np.mean([float(item[1]) for item in valid]))
    confidence = float(np.mean([float(item[2]) for item in valid]))
    return AngleObservation(
        round(angle, 6),
        min(1.0, confidence),
        left,
        right,
    )


def pushup_posture_valid(
    landmarks: Sequence[Landmark],
    view: str,
    frame_width: int,
    frame_height: int,
    min_visibility: float = 0.5,
    max_vertical_deg: float = 35.0,
) -> bool:
    """Reject side-view push-up reps performed while standing or walking away."""
    candidates = PUSHUP_POSTURE_LANDMARKS.get(view)
    if candidates is None:
        return True
    by_id = {landmark.id: landmark for landmark in landmarks}
    evaluated = False
    for index, (first_id, second_id) in enumerate(candidates):
        try:
            first = by_id[first_id]
            second = by_id[second_id]
        except KeyError:
            continue
        if min(first.visibility, second.visibility) < min_visibility:
            continue
        evaluated = True
        horizontal = abs(first.x - second.x) * max(1, frame_width)
        vertical = abs(first.y - second.y) * max(1, frame_height)
        body_angle = math.degrees(
            math.atan2(vertical, max(horizontal, 1e-6))
        )
        limit = max_vertical_deg if index < 2 else 60.0
        if body_angle <= limit:
            return True
    return not evaluated
