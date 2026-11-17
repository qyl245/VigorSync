"""FastAPI application for the VigorSync M1 Live Engine."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .manager import SessionManager, SessionNotFoundError
from .runtime import CameraBusyError, InvalidTransitionError, SessionRuntime


settings = Settings.from_env()
manager = SessionManager(settings)


class SessionStartPayload(BaseModel):
    exercise: Literal["squat", "pushup"]
    view: Literal[
        "front",
        "rear",
        "oblique_left",
        "oblique_right",
        "side_left",
        "side_right",
        "unknown",
    ] = "side_left"
    target_reps: int = Field(default=12, ge=1, le=500)
    session_dir: str | None = None
    source_kind: Literal["camera", "video"] = "camera"
    video_path: str | None = None


def _require_internal_token(
    token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    if (
        settings.internal_token is not None
        and not secrets.compare_digest(
            token or "",
            settings.internal_token,
        )
    ):
        raise HTTPException(status_code=401, detail="invalid internal token")


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="development API is local-only")


def _session_payload(runtime: SessionRuntime) -> dict:
    session_id = runtime.request.session_id
    return {
        "session_id": session_id,
        "status": runtime.status,
        "exercise": runtime.request.exercise,
        "view": runtime.request.view,
        "target_reps": runtime.request.target_reps,
        "stream_url": f"http://127.0.0.1:{settings.port}/live/{session_id}/stream.mjpg",
        "ws_url": f"ws://127.0.0.1:{settings.port}/ws/live/{session_id}",
        "metrics": runtime.latest_metrics,
        "counts": runtime.counts,
        "startup_timings": runtime.startup_timings,
        "error": runtime.error,
    }


def _start_session(
    session_id: str,
    payload: SessionStartPayload,
) -> SessionRuntime:
    return manager.start(
        session_id=session_id,
        exercise=payload.exercise,
        view=payload.view,
        target_reps=payload.target_reps,
        session_dir=payload.session_dir,
        source_kind=payload.source_kind,
        video_path=payload.video_path,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    manager.close_all()


app = FastAPI(
    title="VigorSync Live Engine",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(CameraBusyError)
async def camera_busy_handler(_: Request, exc: CameraBusyError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "camera_busy",
                "message": str(exc),
                "details": {},
                "request_id": f"req_{secrets.token_hex(6)}",
            }
        },
    )


@app.exception_handler(InvalidTransitionError)
async def invalid_transition_handler(
    _: Request,
    exc: InvalidTransitionError,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "invalid_transition",
                "message": str(exc),
                "details": {},
                "request_id": f"req_{secrets.token_hex(6)}",
            }
        },
    )


@app.get("/api/v1/health")
def health() -> dict:
    return manager.health()


@app.post(
    "/internal/v1/sessions/{session_id}/start",
    dependencies=[Depends(_require_internal_token)],
)
def start_internal(
    session_id: str,
    payload: SessionStartPayload,
) -> dict:
    try:
        runtime = _start_session(session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_payload(runtime)


@app.post(
    "/internal/v1/sessions/{session_id}/pause",
    dependencies=[Depends(_require_internal_token)],
)
def pause_internal(session_id: str) -> dict:
    try:
        return _session_payload(manager.pause(session_id))
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.post(
    "/internal/v1/sessions/{session_id}/resume",
    dependencies=[Depends(_require_internal_token)],
)
def resume_internal(session_id: str) -> dict:
    try:
        return _session_payload(manager.resume(session_id))
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.post(
    "/internal/v1/sessions/{session_id}/stop",
    dependencies=[Depends(_require_internal_token)],
)
async def stop_internal(session_id: str) -> dict:
    try:
        return await asyncio.to_thread(manager.stop, session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.post("/dev/v1/sessions")
def start_dev(payload: SessionStartPayload, request: Request) -> dict:
    if not settings.enable_dev_api:
        raise HTTPException(status_code=404, detail="development API disabled")
    _require_loopback(request)
    session_id = manager.new_session_id()
    try:
        runtime = _start_session(session_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_payload(runtime)


@app.get("/dev/v1/sessions/{session_id}")
def get_dev(session_id: str, request: Request) -> dict:
    _require_loopback(request)
    try:
        return _session_payload(manager.get(session_id))
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.post("/dev/v1/sessions/{session_id}/pause")
def pause_dev(session_id: str, request: Request) -> dict:
    _require_loopback(request)
    try:
        return _session_payload(manager.pause(session_id))
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.post("/dev/v1/sessions/{session_id}/resume")
def resume_dev(session_id: str, request: Request) -> dict:
    _require_loopback(request)
    try:
        return _session_payload(manager.resume(session_id))
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.post("/dev/v1/sessions/{session_id}/stop")
async def stop_dev(session_id: str, request: Request) -> dict:
    _require_loopback(request)
    try:
        return await asyncio.to_thread(manager.stop, session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.get("/live/{session_id}/stream.mjpg")
def stream_mjpeg(session_id: str) -> StreamingResponse:
    try:
        runtime = manager.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc

    async def frames():
        version = -1
        while True:
            next_version, jpeg = await asyncio.to_thread(
                runtime.wait_for_jpeg,
                version,
                1.0,
            )
            if jpeg is None or next_version == version:
                if runtime.status in {"failed", "finalized"}:
                    await asyncio.sleep(0.05)
                continue
            version = next_version
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n"
                + jpeg
                + b"\r\n"
            )

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
    )


@app.websocket("/ws/live/{session_id}")
async def live_websocket(websocket: WebSocket, session_id: str) -> None:
    try:
        runtime = manager.get(session_id)
    except SessionNotFoundError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    cursor = 0
    try:
        while True:
            events = runtime.events_after(cursor)
            if events:
                for event in events:
                    await websocket.send_json(event)
                    cursor = int(event["seq"])
            else:
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return


def run() -> None:
    import uvicorn

    uvicorn.run(
        "vigorsync_live.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
