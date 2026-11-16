"""Lightweight Windows desktop host for the VigorSync mirror."""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


IS_FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_ROOT = (
    Path(getattr(sys, "_MEIPASS"))
    if IS_FROZEN
    else Path(__file__).resolve().parents[2]
)
DATA_ROOT = (
    Path(os.getenv("LOCALAPPDATA", Path.home())) / "VigorSync"
    if IS_FROZEN
    else SOURCE_ROOT
)
WEB_ROOT = SOURCE_ROOT / "apps" / "mirror" / "web"
RUNTIME_ROOT = DATA_ROOT / "webdata" / "runtime"
SESSION_ROOT = DATA_ROOT / "webdata" / "sessions"
LIVE_SOURCE = SOURCE_ROOT / "services" / "live-engine" / "src"
APP_TITLE = "VigorSync · 动作遥测"
LIVE_URL = "http://127.0.0.1:8020"
MIRROR_URL = "http://127.0.0.1:8080"

os.environ.setdefault("VIGORSYNC_PROJECT_ROOT", str(DATA_ROOT))
os.environ.setdefault("VIGORSYNC_SESSION_ROOT", str(SESSION_ROOT))
os.environ.setdefault("VIGORSYNC_LIVE_HOST", "127.0.0.1")
os.environ.setdefault("VIGORSYNC_LIVE_PORT", "8020")
if str(LIVE_SOURCE) not in sys.path:
    sys.path.insert(0, str(LIVE_SOURCE))


class QuietMirrorHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        logging.debug("mirror: " + format, *args)


def http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except Exception:
        return False


def read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20.0) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until(predicate: Any, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


class DesktopHost:
    def __init__(self) -> None:
        self.live_server: Any = None
        self.live_thread: threading.Thread | None = None
        self.mirror_server: ThreadingHTTPServer | None = None
        self.mirror_thread: threading.Thread | None = None
        self.manages_live = False
        self.manages_mirror = False
        self.allow_close = False

    def start(self) -> None:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        SESSION_ROOT.mkdir(parents=True, exist_ok=True)
        self._start_live()
        self._start_mirror()

    def _start_live(self) -> None:
        health_url = f"{LIVE_URL}/api/v1/health"
        if http_ok(health_url):
            logging.info("Attached to an existing Live Engine")
            return
        try:
            import uvicorn

            from vigorsync_live.app import app as live_app
        except Exception as exc:
            raise RuntimeError(f"cannot load Live Engine: {exc}") from exc

        config = uvicorn.Config(
            live_app,
            host="127.0.0.1",
            port=8020,
            log_level="warning",
            access_log=False,
        )
        self.live_server = uvicorn.Server(config)
        self.live_thread = threading.Thread(
            target=self.live_server.run,
            name="vigorsync-desktop-live",
            daemon=True,
        )
        self.live_thread.start()
        self.manages_live = True
        if not wait_until(lambda: http_ok(health_url)):
            raise RuntimeError("Live Engine failed to start; see desktop.log")
        logging.info("Live Engine started")

    def _start_mirror(self) -> None:
        if http_ok(MIRROR_URL):
            logging.info("Attached to an existing mirror server")
            return
        handler = partial(QuietMirrorHandler, directory=str(WEB_ROOT))
        self.mirror_server = ThreadingHTTPServer(("127.0.0.1", 8080), handler)
        self.mirror_thread = threading.Thread(
            target=self.mirror_server.serve_forever,
            name="vigorsync-desktop-mirror",
            daemon=True,
        )
        self.mirror_thread.start()
        self.manages_mirror = True
        if not wait_until(lambda: http_ok(MIRROR_URL)):
            raise RuntimeError("Mirror server failed to start")
        logging.info("Mirror server started")

    def active_sessions(self) -> list[str]:
        try:
            payload = read_json(f"{LIVE_URL}/api/v1/health")
            return list(payload.get("active_sessions") or [])
        except Exception:
            return []

    def stop_active_sessions(self) -> None:
        for session_id in self.active_sessions():
            try:
                post_json(f"{LIVE_URL}/dev/v1/sessions/{session_id}/stop")
                logging.info("Stopped session %s before exit", session_id)
            except Exception:
                logging.exception("Failed to stop session %s", session_id)

    def confirm_close(self, window: Any) -> bool:
        active = self.active_sessions()
        if not active:
            return True
        confirmed = window.create_confirmation_dialog(
            "训练正在进行",
            "结束当前训练并退出 VigorSync？\n视频、关键点和摘要会在退出前保存。",
        )
        if not confirmed:
            return False
        self.stop_active_sessions()
        return True

    def on_closing(self, window: Any) -> bool:
        if self.allow_close:
            return True
        if not self.confirm_close(window):
            return False
        self.allow_close = True
        return True

    def shutdown(self) -> None:
        self.stop_active_sessions()
        if self.manages_live and self.live_server is not None:
            self.live_server.should_exit = True
        if self.live_thread is not None:
            self.live_thread.join(timeout=8.0)
        if self.mirror_server is not None:
            self.mirror_server.shutdown()
            self.mirror_server.server_close()
        if self.mirror_thread is not None:
            self.mirror_thread.join(timeout=3.0)
        logging.info("Desktop host stopped")


def configure_logging() -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    file_handler = RotatingFileHandler(
        RUNTIME_ROOT / "desktop.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler])


def acquire_single_instance() -> Any:
    if os.name != "nt":
        return object()
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\VigorSyncDesktop")
    if not handle:
        raise RuntimeError("cannot create VigorSync single-instance mutex")
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        ctypes.windll.user32.MessageBoxW(
            None,
            "VigorSync 已在运行。",
            "VigorSync",
            0x00000040,
        )
        return None
    return handle


def run_headless(seconds: float) -> int:
    host = DesktopHost()
    try:
        host.start()
        print(f"VigorSync desktop services ready at {MIRROR_URL}")
        time.sleep(max(0.0, seconds))
        return 0
    finally:
        host.shutdown()


def run_window() -> int:
    import webview

    host = DesktopHost()
    try:
        host.start()
        window = webview.create_window(
            APP_TITLE,
            MIRROR_URL,
            width=1600,
            height=960,
            min_size=(960, 600),
            maximized=True,
            background_color="#090b0a",
            confirm_close=False,
            text_select=False,
        )
        window.events.closing += host.on_closing
        webview.start(
            private_mode=False,
            storage_path=str(RUNTIME_ROOT / "webview"),
        )
        return 0
    finally:
        host.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    configure_logging()
    instance = acquire_single_instance()
    if instance is None:
        return 0
    try:
        if args.headless:
            return run_headless(args.seconds)
        return run_window()
    except Exception:
        logging.exception("Desktop host failed")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(
                None,
                "VigorSync 启动失败，请查看 webdata/runtime/desktop.log。",
                "VigorSync",
                0x00000010,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
