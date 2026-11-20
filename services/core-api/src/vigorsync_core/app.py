"""VigorSync Core API: pairing, session orchestration and realtime fan-out."""

from __future__ import annotations

import asyncio
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .config import Settings
from .db import Database
from .events import EventHub
from .live_client import LiveClient, LiveEngineError
from .offline_client import OfflineClient, OfflineWorkerError
from .state import InvalidTransition, command_target, internal_target


class SessionCreate(BaseModel):
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
    notes: str | None = None


class PairingClaim(BaseModel):
    pairing_code: str
    device_name: str
    platform: str


class ControlConfig(BaseModel):
    exercise: Literal["squat", "pushup"]
    view: Literal[
        "front",
        "rear",
        "oblique_left",
        "oblique_right",
        "side_left",
        "side_right",
        "unknown",
    ]
    target_reps: int = Field(default=12, ge=1, le=500)


class InternalState(BaseModel):
    status: str
    t_ms: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class InternalFinalize(BaseModel):
    artifacts: dict[str, str]
    summary: dict[str, Any]


def public_session(record: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "session_id": record["session_id"],
        "status": record["status"],
        "exercise": record["exercise"],
        "view": record["view"],
        "target_reps": record["target_reps"],
        "notes": record["notes"],
        "created_at": record["created_at"],
        "started_at": record["started_at"],
        "ended_at": record["ended_at"],
        "job_id": record["job_id"],
        "report_url": record["report_url"],
        "live_summary": record["live_summary"],
        "error": record["error"],
    }
    if record.get("job_id") is not None or record.get("analysis_stage"):
        payload["analysis"] = {
            "job_id": record["job_id"],
            "stage": record.get("analysis_stage"),
            "progress": record.get("analysis_progress") or 0.0,
            "summary": record.get("analysis_summary"),
        }
    return payload


