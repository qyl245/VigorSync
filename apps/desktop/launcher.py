"""Small Windows launcher for the workspace-based VigorSync desktop host."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


LOG_PATH = Path(tempfile.gettempdir()) / "VigorSyncLauncher.log"


def log(message: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat()} {message}\n")
    except Exception:
        pass


def show_error(message: str) -> None:
    log("ERROR " + message.replace("\n", " | "))
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "VigorSync", 0x10)


def find_project_root() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent,
        Path(sys.executable).resolve().parent,
    ]
    for start in candidates:
        for directory in (start, *start.parents):
            pythonw = directory / ".venv" / "Scripts" / "pythonw.exe"
            app = directory / "apps" / "desktop" / "app.py"
            if pythonw.is_file() and app.is_file():
                log(f"Found project root: {directory}")
                return directory
    log("Project root search failed")
    return None


def main() -> int:
    root = find_project_root()
    if root is None:
        show_error(
            "找不到 VigorSync 工作区。\n"
            "请将启动器放在 VigorSync 目录或其 dist 子目录中。"
        )
        return 1

    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    app = root / "apps" / "desktop" / "app.py"
    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    try:
        subprocess.Popen(
            [str(pythonw), str(app)],
            cwd=str(root),
            creationflags=creation_flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"Started desktop host: {pythonw} {app}")
        return 0
    except Exception as exc:
        show_error(f"VigorSync 启动失败：\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
