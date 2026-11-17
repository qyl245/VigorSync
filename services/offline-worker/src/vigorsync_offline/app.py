"""Internal adapter around the vendored VLS RepChain runtime."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field


SERVICE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SERVICE_ROOT.parents[1]
VLS_ROOT = SERVICE_ROOT / "vendor" / "vls"
VLS_BACKEND = VLS_ROOT / "web" / "backend"
VLS_REPCHAIN = VLS_BACKEND / "app" / "repchain"

os.environ.setdefault(
    "VLS_WEBDATA_ROOT",
    str(PROJECT_ROOT / "webdata" / "offline"),
)
os.environ.setdefault("VLS_PYTHON_EXE", sys.executable)
if str(VLS_BACKEND) not in sys.path:
    sys.path.insert(0, str(VLS_BACKEND))
if str(VLS_REPCHAIN) not in sys.path:
    sys.path.insert(0, str(VLS_REPCHAIN))

# The vendored VLS app resolves configs/models relative to its package root.
from app.core import config as vls_config  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.jobs import JobRecord  # noqa: E402
from app.storage import db as vls_db  # noqa: E402
from app.workers.manager import manager as vls_manager  # noqa: E402


_SESSION_ID = re.compile(r"^s_[A-Za-z0-9_-]{6,64}$")
_JOB_ID = re.compile(r"^job_[A-Za-z0-9_-]{6,64}$")
_OFFLINE_TOKEN = os.getenv(
    "VIGORYSYNC_OFFLINE_INTERNAL_TOKEN",
    "vigorsync-offline-dev",
)
_SESSION_ROOT = Path(
    os.getenv(
        "VIGORYSYNC_SESSION_ROOT",
        str(PROJECT_ROOT / "webdata" / "sessions"),
    )
).resolve()


class EnqueueJobRequest(BaseModel):
    job_id: str
    session_id: str
    source_path: str
    exercise: Literal["squat", "pushup"]
    view: str
    rotation: str = "none"
    trim_start_frame: int = Field(default=0, ge=0)
    trim_end_frame: int = Field(default=0, ge=0)


def _require_internal(
    token: Annotated[
        str | None,
        Header(alias="X-Internal-Token"),
    ] = None,
) -> None:
    if not token or not secrets.compare_digest(token, _OFFLINE_TOKEN):
        raise HTTPException(status_code=401, detail="invalid_internal_token")


def _safe_source(value: str) -> Path:
    source = Path(value).expanduser().resolve()
    try:
        source.relative_to(_SESSION_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="source_outside_session_root") from exc
    if not source.is_file():
        raise HTTPException(status_code=400, detail="source_missing")
    if source.suffix.lower() not in vls_config.ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_video_type")
    return source


@app.post(
    "/internal/v1/jobs",
    status_code=202,
    response_model=JobRecord,
    dependencies=[Depends(_require_internal)],
)
def enqueue_job(payload: EnqueueJobRequest) -> JobRecord:
    if not _JOB_ID.fullmatch(payload.job_id):
        raise HTTPException(status_code=400, detail="invalid_job_id")
    if not _SESSION_ID.fullmatch(payload.session_id):
        raise HTTPException(status_code=400, detail="invalid_session_id")
    existing = vls_db.get_job(payload.job_id)
    if existing is not None:
        return existing

    source = _safe_source(payload.source_path)
    job_dir = vls_config.JOBS_DIR / payload.job_id
    input_dir = job_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    target = input_dir / f"source{source.suffix.lower()}"
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)

    request = {
        "source_kind": "upload",
        "sample_id": None,
        "suffix": source.suffix.lower(),
        "exercise": payload.exercise,
        "view": payload.view,
        "rotation": payload.rotation,
        "filename": source.name,
        "bytes": target.stat().st_size,
        "session_id": payload.session_id,
        "trim": {
            "start_frame": payload.trim_start_frame,
            "end_frame": payload.trim_end_frame,
        },
    }
    record = JobRecord(
        job_id=payload.job_id,
        source_kind="upload",
        sample_id=None,
        filename=source.name,
        exercise=payload.exercise,
        view=payload.view,
        upload_bytes=request["bytes"],
        status="queued",
        stage="queued",
        progress=0.0,
        created_at=vls_db.utcnow(),
        pipeline_version="vigorsync-repchain-v3",
    )
    (job_dir / "job.json").write_text(
        __import__("json").dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    vls_db.insert_job(record, request)
    vls_manager.enqueue(payload.job_id)
    return record


def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("VIGORYSYNC_OFFLINE_PORT", "8030")),
        log_level="info",
    )
