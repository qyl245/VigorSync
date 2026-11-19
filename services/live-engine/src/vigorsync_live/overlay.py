"""Draw pose and live metrics onto a BGR preview frame."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

import cv2
import numpy as np

from .angle import Landmark
from .form_rules import JOINT_IDS
from .pose import PoseFrame
from .state_machine import Phase


POSE_CONNECTIONS = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 7),
    (0, 4),
    (4, 5),
    (5, 6),
    (6, 8),
    (9, 10),
    (11, 12),
    (11, 13),
    (13, 15),
    (15, 17),
    (15, 19),
    (15, 21),
    (17, 19),
    (12, 14),
    (14, 16),
    (16, 18),
    (16, 20),
    (16, 22),
    (18, 20),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (24, 26),
    (25, 27),
    (26, 28),
    (27, 29),
    (28, 30),
    (29, 31),
    (30, 32),
    (27, 31),
    (28, 32),
)


def mirror_pose_for_display(pose: PoseFrame) -> PoseFrame:
    """Mirror landmark coordinates for a selfie-style preview.

    The transform is applied before drawing so overlay text remains readable.
    """
    return replace(
        pose,
        landmarks=tuple(
            replace(
                Landmark(
                    id=landmark.id,
                    x=landmark.x,
                    y=landmark.y,
                    z=landmark.z,
                    visibility=landmark.visibility,
                ),
                x=1.0 - landmark.x,
            )
            for landmark in pose.landmarks
        ),
    )


def draw_overlay(
    frame: np.ndarray,
    pose: PoseFrame,
    *,
    exercise: str,
    phase: Phase,
    reps: int,
    target_reps: int,
    fps: float,
    paused: bool,
    show_metrics: bool = True,
    highlight_joints: Iterable[str] = (),
) -> np.ndarray:
    output = frame.copy()
    height, width = output.shape[:2]
    highlight_ids = {
        JOINT_IDS[name]
        for name in highlight_joints
        if name in JOINT_IDS
    }
    if pose.detected:
        for start_id, end_id in POSE_CONNECTIONS:
            if start_id >= len(pose.landmarks) or end_id >= len(pose.landmarks):
                continue
            start = pose.landmarks[start_id]
            end = pose.landmarks[end_id]
            if min(start.visibility, end.visibility) < 0.4:
                continue
            highlighted = (
                start_id in highlight_ids or end_id in highlight_ids
            )
            cv2.line(
                output,
                (int(start.x * width), int(start.y * height)),
                (int(end.x * width), int(end.y * height)),
                (70, 88, 255) if highlighted else (44, 220, 150),
                4 if highlighted else 2,
                cv2.LINE_AA,
            )
        for landmark in pose.landmarks:
            if landmark.visibility < 0.4:
                continue
            center = (int(landmark.x * width), int(landmark.y * height))
            cv2.circle(
                output,
                center,
                3,
                (255, 210, 60),
                -1,
                cv2.LINE_AA,
            )
            if landmark.id in highlight_ids:
                cv2.circle(
                    output,
                    center,
                    8,
                    (70, 88, 255),
                    2,
                    cv2.LINE_AA,
                )

    if show_metrics:
        panel_width = min(280, max(220, width // 3))
        cv2.rectangle(output, (10, 10), (panel_width, 154), (18, 18, 18), -1)
        cv2.rectangle(output, (10, 10), (panel_width, 154), (70, 70, 70), 1)
        cv2.putText(
            output,
            f"{exercise.upper()}  {phase.value.upper()}",
            (24, 38),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (245, 245, 245),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            output,
            f"{reps}/{target_reps}",
            (24, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.8,
            (70, 230, 170),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            output,
            f"fps {fps:4.1f}   conf {pose.confidence:0.2f}",
            (24, 132),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (190, 190, 190),
            1,
            cv2.LINE_AA,
        )
    if paused:
        cv2.rectangle(output, (0, 0), (width, height), (20, 20, 20), -1)
        overlay = output.copy()
        cv2.putText(
            overlay,
            "PAUSED",
            (width // 2 - 125, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.8,
            (250, 250, 250),
            4,
            cv2.LINE_AA,
        )
        output = cv2.addWeighted(overlay, 0.88, output, 0.12, 0)
    return output
