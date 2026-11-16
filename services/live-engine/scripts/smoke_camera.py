#!/usr/bin/env python3
"""Open one local camera frame and run one MediaPipe pose inference."""

from __future__ import annotations

import argparse
import time
from dataclasses import replace

from vigorsync_live.capture import OpenCvCameraSource
from vigorsync_live.config import Settings
from vigorsync_live.pose import PoseEstimator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", choices=["squat", "pushup"], default="squat")
    parser.add_argument("--view", default="side_left")
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--camera-index", type=int)
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.camera_index is not None:
        settings = replace(settings, camera_index=args.camera_index)
    source = OpenCvCameraSource(settings)
    source.open()
    estimator = PoseEstimator(
        args.exercise,
        args.view,
        model_complexity=settings.pose_model_complexity,
        min_visibility=settings.min_visibility,
    )
    detected = 0
    start = time.perf_counter()
    try:
        for frame_index in range(max(1, args.frames)):
            frame = source.read()
            if frame is None:
                raise RuntimeError("camera frame read failed")
            pose = estimator.process(
                frame,
                frame_index,
                int((time.perf_counter() - start) * 1000),
            )
            detected += int(pose.detected)
        elapsed = time.perf_counter() - start
        print(
            f"camera={settings.camera_index} frames={args.frames} "
            f"detected={detected} fps={args.frames / max(elapsed, 1e-6):.2f}"
        )
        return 0
    finally:
        estimator.close()
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
