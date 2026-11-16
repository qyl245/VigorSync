"""Camera and fixed-video frame sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .config import Settings


class FrameSourceError(RuntimeError):
    """Raised when a camera or video source cannot be opened."""


class FrameSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> np.ndarray | None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class OpenCvCameraSource:
    settings: Settings
    _capture: cv2.VideoCapture | None = field(init=False, default=None)

    def open(self) -> None:
        backend_name = self.settings.camera_backend
        if backend_name == "dshow" and hasattr(cv2, "CAP_DSHOW"):
            capture = cv2.VideoCapture(
                self.settings.camera_index,
                cv2.CAP_DSHOW,
            )
        elif backend_name == "msmf" and hasattr(cv2, "CAP_MSMF"):
            capture = cv2.VideoCapture(
                self.settings.camera_index,
                cv2.CAP_MSMF,
            )
        else:
            capture = cv2.VideoCapture(self.settings.camera_index)
        if not capture.isOpened():
            capture.release()
            raise FrameSourceError(
                f"cannot open camera index {self.settings.camera_index}"
            )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.camera_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.camera_height)
        capture.set(cv2.CAP_PROP_FPS, self.settings.camera_fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._capture = capture

    def read(self) -> np.ndarray | None:
        if self._capture is None:
            raise FrameSourceError("camera is not open")
        ok, frame = self._capture.read()
        return frame if ok else None

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


@dataclass(slots=True)
class OpenCvVideoSource:
    path: Path
    realtime: bool = False
    _capture: cv2.VideoCapture | None = field(init=False, default=None)
    _delay_s: float = field(init=False, default=0.0)

    def open(self) -> None:
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            capture.release()
            raise FrameSourceError(f"cannot open video: {self.path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        self._delay_s = 1.0 / fps if self.realtime and fps > 0 else 0.0
        self._capture = capture

    def read(self) -> np.ndarray | None:
        if self._capture is None:
            raise FrameSourceError("video is not open")
        ok, frame = self._capture.read()
        if not ok:
            return None
        if self._delay_s > 0:
            import time

            time.sleep(self._delay_s)
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
