"""Asynchronous MP4 recorder for the live capture thread."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import cv2
import numpy as np


class VideoRecorderError(RuntimeError):
    pass


class AsyncVideoRecorder:
    """Write timestamp-preserving frame slots without blocking capture."""

    def __init__(
        self,
        path: Path,
        fps: float,
        max_queue_size: int = 8,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fps = fps
        self._queue: queue.Queue[tuple[np.ndarray, int] | None] = queue.Queue(
            maxsize=max_queue_size
        )
        self._error: Exception | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=f"vigorsync-video-{path.parent.parent.name}",
            daemon=True,
        )
        self._thread.start()

    def submit(self, frame: np.ndarray, copies: int) -> None:
        if self._closed:
            return
        if self._error is not None:
            raise VideoRecorderError(str(self._error)) from self._error
        if copies <= 0:
            return
        item = (frame, copies)
        try:
            self._queue.put_nowait(item)
            return
        except queue.Full:
            pass

        # Coalesce pending output into the newest frame rather than blocking
        # capture. This preserves wall-clock duration at reduced visual detail.
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                queued = None
            if queued is not None:
                copies += queued[1]
            try:
                self._queue.put_nowait((frame, copies))
                return
            except queue.Full:
                continue

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(None, timeout=5.0)
        except queue.Full:
            pass
        self._thread.join(timeout=5.0)
        if self._error is not None:
            raise VideoRecorderError(str(self._error)) from self._error

    def _run(self) -> None:
        writer: cv2.VideoWriter | None = None
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                frame, copies = item
                if writer is None:
                    height, width = frame.shape[:2]
                    writer = cv2.VideoWriter(
                        str(self.path),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        self.fps,
                        (width, height),
                    )
                    if not writer.isOpened():
                        raise VideoRecorderError(
                            f"cannot open video writer: {self.path}"
                        )
                for _ in range(copies):
                    writer.write(frame)
        except Exception as exc:
            self._error = exc
        finally:
            if writer is not None:
                writer.release()
