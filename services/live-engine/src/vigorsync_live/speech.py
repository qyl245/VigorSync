"""Bounded asynchronous speech output with a Windows SAPI wave backend."""

from __future__ import annotations

import base64
import hashlib
import heapq
import json
import logging
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .cue_policy import SpeechCue


logger = logging.getLogger("uvicorn.error")


class SpeechBackend(Protocol):
    name: str

    def speak(self, text: str) -> None: ...

    def warm(self, texts: tuple[str, ...]) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class SilentSpeechBackend:
    name = "silent"

    def speak(self, text: str) -> None:
        del text

    def warm(self, texts: tuple[str, ...]) -> None:
        del texts

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


class SystemSpeechBackend:
    """Synthesize SAPI audio to WAV and play it through winsound."""

    name = "windows-system-speech-wav"

    def __init__(
        self,
        *,
        project_root: Path,
        voice: str | None = None,
        rate: int = 170,
        volume: float = 1.0,
    ) -> None:
        self.cache_dir = (
            project_root / "webdata" / "runtime" / "tts-cache"
        )
        self.script_path = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "system_speech_to_wav.ps1"
        )
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self._lock = threading.RLock()

    def _cache_path(self, text: str) -> Path:
        identity = json.dumps(
            {
                "text": text,
                "voice": self.voice,
                "rate": self.rate,
                "volume": self.volume,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.wav"

    def _synthesize(self, texts: tuple[str, ...]) -> None:
        missing = [
            text
            for text in texts
            if not (
                self._cache_path(text).is_file()
                and self._cache_path(text).stat().st_size > 44
            )
        ]
        if not missing:
            return
        if not self.script_path.is_file():
            raise RuntimeError(f"speech helper missing: {self.script_path}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        items = [
            {"text": text, "path": str(self._cache_path(text))}
            for text in missing
        ]
        payload = base64.b64encode(
            json.dumps(items, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        rate = max(-10, min(10, round((self.rate - 200) / 20)))
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-ItemsBase64",
            payload,
            "-Rate",
            str(rate),
            "-Volume",
            str(self.volume),
        ]
        if self.voice:
            command.extend(["-Voice", self.voice])
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                stderr or "System.Speech WAV synthesis failed"
            )

    def warm(self, texts: tuple[str, ...]) -> None:
        with self._lock:
            self._synthesize(texts)

    def speak(self, text: str) -> None:
        with self._lock:
            self._synthesize((text,))
        import winsound

        winsound.PlaySound(
            str(self._cache_path(text)),
            winsound.SND_FILENAME | winsound.SND_NODEFAULT,
        )

    def stop(self) -> None:
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except (AttributeError, ImportError, RuntimeError):
            pass

    def close(self) -> None:
        self.stop()


@dataclass(order=True, slots=True)
class _QueuedCue:
    priority: int
    sequence: int
    cue: SpeechCue


class SpeechQueue:
    """Bounded priority queue whose worker never blocks the Pose thread."""

    def __init__(
        self,
        backend: SpeechBackend,
        *,
        max_size: int = 4,
        enabled: bool = True,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._backend = backend
        self._max_size = max_size
        self._enabled = enabled
        self._closed = False
        self._failed = False
        self._condition = threading.Condition()
        self._items: list[_QueuedCue] = []
        self._warm_texts: list[str] = []
        self._sequence = 0
        self._worker: threading.Thread | None = None
        self._state = "idle" if enabled else "muted"
        self._current: str | None = None
        self._last_spoken: str | None = None
        self._last_error: str | None = None
        self._queued_total = 0
        self._spoken_total = 0
        self._dropped_total = 0

    def submit(self, cue: SpeechCue) -> bool:
        with self._condition:
            if self._closed or self._failed:
                self._dropped_total += 1
                return False
            if not self._enabled:
                self._dropped_total += 1
                self._state = "muted"
                return False
            self._ensure_worker_locked()
            self._sequence += 1
            item = _QueuedCue(cue.priority, self._sequence, cue)
            if len(self._items) >= self._max_size:
                worst_index = max(
                    range(len(self._items)),
                    key=lambda index: (
                        self._items[index].priority,
                        -self._items[index].sequence,
                    ),
                )
                worst = self._items[worst_index]
                if item.priority >= worst.priority:
                    self._dropped_total += 1
                    return False
                self._items.pop(worst_index)
                self._dropped_total += 1
                heapq.heapify(self._items)
            heapq.heappush(self._items, item)
            self._queued_total += 1
            if self._state != "speaking":
                self._state = "queued"
            self._condition.notify()
            logger.info(
                "speech queued kind=%s code=%s priority=%d text=%s",
                cue.kind,
                cue.code,
                cue.priority,
                cue.text,
            )
            return True

    @property
    def accepts_cues(self) -> bool:
        with self._condition:
            return (
                self._enabled
                and not self._failed
                and not self._closed
                and getattr(self._backend, "name", "") != "silent"
            )

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run,
            name="vigorsync-speech",
            daemon=True,
        )
        self._worker.start()

    def warm(self, texts: tuple[str, ...]) -> None:
        with self._condition:
            if (
                self._closed
                or self._failed
                or not self._enabled
                or getattr(self._backend, "name", "") == "silent"
            ):
                return
            self._warm_texts.extend(
                text for text in texts if text not in self._warm_texts
            )
            if self._warm_texts:
                self._ensure_worker_locked()
                self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while (
                    not self._items
                    and not self._warm_texts
                    and not self._closed
                ):
                    self._condition.wait()
                if self._closed:
                    return
                if self._warm_texts:
                    warm_batch = tuple(self._warm_texts)
                    self._warm_texts.clear()
                    cue = None
                    self._state = "warming"
                else:
                    warm_batch = ()
                    item = heapq.heappop(self._items)
                    cue = item.cue
                    self._current = cue.text
                    self._state = "speaking"
            if cue is None:
                try:
                    self._backend.warm(warm_batch)
                except Exception as exc:
                    with self._condition:
                        self._last_error = str(exc)
                        self._state = (
                            "queued" if self._items else "idle"
                        )
                        self._condition.notify_all()
                    logger.warning(
                        "speech prewarm failed error=%s",
                        exc,
                    )
                else:
                    with self._condition:
                        self._state = (
                            "muted"
                            if not self._enabled
                            else "queued" if self._items else "idle"
                        )
                        self._condition.notify_all()
                continue
            try:
                self._backend.speak(cue.text)
            except Exception as exc:
                with self._condition:
                    self._failed = True
                    self._state = "unavailable"
                    self._last_error = str(exc)
                    self._dropped_total += len(self._items)
                    self._items.clear()
                    self._current = None
                    self._condition.notify_all()
                logger.warning(
                    "speech playback failed text=%s error=%s",
                    cue.text,
                    exc,
                )
                try:
                    self._backend.close()
                except Exception:
                    pass
                return
            with self._condition:
                self._spoken_total += 1
                self._last_spoken = cue.text
                self._current = None
                self._state = (
                    "muted"
                    if not self._enabled
                    else "queued" if self._items else "idle"
                )
                self._condition.notify_all()
            logger.info(
                "speech spoken kind=%s code=%s text=%s",
                cue.kind,
                cue.code,
                cue.text,
            )

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._condition:
            self._enabled = enabled
            if not enabled:
                self._dropped_total += len(self._items)
                self._items.clear()
                self._state = "muted"
            elif self._failed:
                self._state = "unavailable"
            else:
                self._state = "idle"
                self._ensure_worker_locked()
            self._condition.notify_all()
        if not enabled:
            self._safe_stop()
        return self.status()

    def clear_and_stop(self) -> None:
        with self._condition:
            self._dropped_total += len(self._items)
            self._items.clear()
            if not self._closed:
                self._state = "idle" if self._enabled else "muted"
            self._condition.notify_all()
        self._safe_stop()

    def status(self) -> dict[str, Any]:
        with self._condition:
            return {
                "backend": getattr(self._backend, "name", "unknown"),
                "enabled": self._enabled,
                "available": (
                    not self._failed
                    and not self._closed
                    and getattr(self._backend, "name", "") != "silent"
                ),
                "state": self._state,
                "queued": len(self._items),
                "queued_total": self._queued_total,
                "spoken_total": self._spoken_total,
                "dropped_total": self._dropped_total,
                "current": self._current,
                "last_spoken": self._last_spoken,
                "last_error": self._last_error,
            }

    def _safe_stop(self) -> None:
        try:
            self._backend.stop()
        except Exception:
            pass

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._dropped_total += len(self._items)
            self._items.clear()
            self._state = "closed"
            worker = self._worker
            self._condition.notify_all()
        self._safe_stop()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        try:
            self._backend.close()
        except Exception:
            pass
