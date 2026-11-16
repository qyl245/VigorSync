"""Thread-safe JSONL and session output helpers."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open(
            "w",
            encoding="utf-8",
            buffering=64 * 1024,
        )
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

    def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if not self._handle.closed:
                self._handle.write(line + "\n")
                now = time.monotonic()
                if now - self._last_flush >= 1.0:
                    self._handle.flush()
                    self._last_flush = now

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.flush()
                self._handle.close()
