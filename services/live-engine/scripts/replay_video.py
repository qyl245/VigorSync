#!/usr/bin/env python3
"""Run MediaPipe, real-time state logic, and overlay rendering on a video."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import cv2

from vigorsync_live.config import Settings
from vigorsync_live.overlay import draw_overlay
from vigorsync_live.pose import PoseEstimator
from vigorsync_live.state_machine import FormAlert, RepCompleted, StateMachine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--exercise", choices=["squat", "pushup"], default="squat")
    parser.add_argument("--view", default="side_left")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        parser.error(f"cannot open video: {args.video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    output_dir = (
        args.output_dir
        or Path(__file__).resolve().parents[1] / "smoke-output"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video = output_dir / f"{args.video.stem}-overlay.mp4"
    writer = cv2.VideoWriter(
        str(output_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    settings = Settings.from_env()
    estimator = PoseEstimator(
        args.exercise,
        args.view,
        model_complexity=settings.pose_model_complexity,
        min_visibility=settings.min_visibility,
    )
    machine = StateMachine(
        args.exercise,
        view=args.view,
        min_confidence=settings.min_visibility,
    )
    events = []
    detected = 0
    processed = 0
    started = time.perf_counter()
    try:
        while args.max_frames is None or processed < args.max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            t_ms = int(processed * 1000.0 / max(fps, 1e-6))
            pose = estimator.process(frame, processed, t_ms)
            detected += int(pose.detected)
            machine_events = machine.update(
                t_ms,
                pose.angle.angle_deg,
                pose.angle.confidence,
            )
            for event in machine_events:
                if isinstance(event, RepCompleted):
                    events.append(
                        {
                            "type": "rep.completed",
                            **asdict(event),
                        }
                    )
                elif isinstance(event, FormAlert):
                    events.append(
                        {
                            "type": "form.alert",
                            **asdict(event),
                        }
                    )
            final = draw_overlay(
                frame,
                pose,
                exercise=args.exercise,
                phase=machine.display_phase(),
                reps=machine.reps,
                target_reps=0,
                fps=processed / max(time.perf_counter() - started, 1e-6),
                paused=False,
            )
            writer.write(final)
            processed += 1
    finally:
        writer.release()
        estimator.close()
        capture.release()

    elapsed = time.perf_counter() - started
    summary = {
        "video": str(args.video.resolve()),
        "processed_frames": processed,
        "pose_detected_frames": detected,
        "reps": machine.reps,
        "events": events,
        "processing_fps": round(processed / max(elapsed, 1e-6), 3),
        "output_video": str(output_video.resolve()),
    }
    (output_dir / f"{args.video.stem}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
