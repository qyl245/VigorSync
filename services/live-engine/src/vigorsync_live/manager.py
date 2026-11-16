"""Single-camera session manager."""

from __future__ import annotations

import re
import secrets
import threading
from pathlib import Path
from typing import Any

from .config import Settings
from .runtime import (
    CameraBusyError,
    InvalidTransitionError,
    SessionRequest,
    SessionRuntime,
)


SESSION_ID_PATTERN = re.compile(r"^s_[A-Za-z0-9_-]{6,64}$")


class SessionNotFoundError(KeyError):
    pass


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_directories()
        self.settings = settings
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionRuntime] = {}

    def new_session_id(self) -> str:
        return f"s_{secrets.token_hex(12)}"

    def _session_dir(self, session_id: str, requested: str | None) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("invalid session_id")
        if requested:
            directory = Path(requested).expanduser().resolve()
            try:
                directory.relative_to(self.settings.session_root)
            except ValueError as exc:
                raise ValueError(
                    "session_dir must be inside the configured session root"
                ) from exc
            return directory
        return (self.settings.session_root / session_id).resolve()

    def start(
        self,
        *,
        session_id: str,
        exercise: str,
        view: str,
        target_reps: int,
        session_dir: str | None = None,
        source_kind: str = "camera",
        video_path: str | None = None,
    ) -> SessionRuntime:
        if exercise not in {"squat", "pushup"}:
            raise ValueError("exercise must be squat or pushup")
        if not 1 <= target_reps <= 500:
            raise ValueError("target_reps must be in [1, 500]")
        if source_kind not in {"camera", "video"}:
            raise ValueError("source_kind must be camera or video")
        directory = self._session_dir(session_id, session_dir)
        video = None
        if source_kind == "video":
            if not video_path:
                raise ValueError("video_path is required for a video source")
            video = Path(video_path).expanduser().resolve()
            if not video.is_file():
                raise ValueError(f"video does not exist: {video}")

        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            for runtime in self._sessions.values():
                if runtime.status not in {"finalized", "failed"}:
                    raise CameraBusyError(
                        "another Live Engine session is already active"
                    )
            request = SessionRequest(
                session_id=session_id,
                exercise=exercise,
                view=view,
                target_reps=target_reps,
                session_dir=directory,
                source_kind=source_kind,
                video_path=video,
            )
            runtime = SessionRuntime(request, self.settings)
            self._sessions[session_id] = runtime
            runtime.start()
            return runtime

    def get(self, session_id: str) -> SessionRuntime:
        with self._lock:
            runtime = self._sessions.get(session_id)
        if runtime is None:
            raise SessionNotFoundError(session_id)
        return runtime

    def pause(self, session_id: str) -> SessionRuntime:
        runtime = self.get(session_id)
        runtime.pause()
        return runtime

    def resume(self, session_id: str) -> SessionRuntime:
        runtime = self.get(session_id)
        runtime.resume()
        return runtime

    def stop(self, session_id: str) -> dict[str, Any]:
        runtime = self.get(session_id)
        return runtime.stop()

    def close_all(self) -> None:
        with self._lock:
            runtimes = list(self._sessions.values())
        for runtime in runtimes:
            try:
                runtime.close()
            except Exception:
                pass

    def health(self) -> dict[str, Any]:
        with self._lock:
            active = [
                runtime.request.session_id
                for runtime in self._sessions.values()
                if runtime.status not in {"finalized", "failed"}
            ]
        return {
            "status": "ok",
            "version": "0.1.0",
            "active_sessions": active,
        }