def create_app(
    settings: Settings | None = None,
    live_client: LiveClient | None = None,
    offline_client: OfflineClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    database = Database(settings.db_path)
    owns_live_client = live_client is None
    live = live_client or LiveClient(
        settings.live_url,
        settings.live_internal_token,
    )
    owns_offline_client = offline_client is None
    offline = offline_client or OfflineClient(
        settings.offline_url,
        settings.offline_internal_token,
    )
    hub = EventHub()
    analysis_tasks: dict[str, asyncio.Task[None]] = {}
    latest_config: dict[str, Any] = {
        "exercise": "squat",
        "view": "side_left",
        "target_reps": 12,
    }

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            for record in database.list_sessions(status="analyzing", limit=100):
                if record.get("job_id"):
                    start_analysis_monitor(
                        record["session_id"],
                        record["job_id"],
                    )
            yield
        finally:
            tasks = list(analysis_tasks.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if owns_live_client:
                await live.close()
            if owns_offline_client:
                await offline.close()

    app = FastAPI(title="VigorSync Core", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    async def require_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="unauthorized")
        if not database.token_is_valid(authorization[7:].strip()):
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_internal(
        token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    ) -> None:
        expected = settings.live_internal_token
        if expected and token != expected:
            raise HTTPException(status_code=401, detail="invalid internal token")

    def require_loopback(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=403, detail="development API is local-only")

    @app.exception_handler(LiveEngineError)
    async def live_error_handler(_: Request, exc: LiveEngineError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": {},
                    "request_id": f"req_{secrets.token_hex(6)}",
                }
            },
        )

    @app.exception_handler(InvalidTransition)
    async def transition_error_handler(
        _: Request,
        exc: InvalidTransition,
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

    def get_session_or_404(session_id: str) -> dict[str, Any]:
        try:
            return database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session_not_found") from exc

    async def append_and_publish(
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        t_ms: int = 0,
    ) -> dict[str, Any]:
        event = database.append_event(session_id, event_type, payload, t_ms=t_ms)
        await hub.publish("client", event)
        await hub.publish("mirror", event)
        return event

    async def set_session_status(
        session_id: str,
        status: str,
        previous: str,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {"status": status}
        if status == "preparing" and previous == "created":
            changes["started_at"] = database.get_session(session_id)["created_at"]
        if status == "analyzing":
            from .db import utc_now

            changes["ended_at"] = utc_now()
        record = database.update_session(session_id, **changes)
        await append_and_publish(
            session_id,
            "session.state",
            {"status": status, "previous_status": previous},
        )
        return record

    async def complete_without_analysis(
        session_id: str,
        live_summary: dict[str, Any],
        previous_status: str,
    ) -> dict[str, Any]:
        from .db import utc_now

        summary = {
            "status": "analysis_disabled",
            "rep_count": int(live_summary.get("reps") or 0),
            "rejected_rep_count": 0,
            "duration_s": float(live_summary.get("elapsed_s") or 0.0),
            "quality_status": "not_analyzed",
            "active_issue_count": 0,
            "severity_counts": {},
            "mean_confidence": float(live_summary.get("confidence") or 0.0),
            "issues": [],
        }
        record = database.update_session(
            session_id,
            status="completed",
            ended_at=utc_now(),
            analysis_stage="disabled",
            analysis_progress=0.0,
            analysis_summary=summary,
            report_ticket=None,
            report_url=None,
        )
        await append_and_publish(
            session_id,
            "session.state",
            {
                "status": "completed",
                "previous_status": previous_status,
            },
        )
        return record

    def analysis_summary(result: dict[str, Any]) -> dict[str, Any]:
        sample = result.get("sample") or {}
        issues = [
            {
                "issue_code": issue.get("issue_code"),
                "title": issue.get("title"),
                "detail": issue.get("detail"),
                "cue": issue.get("cue"),
                "severity": issue.get("severity"),
                "confidence": issue.get("confidence"),
                "anchor_s": issue.get("anchor_s"),
                "evidence_interval_s": issue.get("evidence_interval_s") or [],
            }
            for issue in (result.get("issues") or [])
            if not issue.get("suppressed")
        ][:5]
        return {
            "status": sample.get("status") or "unknown",
            "rep_count": sample.get("rep_count") or 0,
            "rejected_rep_count": sample.get("rejected_rep_count") or 0,
            "duration_s": sample.get("duration_seconds") or 0.0,
            "quality_status": sample.get("quality_status") or "unknown",
            "active_issue_count": sample.get("active_issue_count") or 0,
            "severity_counts": sample.get("severity_counts") or {},
            "mean_confidence": (
                (result.get("confidence") or {}).get("mean_confidence") or 0.0
            ),
            "issues": issues,
        }

    async def mark_analysis_failed(
        session_id: str,
        code: str,
        message: str,
        *,
        stage: str = "analysis_failed",
    ) -> None:
        record = get_session_or_404(session_id)
        if record["status"] == "failed":
            return
        database.update_session(
            session_id,
            status="failed",
            analysis_stage=stage,
            error={"code": code, "message": message[:512]},
        )
        await append_and_publish(
            session_id,
            "session.state",
            {"status": "failed", "previous_status": record["status"]},
        )
        await hub.publish(
            "client",
            {
                "type": "error",
                "version": "v1",
                "session_id": session_id,
                "error": {"code": code, "message": message[:512]},
            },
        )

    async def monitor_analysis(session_id: str, job_id: str) -> None:
        last_stage: str | None = None
        last_progress = -1.0
        deadline = asyncio.get_running_loop().time() + 15 * 60
        while asyncio.get_running_loop().time() < deadline:
            try:
                job = await offline.get_job(job_id)
            except OfflineWorkerError as exc:
                await asyncio.sleep(2.0)
                if asyncio.get_running_loop().time() >= deadline:
                    await mark_analysis_failed(
                        session_id,
                        exc.code,
                        str(exc),
                    )
                    return
                continue

            stage = str(job.get("stage") or "unknown")
            progress = float(job.get("progress") or 0.0)
            if stage != last_stage or progress != last_progress:
                last_stage = stage
                last_progress = progress
                database.update_session(
                    session_id,
                    analysis_stage=stage,
                    analysis_progress=progress,
                )
                progress_event = {
                    "type": "report.progress",
                    "version": "v1",
                    "session_id": session_id,
                    "payload": {
                        "job_id": job_id,
                        "stage": stage,
                        "progress": progress,
                    },
                }
                await hub.publish("client", progress_event)
                await hub.publish("mirror", progress_event)

            status = job.get("status")
            if status == "succeeded":
                try:
                    result = await offline.get_result(job_id)
                except OfflineWorkerError as exc:
                    await mark_analysis_failed(
                        session_id,
                        exc.code,
                        str(exc),
                        stage="result",
                    )
                    return
                summary = analysis_summary(result)
                ticket = secrets.token_urlsafe(24)
                report_url = (
                    f"/api/v1/reports/{ticket}/report_v2.html"
                )
                previous_status = get_session_or_404(session_id)["status"]
                database.update_session(
                    session_id,
                    status="completed",
                    analysis_stage="completed",
                    analysis_progress=1.0,
                    analysis_summary=summary,
                    report_ticket=ticket,
                    report_url=report_url,
                )
                await append_and_publish(
                    session_id,
                    "session.state",
                    {
                        "status": "completed",
                        "previous_status": previous_status,
                    },
                )
                event = {
                    "type": "report.ready",
                    "version": "v1",
                    "session_id": session_id,
                    "job_id": job_id,
                    "report_url": report_url,
                    "summary": summary,
                }
                await hub.publish("client", event)
                await hub.publish("mirror", event)
                return
            if status in {"failed", "cancelled"}:
                await mark_analysis_failed(
                    session_id,
                    str(job.get("error_code") or f"analysis_{status}"),
                    str(job.get("error_message") or "离线分析失败"),
                    stage=stage,
                )
                return
            await asyncio.sleep(1.0)

        await mark_analysis_failed(
            session_id,
            "analysis_timeout",
            "离线分析超时",
        )

    def start_analysis_monitor(session_id: str, job_id: str) -> None:
        existing = analysis_tasks.get(session_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(monitor_analysis(session_id, job_id))
        analysis_tasks[session_id] = task

        def finish(_: asyncio.Task[None]) -> None:
            if analysis_tasks.get(session_id) is task:
                analysis_tasks.pop(session_id, None)

        task.add_done_callback(finish)

    async def enqueue_analysis(
        session_id: str,
        artifacts: dict[str, str],
    ) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        job_id = record.get("job_id") or f"job_{secrets.token_hex(8)}"
        video_path = artifacts.get("video_path")
        database.update_session(
            session_id,
            job_id=job_id,
            analysis_stage="queued",
            analysis_progress=0.0,
        )
        if not video_path or not Path(video_path).is_file():
            await mark_analysis_failed(
                session_id,
                "artifact_missing",
                "训练视频不存在，无法创建离线分析任务",
                stage="validate_artifacts",
            )
            return get_session_or_404(session_id)
        try:
            await offline.enqueue(
                {
                    "job_id": job_id,
                    "session_id": session_id,
                    "source_path": video_path,
                    "exercise": record["exercise"],
                    "view": record["view"],
                    "rotation": "none",
                },
            )
        except OfflineWorkerError as exc:
            await mark_analysis_failed(
                session_id,
                "analysis_enqueue_failed",
                str(exc),
                stage="enqueue",
            )
            return get_session_or_404(session_id)
        database.update_session(
            session_id,
            analysis_stage="queued",
            analysis_progress=0.0,
        )
        start_analysis_monitor(session_id, job_id)
        return get_session_or_404(session_id)

    async def create_session(payload: SessionCreate) -> dict[str, Any]:
        session_id = f"s_{secrets.token_hex(12)}"
        return database.create_session(
            session_id=session_id,
            exercise=payload.exercise,
            view=payload.view,
            target_reps=payload.target_reps,
            notes=payload.notes,
        )

    async def start_session(session_id: str) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        target = command_target(record["status"], "start")
        if target == record["status"]:
            return record
        try:
            live_result = await live.start(
                session_id,
                {
                    "exercise": record["exercise"],
                    "view": record["view"],
                    "target_reps": record["target_reps"],
                    "session_dir": str(
                        settings.session_root / session_id
                    ),
                },
            )
        except LiveEngineError:
            database.update_session(
                session_id,
                status="failed",
                error={
                    "code": "live_engine_unavailable",
                    "message": "Live Engine failed to start session",
                },
            )
            raise
        record = await set_session_status(session_id, "preparing", record["status"])
        await hub.publish(
            "mirror",
            {
                "type": "session.start",
                "version": "v1",
                "session_id": session_id,
                "payload": {
                    "exercise": record["exercise"],
                    "view": record["view"],
                    "target_reps": record["target_reps"],
                    "stream_url": live_result.get("stream_url"),
                    "ws_url": live_result.get("ws_url"),
                },
            },
        )
        return record

    async def perform_command(
        session_id: str,
        command: str,
    ) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        if command == "start":
            return await start_session(session_id)
        target = command_target(record["status"], command)
        if command == "cancel":
            if record["status"] in {"active", "paused", "preparing"}:
                try:
                    await live.stop(session_id)
                except LiveEngineError:
                    pass
            return await set_session_status(session_id, target, record["status"])
        if command == "pause":
            await live.pause(session_id)
        elif command == "resume":
            await live.resume(session_id)
        elif command == "stop":
            result = await live.stop(session_id)
            database.update_session(
                session_id,
                artifacts=result.get("artifacts"),
                live_summary=result.get("summary"),
            )
            if not settings.offline_analysis_enabled:
                return await complete_without_analysis(
                    session_id,
                    result.get("summary") or {},
                    record["status"],
                )
            await set_session_status(session_id, "analyzing", record["status"])
            return await enqueue_analysis(
                session_id,
                result.get("artifacts") or {},
            )
        return await set_session_status(session_id, target, record["status"])

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        live_status = "ok"
        try:
            await live.health()
        except LiveEngineError:
            live_status = "unavailable"
        if settings.offline_analysis_enabled:
            offline_status = "ok"
            try:
                await offline.health()
            except OfflineWorkerError:
                offline_status = "unavailable"
        else:
            offline_status = "disabled"
        overall = (
            "ok"
            if live_status == "ok"
            and (
                not settings.offline_analysis_enabled
                or offline_status == "ok"
            )
            else "degraded"
        )
        return {
            "status": overall,
            "version": "0.1.0",
            "live_engine": live_status,
            "offline_worker": offline_status,
        }

    @app.post("/api/v1/pairing/claim")
    async def claim_pairing(payload: PairingClaim) -> dict[str, Any]:
        if not secrets.compare_digest(
            payload.pairing_code,
            settings.pairing_code,
        ):
            raise HTTPException(status_code=401, detail="invalid_pairing_code")
        token = f"pair_{secrets.token_urlsafe(24)}"
        database.create_token(
            token=token,
            device_name=payload.device_name,
            platform=payload.platform,
        )
        return {
            "token": token,
            "core_url": f"http://127.0.0.1:{settings.port}",
            "expires_at": None,
        }

    @app.post(
        "/api/v1/control/config",
        dependencies=[Depends(require_token)],
    )
    async def update_control_config(payload: ControlConfig) -> dict[str, Any]:
        latest_config.update(payload.model_dump())
        await hub.publish(
            "mirror",
            {
                "type": "session.config",
                "version": "v1",
                "payload": dict(latest_config),
            },
        )
        return dict(latest_config)

    @app.post("/api/v1/sessions", dependencies=[Depends(require_token)])
    async def create_authenticated_session(
        payload: SessionCreate,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        scope = "POST:/api/v1/sessions"
        cached = database.get_idempotent(scope, idempotency_key)
        if cached is not None:
            return cached
        record = await create_session(payload)
        response = public_session(record)
        database.save_idempotent(scope, idempotency_key, response)
        return response

    @app.post("/api/v1/sessions/start", dependencies=[Depends(require_token)])
    async def create_and_start_session(
        payload: SessionCreate,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        scope = "POST:/api/v1/sessions/start"
        cached = database.get_idempotent(scope, idempotency_key)
        if cached is not None:
            return cached
        record = await create_session(payload)
        record = await start_session(record["session_id"])
        response = public_session(record)
        database.save_idempotent(scope, idempotency_key, response)
        return response

    @app.get("/api/v1/sessions", dependencies=[Depends(require_token)])
    async def list_sessions(
        status: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        records = database.list_sessions(status=status, limit=limit)
        return {"items": [public_session(record) for record in records]}

    @app.get("/api/v1/sessions/{session_id}", dependencies=[Depends(require_token)])
    async def get_session(session_id: str) -> dict[str, Any]:
        return public_session(get_session_or_404(session_id))

    async def command_endpoint(
        session_id: str,
        command: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        scope = f"POST:/api/v1/sessions/{session_id}/{command}"
        cached = database.get_idempotent(scope, idempotency_key)
        if cached is not None:
            return cached
        record = await perform_command(session_id, command)
        response = public_session(record)
        database.save_idempotent(scope, idempotency_key, response)
        return response

    @app.post(
        "/api/v1/sessions/{session_id}/start",
        dependencies=[Depends(require_token)],
    )
    async def start_command(
        session_id: str,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await command_endpoint(session_id, "start", idempotency_key)

    @app.post(
        "/api/v1/sessions/{session_id}/pause",
        dependencies=[Depends(require_token)],
    )
    async def pause_command(
        session_id: str,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await command_endpoint(session_id, "pause", idempotency_key)

    @app.post(
        "/api/v1/sessions/{session_id}/resume",
        dependencies=[Depends(require_token)],
    )
    async def resume_command(
        session_id: str,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await command_endpoint(session_id, "resume", idempotency_key)

    @app.post(
        "/api/v1/sessions/{session_id}/stop",
        dependencies=[Depends(require_token)],
    )
    async def stop_command(
        session_id: str,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await command_endpoint(session_id, "stop", idempotency_key)

    @app.post(
        "/api/v1/sessions/{session_id}/mirror/dismiss",
        dependencies=[Depends(require_token)],
    )
    async def dismiss_mirror_session(session_id: str) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        if record["status"] in {"created", "preparing", "active", "paused"}:
            raise HTTPException(status_code=409, detail="session_not_finished")
        await hub.publish(
            "mirror",
            {
                "type": "session.dismissed",
                "version": "v1",
                "session_id": session_id,
                "payload": {"status": record["status"]},
            },
        )
        return {"session_id": session_id, "dismissed": True}

    @app.post(
        "/api/v1/sessions/{session_id}/cancel",
        dependencies=[Depends(require_token)],
    )
    async def cancel_command(
        session_id: str,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await command_endpoint(session_id, "cancel", idempotency_key)

    @app.get(
        "/api/v1/sessions/{session_id}/events",
        dependencies=[Depends(require_token)],
    )
    async def get_events(
        session_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, Any]:
        get_session_or_404(session_id)
        items = database.list_events(
            session_id,
            after_seq=after_seq,
            limit=limit,
        )
        next_seq = items[-1]["seq"] if items else after_seq
        return {"items": items, "next_seq": next_seq}

    @app.get(
        "/api/v1/sessions/{session_id}/report",
        dependencies=[Depends(require_token)],
    )
    async def get_report(session_id: str) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        if not record["report_url"]:
            raise HTTPException(status_code=409, detail="report_not_ready")
        return {
            "session_id": session_id,
            "job_id": record["job_id"],
            "status": record["status"],
            "report_url": record["report_url"],
            "summary": record.get("analysis_summary") or {},
        }

    @app.get("/api/v1/reports/{report_ticket}/{asset_path:path}")
    async def report_asset(
        report_ticket: str,
        asset_path: str,
    ) -> Response:
        record = database.get_session_by_report_ticket(report_ticket)
        if record is None or not record.get("job_id"):
            raise HTTPException(status_code=404, detail="report_not_found")
        job_id = record["job_id"]
        if settings.offline_data_root is None:
            raise HTTPException(status_code=503, detail="offline_data_unavailable")
        artifacts_dir = (
            settings.offline_data_root
            / "jobs"
            / job_id
            / "artifacts"
            / job_id
        ).resolve()

        if asset_path in {"", "report.html", "report_v2.html"}:
            html_path = artifacts_dir / "report_v2.html"
            if not html_path.is_file():
                raise HTTPException(status_code=404, detail="report_not_found")
            html = html_path.read_text(encoding="utf-8")
            worker_prefix = f"/api/v1/jobs/{job_id}"
            public_prefix = f"/api/v1/reports/{report_ticket}"
            html = html.replace(
                f"{worker_prefix}/media",
                f"{public_prefix}/media",
            ).replace(
                f"{worker_prefix}/frames/",
                f"{public_prefix}/frames/",
            )
            return Response(
                content=html,
                media_type="text/html",
                headers={"Cache-Control": "no-store"},
            )

        if asset_path == "media":
            path = artifacts_dir / "media" / f"{job_id}_clip.webm"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="media_not_found")
            return FileResponse(
                path,
                media_type="video/webm",
                filename=path.name,
            )

        if asset_path.startswith("frames/"):
            frame_name = asset_path.removeprefix("frames/")
            if not frame_name or Path(frame_name).name != frame_name:
                raise HTTPException(status_code=404, detail="frame_not_found")
            path = (artifacts_dir / "annotated_frames" / frame_name).resolve()
            try:
                path.relative_to(artifacts_dir / "annotated_frames")
            except ValueError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="frame_not_found",
                ) from exc
            if not path.is_file():
                raise HTTPException(status_code=404, detail="frame_not_found")
            return FileResponse(path, media_type="image/png")

        raise HTTPException(status_code=404, detail="report_asset_not_found")

    @app.delete(
        "/api/v1/sessions/{session_id}/media",
        dependencies=[Depends(require_token)],
    )
    async def delete_media(session_id: str, include_report: bool = False) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        artifacts = record.get("artifacts") or {}
        removed: list[str] = []
        for key in ("video_path", "landmarks_path"):
            path = artifacts.get(key)
            if path:
                candidate = Path(path)
                if candidate.is_file():
                    candidate.unlink()
                    removed.append(str(candidate))
        if include_report and record["report_url"]:
            database.update_session(session_id, report_url=None)
        return {"removed": removed}

    @app.delete(
        "/api/v1/sessions/{session_id}",
        dependencies=[Depends(require_token)],
    )
    async def delete_session(session_id: str) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        in_progress = record["status"] in {
            "created",
            "preparing",
            "active",
            "paused",
            "stopping",
        } or (record["status"] == "analyzing" and record.get("job_id"))
        if in_progress:
            raise HTTPException(status_code=409, detail="session_in_progress")

        session_root = settings.session_root.resolve()
        candidate = session_root / session_id
        resolved = candidate.resolve()
        if (
            candidate.name != session_id
            or candidate.is_symlink()
            or resolved.parent != session_root
        ):
            raise HTTPException(status_code=400, detail="invalid_session_path")

        staged: Path | None = None
        if candidate.exists() or candidate.is_symlink():
            staged = session_root / (
                f".deleting-{session_id}-{secrets.token_hex(4)}"
            )
            candidate.replace(staged)
        try:
            database.delete_session(session_id)
        except Exception:
            if staged is not None and staged.exists():
                staged.replace(candidate)
            raise
        if staged is not None:
            shutil.rmtree(staged)

        job_id = record.get("job_id")
        if job_id and settings.offline_data_root is not None:
            jobs_root = (settings.offline_data_root / "jobs").resolve()
            offline_job = (jobs_root / job_id).resolve()
            try:
                offline_job.relative_to(jobs_root)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="invalid_offline_job_path",
                ) from exc
            if offline_job.is_dir():
                shutil.rmtree(offline_job)

        payload = {
            "type": "session.deleted",
            "version": "v1",
            "session_id": session_id,
            "payload": {},
        }
        await hub.publish("client", payload)
        await hub.publish("mirror", payload)
        return {"session_id": session_id, "deleted": True}

    @app.post(
        "/api/v1/internal/sessions/{session_id}/state",
        dependencies=[Depends(require_internal)],
    )
    async def receive_live_state(
        session_id: str,
        payload: InternalState,
    ) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        status_changed = (
            payload.status in {"active", "paused"}
            and record["status"] != payload.status
        )
        if status_changed:
            try:
                database.update_session(session_id, status=payload.status)
            except Exception:
                status_changed = False
        if status_changed:
            await append_and_publish(
                session_id,
                "session.state",
                {
                    "status": payload.status,
                    "previous_status": record["status"],
                },
            )
        event = database.append_event(
            session_id,
            "live.metrics",
            payload.payload,
            t_ms=payload.t_ms,
        )
        await hub.publish("client", event)
        await hub.publish("mirror", event)
        return {"accepted": True, "seq": event["seq"]}

    @app.post(
        "/api/v1/internal/sessions/{session_id}/finalize",
        dependencies=[Depends(require_internal)],
    )
    async def receive_finalize(
        session_id: str,
        payload: InternalFinalize,
    ) -> dict[str, Any]:
        record = get_session_or_404(session_id)
        target = internal_target(record["status"], "finalize_accepted")
        database.update_session(
            session_id,
            artifacts=payload.artifacts,
            live_summary=payload.summary,
        )
        if not settings.offline_analysis_enabled:
            completed = await complete_without_analysis(
                session_id,
                payload.summary,
                record["status"],
            )
            return {
                "session_id": session_id,
                "status": completed["status"],
                "job_id": None,
            }
        await set_session_status(
            session_id,
            target,
            record["status"],
        )
        analyzed = await enqueue_analysis(
            session_id,
            payload.artifacts,
        )
        return {
            "session_id": session_id,
            "status": analyzed["status"],
            "job_id": analyzed["job_id"],
        }

    @app.get("/dev/v1/bootstrap")
    async def dev_bootstrap(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return {
            "core_url": f"http://127.0.0.1:{settings.port}",
            "mirror_token": settings.mirror_token,
            "pairing_code": settings.pairing_code,
        }

    @app.get("/dev/v1/config")
    async def dev_config(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return dict(latest_config)

    @app.post("/dev/v1/sessions")
    async def dev_create(payload: SessionCreate, request: Request) -> dict[str, Any]:
        require_loopback(request)
        record = await create_session(payload)
        try:
            record = await start_session(record["session_id"])
        except Exception:
            database.update_session(
                record["session_id"],
                status="failed",
                error={
                    "code": "live_engine_unavailable",
                    "message": "Live Engine failed to start session",
                },
            )
            raise
        return {
            **public_session(record),
            "core_url": f"http://127.0.0.1:{settings.port}",
            "stream_url": f"http://127.0.0.1:8020/live/{record['session_id']}/stream.mjpg",
            "ws_url": f"ws://127.0.0.1:8020/ws/live/{record['session_id']}",
        }

    @app.get("/dev/v1/sessions/{session_id}")
    async def dev_get(session_id: str, request: Request) -> dict[str, Any]:
        require_loopback(request)
        record = get_session_or_404(session_id)
        return {
            **public_session(record),
            "artifacts": record.get("artifacts"),
            "stream_url": (
                f"http://127.0.0.1:8020/live/{session_id}/stream.mjpg"
            ),
            "ws_url": f"ws://127.0.0.1:8020/ws/live/{session_id}",
        }

    @app.get("/dev/v1/sessions")
    async def dev_list(request: Request) -> dict[str, Any]:
        require_loopback(request)
        records = database.list_sessions(limit=50)
        return {"items": [public_session(record) for record in records]}

    async def dev_command(session_id: str, command: str, request: Request) -> dict[str, Any]:
        require_loopback(request)
        record = await perform_command(session_id, command)
        return {
            **public_session(record),
            "artifacts": record.get("artifacts"),
        }

    @app.post("/dev/v1/sessions/{session_id}/pause")
    async def dev_pause(session_id: str, request: Request) -> dict[str, Any]:
        return await dev_command(session_id, "pause", request)

    @app.post("/dev/v1/sessions/{session_id}/resume")
    async def dev_resume(session_id: str, request: Request) -> dict[str, Any]:
        return await dev_command(session_id, "resume", request)

    @app.post("/dev/v1/sessions/{session_id}/stop")
    async def dev_stop(session_id: str, request: Request) -> dict[str, Any]:
        return await dev_command(session_id, "stop", request)

    @app.post("/dev/v1/sessions/{session_id}/cancel")
    async def dev_cancel(session_id: str, request: Request) -> dict[str, Any]:
        return await dev_command(session_id, "cancel", request)

    async def websocket_stream(
        websocket: WebSocket,
        channel: str,
        token: str | None,
    ) -> None:
        authorization = websocket.headers.get("authorization", "")
        header_token = (
            authorization[7:].strip()
            if authorization.startswith("Bearer ")
            else None
        )
        requested_protocols = websocket.scope.get("subprotocols", [])
        protocol_token = next(
            (
                protocol.removeprefix("vigorsync.token.")
                for protocol in requested_protocols
                if protocol.startswith("vigorsync.token.")
            ),
            None,
        )
        token = token or header_token or protocol_token
        valid = (
            token == settings.mirror_token
            if channel == "mirror"
            else bool(token and database.token_is_valid(token))
        )
        if not valid:
            await websocket.close(code=4401)
            return
        selected_protocol = (
            "vigorsync.v1"
            if "vigorsync.v1" in requested_protocols
            else None
        )
        await websocket.accept(subprotocol=selected_protocol)
        latest = database.list_sessions(limit=1)
        await websocket.send_json(
            {
                "type": "session.snapshot",
                "version": "v1",
                "payload": public_session(latest[0]) if latest else None,
            }
        )
        try:
            async for event in hub.subscribe(channel):
                await websocket.send_json(event)
        except WebSocketDisconnect:
            return

    @app.websocket("/api/v1/ws/client")
    async def client_websocket(
        websocket: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        await websocket_stream(websocket, "client", token)

    @app.websocket("/api/v1/ws/mirror")
    async def mirror_websocket(
        websocket: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        await websocket_stream(websocket, "mirror", token)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "vigorsync_core.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
