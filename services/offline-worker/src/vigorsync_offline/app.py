"""Internal adapter around the vendored VLS RepChain runtime."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sys
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field


SERVICE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SERVICE_ROOT.parents[1]
RESOURCE_ROOT = Path(
    os.getenv("VIGORYSYNC_RESOURCE_ROOT", str(PROJECT_ROOT))
).resolve()
VLS_ROOT = Path(
    os.getenv("VIGORYSYNC_VLS_ROOT", str(SERVICE_ROOT / "vendor" / "vls"))
).resolve()
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

from .realtime_bridge import enrich_job_realtime_review  # noqa: E402


logger = logging.getLogger("uvicorn.error")
_SESSION_ID = re.compile(r"^s_[A-Za-z0-9_-]{6,64}$")
_JOB_ID = re.compile(r"^job_[A-Za-z0-9_-]{6,64}$")
_ARTIFACT_ROUTE = re.compile(
    r"^/api/v1/jobs/(job_[A-Za-z0-9_-]{6,64})/"
    r"(?:result|feedback-report)$"
)
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
    realtime_events_path: str | None = None


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


def _safe_realtime_events(
    value: str | None,
    session_id: str,
) -> Path | None:
    if not value:
        return None
    source = Path(value).expanduser().resolve()
    session_root = (_SESSION_ROOT / session_id).resolve()
    try:
        source.relative_to(session_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="realtime_events_outside_session",
        ) from exc
    if not source.is_file():
        raise HTTPException(status_code=400, detail="realtime_events_missing")
    if source.suffix.lower() != ".jsonl":
        raise HTTPException(
            status_code=400,
            detail="invalid_realtime_events_type",
        )
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
    realtime_events = _safe_realtime_events(
        payload.realtime_events_path,
        payload.session_id,
    )
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
    if realtime_events is not None:
        shutil.copy2(
            realtime_events,
            input_dir / "realtime-events.jsonl",
        )

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
        "realtime_events_path": (
            "input/realtime-events.jsonl"
            if realtime_events is not None
            else None
        ),
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


@app.middleware("http")
async def attach_realtime_review(request: Request, call_next):
    match = _ARTIFACT_ROUTE.fullmatch(request.url.path)
    if match:
        try:
            enrich_job_realtime_review(
                vls_config.JOBS_DIR / match.group(1),
                RESOURCE_ROOT,
            )
        except Exception:
            logger.exception(
                "realtime review enrichment failed job_id=%s",
                match.group(1),
            )
    return await call_next(request)


def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("VIGORYSYNC_OFFLINE_PORT", "8030")),
        log_level="info",
    )
