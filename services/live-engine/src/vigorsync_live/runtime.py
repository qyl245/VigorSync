"""Threaded live session runtime and artifact persistence."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from .angle import AngleObservation, pushup_posture_valid
from .capture import FrameSource
from .config import Settings
from .core_reporter import CoreReporter
from .cue_policy import CuePolicy, SpeechCue
from .form_aggregator import FaultAggregator
from .form_rules import FormFaultDetector
from .issue_catalog import live_speech_texts
from .overlay import draw_overlay, mirror_pose_for_display
from .pose import PoseEstimator, PoseFrame
from .speech import (
    SilentSpeechBackend,
    SpeechBackend,
    SpeechQueue,
    SystemSpeechBackend,
)
from .state_machine import (
    FormAlert,
    Phase,
    RepCompleted,
    StateMachine,
)
from .storage import JsonlWriter, utc_now
from .video import AsyncVideoRecorder


logger = logging.getLogger("uvicorn.error")


class PoseProcessor(Protocol):
    def process(
        self,
        frame_bgr: np.ndarray,
        frame_index: int,
        t_ms: int,
    ) -> PoseFrame: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionRequest:
    session_id: str
    exercise: str
    view: str
    target_reps: int
    session_dir: Path
    source_kind: str = "camera"
    video_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    frame: np.ndarray
    frame_index: int
    t_ms: int


class LiveEngineError(RuntimeError):
    code = "live_engine_error"


class CameraBusyError(LiveEngineError):
    code = "camera_busy"


class InvalidTransitionError(LiveEngineError):
    code = "invalid_transition"


class SessionRuntime:
    """Own one camera source and one MediaPipe inference thread."""

    def __init__(
        self,
        request: SessionRequest,
        settings: Settings,
        *,
        source_factory: Callable[[], FrameSource] | None = None,
        estimator_factory: Callable[[], PoseProcessor] | None = None,
        speech_backend_factory: Callable[[], SpeechBackend] | None = None,
        recording_fps: float | None = None,
    ) -> None:
        self.request = request
        self.settings = settings
        self._source_factory = source_factory
        self._estimator_factory = estimator_factory or (
            lambda: PoseEstimator(
                request.exercise,
                request.view,
                model_complexity=settings.pose_model_complexity,
                min_visibility=settings.min_visibility,
            )
        )
        self._recording_fps = recording_fps or float(settings.camera_fps)

        self._status = "preparing"
        self._status_lock = threading.RLock()
        self._error: dict[str, str] | None = None
        self._stop_event = threading.Event()
        self._threads_done = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._processing_thread: threading.Thread | None = None
        self._frame_queue: queue.Queue[CapturedFrame | None] = queue.Queue(
            maxsize=1
        )

        self._session_started_monotonic: float | None = None
        self._started_monotonic: float | None = None
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._active_started_monotonic: float | None = None
        self._active_elapsed_s = 0.0
        self._capture_count = 0
        self._processed_count = 0
        self._dropped_count = 0
        self._throttle_wait_count = 0
        self._processed_at: deque[float] = deque(maxlen=30)
        self._pose_confidence_sum = 0.0
        self._pose_confidence_count = 0
        self._startup_timings: dict[str, float] = {}

        self._state_machine = StateMachine(
            request.exercise,
            view=request.view,
            min_confidence=settings.min_visibility,
        )
        self._form_detector = FormFaultDetector(
            request.exercise,
            request.view,
            min_visibility=settings.min_visibility,
        )
        self._fault_aggregator = FaultAggregator()
        self._highlight_until_ms: dict[str, int] = {}
        self._alerts: Counter[str] = Counter()
        self._cue_policy = CuePolicy(
            min_confidence=settings.tts_min_confidence,
            code_cooldown_ms=settings.tts_code_cooldown_ms,
            global_cooldown_ms=settings.tts_global_cooldown_ms,
        )
        speech_backend = (
            speech_backend_factory()
            if speech_backend_factory is not None
            else (
                SystemSpeechBackend(
                    project_root=settings.project_root,
                    voice=settings.tts_voice,
                    rate=settings.tts_rate,
                    volume=settings.tts_volume,
                )
                if settings.tts_enabled
                else SilentSpeechBackend()
            )
        )
        self._speech = SpeechQueue(
            speech_backend,
            max_size=settings.tts_queue_size,
            enabled=settings.tts_enabled,
        )
        self._speech_enabled_before_pause = settings.tts_enabled
        self._core_reporter: CoreReporter | None = None

        self._event_lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=10000)
        self._next_seq = 0
        self._events_writer: JsonlWriter | None = None
        self._landmark_writer: JsonlWriter | None = None

        self._jpeg_condition = threading.Condition()
        self._latest_jpeg: bytes | None = None
        self._jpeg_version = 0
        self._latest_pose = PoseFrame(
            detected=False,
            confidence=0.0,
            landmarks=(),
            angle=AngleObservation(None, 0.0, None, None),
        )
        self._latest_metrics: dict[str, Any] = {
            "exercise": request.exercise,
            "phase": Phase.UNKNOWN.value,
            "reps": 0,
            "confidence": 0.0,
            "fps": 0.0,
            "paused": False,
            "joint_angle": None,
            "elapsed_s": 0.0,
        }
        self._artifacts = self._artifact_paths()

    def _artifact_paths(self) -> dict[str, Path]:
        return {
            "video_path": self.request.session_dir / "input" / "source.mp4",
            "landmarks_path": self.request.session_dir
            / "input"
            / "landmarks.jsonl",
            "events_path": self.request.session_dir / "live" / "events.jsonl",
            "summary_path": self.request.session_dir / "live" / "summary.json",
        }

    @property
    def status(self) -> str:
        with self._status_lock:
            return self._status

    @property
    def error(self) -> dict[str, str] | None:
        with self._status_lock:
            return None if self._error is None else dict(self._error)

    @property
    def artifacts(self) -> dict[str, str]:
        return {
            name: str(path.resolve())
            for name, path in self._artifacts.items()
        }

    @property
    def latest_metrics(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._latest_metrics)

    @property
    def counts(self) -> dict[str, int]:
        with self._status_lock:
            return {
                "captured_frames": self._capture_count,
                "processed_frames": self._processed_count,
                "dropped_frames": self._dropped_count,
                "throttle_waits": self._throttle_wait_count,
            }

    @property
    def startup_timings(self) -> dict[str, float]:
        with self._status_lock:
            return dict(self._startup_timings)

    @property
    def speech_status(self) -> dict[str, Any]:
        return self._speech.status()

    def set_speech_enabled(self, enabled: bool) -> dict[str, Any]:
        status = self._speech.set_enabled(enabled)
        if self.status == "paused":
            self._speech_enabled_before_pause = enabled
        if not enabled:
            self._cue_policy.reset()
        return status

    def _record_startup_timing(self, name: str, milliseconds: float) -> None:
        with self._status_lock:
            self._startup_timings[name] = round(milliseconds, 3)

    def start(self) -> None:
        if self._capture_thread is not None:
            return
        self._speech.warm(live_speech_texts())
        self.request.session_dir.joinpath("input").mkdir(parents=True, exist_ok=True)
        self.request.session_dir.joinpath("live").mkdir(parents=True, exist_ok=True)
        self._events_writer = JsonlWriter(self._artifacts["events_path"])
        self._landmark_writer = JsonlWriter(
            self._artifacts["landmarks_path"]
        )
        self._session_started_monotonic = time.monotonic()
        if self.settings.core_url:
            self._core_reporter = CoreReporter(
                self.request.session_id,
                self.settings,
            )
        self._set_status("preparing")
        self._emit_state("preparing", "created")
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name=f"vigorsync-capture-{self.request.session_id}",
            daemon=True,
        )
        self._processing_thread = threading.Thread(
            target=self._processing_loop,
            name=f"vigorsync-pose-{self.request.session_id}",
            daemon=True,
        )
        self._capture_thread.start()
        self._processing_thread.start()
        self._record_startup_timing(
            "threads_started_ms",
            (time.monotonic() - self._session_started_monotonic) * 1000,
        )

    def pause(self) -> None:
        with self._status_lock:
            if self._status != "active":
                raise InvalidTransitionError("only an active session can be paused")
            now = time.monotonic()
            if self._active_started_monotonic is not None:
                self._active_elapsed_s += now - self._active_started_monotonic
            self._active_started_monotonic = None
            self._status = "paused"
            self._state_machine.set_paused(True)
            self._fault_aggregator.reset()
            self._cue_policy.reset()
            self._speech_enabled_before_pause = self._speech.status()["enabled"]
            self._speech.set_enabled(False)
            self._highlight_until_ms.clear()
            self._latest_metrics["paused"] = True
            self._latest_metrics["phase"] = Phase.UNKNOWN.value
        self._emit_state("paused", "active")
        if self._core_reporter is not None:
            self._core_reporter.submit(
                "paused",
                dict(self._latest_metrics),
                int(float(self._latest_metrics.get("elapsed_s", 0)) * 1000),
            )

    def resume(self) -> None:
        with self._status_lock:
            if self._status != "paused":
                raise InvalidTransitionError("only a paused session can be resumed")
            self._active_started_monotonic = time.monotonic()
            self._status = "active"
            self._state_machine.set_paused(False)
            self._speech.set_enabled(self._speech_enabled_before_pause)
            self._latest_metrics["paused"] = False
        self._emit_state("active", "paused")
        if self._core_reporter is not None:
            self._core_reporter.submit(
                "active",
                dict(self._latest_metrics),
                int(float(self._latest_metrics.get("elapsed_s", 0)) * 1000),
            )

    def stop(self) -> dict[str, Any]:
        with self._status_lock:
            if self._status == "finalized":
                return self._finalize_payload()
            was_failed = self._status == "failed"
            previous = self._status
            if not was_failed:
                if self._active_started_monotonic is not None:
                    self._active_elapsed_s += (
                        time.monotonic() - self._active_started_monotonic
                    )
                    self._active_started_monotonic = None
                self._latest_metrics["elapsed_s"] = round(
                    self._active_elapsed_s,
                    3,
                )
                self._status = "stopping"
        if not was_failed:
            self._emit_state("stopping", previous)
        self._speech.set_enabled(False)
        self._stop_event.set()
        self._join_threads()
        self._ended_at = utc_now()
        if was_failed:
            try:
                self._write_summary()
            except Exception:
                pass
        else:
            with self._status_lock:
                self._status = "finalized"
            try:
                self._write_summary()
            except Exception as exc:  # pragma: no cover - defensive artifact path
                self._fail("recording_failed", f"cannot write summary: {exc}")
        return self._finalize_payload()

    def close(self) -> None:
        if self._capture_thread is not None:
            if self.status not in {"finalized", "failed"}:
                self.stop()
            else:
                self._join_threads()

    def _source(self) -> FrameSource:
        if self._source_factory is not None:
            return self._source_factory()
        from .capture import OpenCvCameraSource, OpenCvVideoSource

        if self.request.source_kind == "video":
            if self.request.video_path is None:
                raise ValueError("video source requires video_path")
            return OpenCvVideoSource(self.request.video_path)
        return OpenCvCameraSource(self.settings)

    def _capture_loop(self) -> None:
        source = self._source()
        recorder = AsyncVideoRecorder(
            self._artifacts["video_path"],
            self._recording_fps,
        )
        video_frames_written = 0
        try:
            camera_open_started = time.monotonic()
            source.open()
            self._record_startup_timing(
                "camera_open_ms",
                (time.monotonic() - camera_open_started) * 1000,
            )
            first_frame = True
            while not self._stop_event.is_set():
                frame = source.read()
                if frame is None:
                    if self.request.source_kind == "video":
                        break
                    raise RuntimeError("camera returned an empty frame")
                with self._status_lock:
                    frame_index = self._capture_count
                    self._capture_count += 1
                captured_at = time.monotonic()
                if first_frame:
                    first_frame = False
                    self._record_startup_timing(
                        "first_frame_ms",
                        (
                            captured_at
                            - (self._session_started_monotonic or captured_at)
                        )
                        * 1000,
                    )
                if self._started_monotonic is None:
                    self._started_monotonic = captured_at
                    self._started_at = utc_now()
                elapsed = captured_at - self._started_monotonic
                target_frame = int(elapsed * self._recording_fps)
                slots_to_write = target_frame - video_frames_written + 1
                if slots_to_write > 0:
                    max_fill = max(1, int(self._recording_fps * 2))
                    recorder.submit(frame, min(slots_to_write, max_fill))
                    video_frames_written = target_frame + 1
                captured = CapturedFrame(
                    frame=frame,
                    frame_index=frame_index,
                    t_ms=max(0, int(elapsed * 1000)),
                )
                try:
                    self._frame_queue.put_nowait(captured)
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                        with self._status_lock:
                            self._dropped_count += 1
                    except queue.Empty:
                        pass
                    self._frame_queue.put_nowait(captured)
        except Exception as exc:
            self._fail("camera_open_failed", str(exc))
        finally:
            try:
                source.close()
            finally:
                try:
                    recorder.close()
                except Exception as exc:
                    self._fail("recording_failed", str(exc))
                self._stop_event.set()
                try:
                    self._frame_queue.put_nowait(None)
                except queue.Full:
                    pass

    def _processing_loop(self) -> None:
        estimator: PoseProcessor | None = None
        last_metric_at = 0.0
        last_preview_at = 0.0
        last_inference_started: float | None = None
        inference_interval = 1.0 / self.settings.inference_fps
        preview_interval = 1.0 / self.settings.preview_fps
        try:
            pose_init_started = time.monotonic()
            estimator = self._estimator_factory()
            self._record_startup_timing(
                "pose_init_ms",
                (time.monotonic() - pose_init_started) * 1000,
            )
            first_inference = True
            while not self._stop_event.is_set() or not self._frame_queue.empty():
                try:
                    item = self._frame_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is None:
                    continue
                now = time.monotonic()
                if (
                    last_inference_started is not None
                    and now - last_inference_started < inference_interval
                ):
                    with self._status_lock:
                        self._throttle_wait_count += 1
                    time.sleep(inference_interval - (now - last_inference_started))
                    now = time.monotonic()
                last_inference_started = now
                inference_started = time.monotonic()
                pose = estimator.process(
                    item.frame,
                    item.frame_index,
                    item.t_ms,
                )
                now = time.monotonic()
                if first_inference:
                    first_inference = False
                    self._record_startup_timing(
                        "first_inference_ms",
                        (now - inference_started) * 1000,
                    )
                self._processed_at.append(now)
                self._record_landmark(item, pose)
                self._consume_state_events(item, pose)
                fps = self._inference_fps()
                became_active = False
                with self._status_lock:
                    self._processed_count += 1
                    self._latest_pose = pose
                    self._latest_metrics = {
                        "exercise": self.request.exercise,
                        "phase": (
                            Phase.UNKNOWN.value
                            if self.status == "paused"
                            else self._state_machine.display_phase().value
                        ),
                        "reps": self._state_machine.reps,
                        "confidence": round(pose.confidence, 6),
                        "fps": round(fps, 3),
                        "paused": self.status == "paused",
                        "joint_angle": (
                            round(pose.angle.angle_deg, 3)
                            if pose.angle.angle_deg is not None
                            else None
                        ),
                        "elapsed_s": round(
                            self._active_elapsed_s
                            + (
                                now - self._active_started_monotonic
                                if self._active_started_monotonic is not None
                                else 0.0
                            ),
                            3,
                        ),
                    }
                    if self._status == "preparing":
                        self._status = "active"
                        self._active_started_monotonic = now
                        self._startup_timings["active_ms"] = round(
                            (
                                now
                                - (
                                    self._session_started_monotonic
                                    or now
                                )
                            )
                            * 1000,
                            3,
                        )
                        became_active = True
                if pose.detected:
                    self._pose_confidence_sum += pose.confidence
                    self._pose_confidence_count += 1
                if became_active:
                    logger.info(
                        "session %s active startup_ms=%s",
                        self.request.session_id,
                        json.dumps(
                            self.startup_timings,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                    self._emit_state("active", "preparing")
                if now - last_preview_at >= preview_interval:
                    last_preview_at = now
                    preview_frame = cv2.flip(item.frame, 1)
                    preview_pose = mirror_pose_for_display(pose)
                    overlay = draw_overlay(
                        preview_frame,
                        preview_pose,
                        exercise=self.request.exercise,
                        phase=self._state_machine.display_phase(),
                        reps=self._state_machine.reps,
                        target_reps=self.request.target_reps,
                        fps=fps,
                        paused=self.status == "paused",
                        show_metrics=False,
                        highlight_joints=self._active_highlights(item.t_ms),
                    )
                    self._publish_jpeg(overlay)
                if now - last_metric_at >= 1.0 / self.settings.metric_hz:
                    last_metric_at = now
                    self._emit(
                        "live.metrics",
                        dict(self._latest_metrics),
                        item.t_ms,
                        persist=False,
                    )
                    if self._core_reporter is not None:
                        self._core_reporter.submit(
                            self.status,
                            dict(self._latest_metrics),
                            item.t_ms,
                        )
        except Exception as exc:
            self._fail("pose_model_load_failed", str(exc))
        finally:
            if estimator is not None:
                estimator.close()
            if self._core_reporter is not None:
                self._core_reporter.close()
            self._speech.close()
            self._threads_done.set()

    def _record_landmark(
        self,
        captured: CapturedFrame,
        pose: PoseFrame,
    ) -> None:
        if not pose.detected:
            return
        height, width = captured.frame.shape[:2]
        payload = {
            "t_ms": captured.t_ms,
            "frame": captured.frame_index,
            "width": width,
            "height": height,
            "pose_confidence": round(pose.confidence, 6),
            "landmarks": [
                {
                    "id": point.id,
                    "x": round(point.x, 6),
                    "y": round(point.y, 6),
                    "z": round(point.z, 6),
                    "visibility": round(point.visibility, 6),
                }
                for point in pose.landmarks
            ],
        }
        self._landmark_writer.write(payload)

    def _consume_state_events(
        self,
        captured: CapturedFrame,
        pose: PoseFrame,
    ) -> None:
        with self._status_lock:
            paused = self._status != "active"
        if paused:
            return
        state_events = self._state_machine.update(
            captured.t_ms,
            pose.angle.angle_deg,
            pose.angle.confidence,
            posture_valid=(
                self.request.exercise != "pushup"
                or pushup_posture_valid(
                    pose.landmarks,
                    self.request.view,
                    captured.frame.shape[1],
                    captured.frame.shape[0],
                    self.settings.min_visibility,
                )
            ),
        )
        evaluation = self._form_detector.detect(
            pose,
            self._state_machine.display_phase(),
            captured.frame.shape[1],
            captured.frame.shape[0],
        )
        stable_faults = self._fault_aggregator.update(
            evaluation.candidates,
            evaluation.evaluated_codes,
            captured.t_ms,
        )
        events: list[RepCompleted | FormAlert] = [
            *state_events,
            *(
                FormAlert(
                    code=fault.code,
                    severity=fault.severity,
                    message=fault.message,
                    confidence=fault.confidence,
                    joints=fault.joints,
                )
                for fault in stable_faults
            ),
        ]
        for event in events:
            if isinstance(event, RepCompleted):
                self._emit(
                    "rep.completed",
                    {
                        "rep_id": event.rep_id,
                        "start_ms": event.start_ms,
                        "end_ms": event.end_ms,
                        "duration_ms": event.duration_ms,
                        "confidence": event.confidence,
                    },
                    captured.t_ms,
                )
                if self._speech.accepts_cues:
                    self._submit_speech_cue(
                        self._cue_policy.rep_milestone(
                            self._state_machine.reps,
                            self.request.target_reps,
                            captured.t_ms,
                        )
                    )
            elif isinstance(event, FormAlert):
                self._mark_highlights(event.joints, captured.t_ms)
                self._alerts[event.code] += 1
                self._emit(
                    "form.alert",
                    {
                        "code": event.code,
                        "severity": event.severity,
                        "message": event.message,
                        "confidence": event.confidence,
                        "joints": list(event.joints),
                    },
                    captured.t_ms,
                )
                if self._speech.accepts_cues:
                    self._submit_speech_cue(
                        self._cue_policy.form_alert(
                            event,
                            captured.t_ms,
                        )
                    )

    def _submit_speech_cue(self, cue: SpeechCue | None) -> None:
        if cue is not None:
            self._speech.submit(cue)

    def _mark_highlights(
        self,
        joints: tuple[str, ...],
        t_ms: int,
    ) -> None:
        expires_at = t_ms + 1500
        for joint in joints:
            self._highlight_until_ms[joint] = max(
                expires_at,
                self._highlight_until_ms.get(joint, expires_at),
            )

    def _active_highlights(self, t_ms: int) -> frozenset[str]:
        for joint, expires_at in list(self._highlight_until_ms.items()):
            if expires_at < t_ms:
                self._highlight_until_ms.pop(joint, None)
        return frozenset(
            self._highlight_until_ms
        ) | self._fault_aggregator.active_joints(t_ms)

    def _inference_fps(self) -> float:
        if len(self._processed_at) < 2:
            return 0.0
        elapsed = self._processed_at[-1] - self._processed_at[0]
        if elapsed <= 0:
            return 0.0
        return min(120.0, (len(self._processed_at) - 1) / elapsed)

    def _publish_jpeg(self, frame: np.ndarray) -> None:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality],
        )
        if not ok:
            return
        with self._jpeg_condition:
            self._latest_jpeg = encoded.tobytes()
            self._jpeg_version += 1
            self._jpeg_condition.notify_all()

    def wait_for_jpeg(
        self,
        previous_version: int,
        timeout: float = 1.0,
    ) -> tuple[int, bytes | None]:
        with self._jpeg_condition:
            if self._jpeg_version == previous_version:
                self._jpeg_condition.wait(timeout=timeout)
            return self._jpeg_version, self._latest_jpeg

    def _emit_state(self, status: str, previous: str) -> None:
        elapsed_ms = int(
            (
                time.monotonic()
                - (
                    self._started_monotonic
                    or self._session_started_monotonic
                    or time.monotonic()
                )
            )
            * 1000
        )
        self._emit(
            "session.state",
            {"status": status, "previous_status": previous},
            max(0, elapsed_ms),
        )

    def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        t_ms: int,
        *,
        persist: bool = True,
    ) -> None:
        with self._event_lock:
            self._next_seq += 1
            event = {
                "type": event_type,
                "version": "v1",
                "session_id": self.request.session_id,
                "t_ms": int(t_ms),
                "seq": self._next_seq,
                "created_at": utc_now(),
                "payload": payload,
            }
            self._events.append(event)
            writer = self._events_writer
            if persist and writer is not None:
                writer.write(event)

    def events_after(self, sequence: int) -> list[dict[str, Any]]:
        with self._event_lock:
            return [
                event
                for event in self._events
                if int(event["seq"]) > sequence
            ]

    def _set_status(self, status: str) -> None:
        with self._status_lock:
            self._status = status

    def _fail(self, code: str, message: str) -> None:
        with self._status_lock:
            if self._status == "failed":
                return
            previous = self._status
            self._status = "failed"
            self._error = {"code": code, "message": message[:512]}
        self._stop_event.set()
        try:
            self._emit_state("failed", previous)
        except Exception:
            pass

    def _join_threads(self) -> None:
        current = threading.current_thread()
        for thread in (self._capture_thread, self._processing_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=5.0)
        self._close_writers()

    def _close_writers(self) -> None:
        if self._landmark_writer is not None:
            self._landmark_writer.close()
        if self._events_writer is not None:
            self._events_writer.close()

    def _write_summary(self) -> None:
        if self._started_at is None:
            self._started_at = utc_now()
        ended_at = self._ended_at or utc_now()
        duration_s = max(
            0.0,
            (
                time.monotonic() - (self._started_monotonic or time.monotonic())
                if self.status != "failed"
                else 0.0
            ),
        )
        mean_confidence = (
            self._pose_confidence_sum / self._pose_confidence_count
            if self._pose_confidence_count
            else 0.0
        )
        summary = {
            "schema_version": "vigorsync-live-session-v1",
            "session_id": self.request.session_id,
            "exercise": self.request.exercise,
            "view": self.request.view,
            "started_at": self._started_at,
            "ended_at": ended_at,
            "reps": self._state_machine.reps,
            "duration_s": round(duration_s, 6),
            "mean_confidence": round(
                max(0.0, min(1.0, mean_confidence)),
                6,
            ),
            "alert_counts": dict(sorted(self._alerts.items())),
            "algorithm": {
                "pose_model": "mediapipe_pose_landmarker_lite",
                "inference_fps": round(self._inference_fps(), 6),
                "version": "0.1.0",
            },
        }
        self._artifacts["summary_path"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _finalize_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.request.session_id,
            "status": self.status,
            "artifacts": self.artifacts,
            "summary": self.latest_metrics,
            "error": self.error,
        }
