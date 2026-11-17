"""Best-effort background reporting from Live Engine to Core API."""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from typing import Any

from .config import Settings


class CoreReporter:
    def __init__(self, session_id: str, settings: Settings) -> None:
        self.session_id = session_id
        self.core_url = settings.core_url
        self.internal_token = settings.core_internal_token
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=2)
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=f"vigorsync-core-reporter-{session_id}",
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        status: str,
        payload: dict[str, Any],
        t_ms: int,
    ) -> None:
        if self._closed or not self.core_url:
            return
        item = {
            "status": status,
            "t_ms": max(0, int(t_ms)),
            "payload": payload,
        }
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except (queue.Empty, queue.Full):
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(None, timeout=1.0)
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            request = urllib.request.Request(
                (
                    f"{self.core_url}/api/v1/internal/sessions/"
                    f"{self.session_id}/state"
                ),
                data=json.dumps(item, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    **(
                        {"X-Internal-Token": self.internal_token}
                        if self.internal_token
                        else {}
                    ),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=1.0) as response:
                    response.read()
            except (OSError, urllib.error.HTTPError):
                pass
