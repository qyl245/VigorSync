"""VigorSync Core API: pairing, session orchestration and realtime fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
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
from .db import Database, utc_now
from .discovery import DiscoveryResponder
from .events import EventHub
from .live_client import LiveClient, LiveEngineError
from .offline_client import OfflineClient, OfflineWorkerError
from .state import InvalidTransition, command_target, internal_target


LOGGER = logging.getLogger(__name__)


class MirrorModuleItem(BaseModel):
    id: Literal["weather", "trend", "todo", "weekly_plan"]
    size: Literal["1x1", "1x2"]
    column: Literal["left", "right"]
    order: int = Field(ge=0, le=3)


class MirrorLayoutUpdate(BaseModel):
    modules: list[MirrorModuleItem] = Field(max_length=8)


class MirrorFocusBody(BaseModel):
    page: Literal["home", "training", "history", "profile"]


class MirrorThemeBody(BaseModel):
    mode: Literal["light", "dark"]


ViewName = Literal[
    "front",
    "rear",
    "oblique_left",
    "oblique_right",
    "side_left",
    "side_right",
    "unknown",
]


class TodoCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    todo_date: date | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)


class TodoUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    completed: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)


class WeeklyPlanBody(BaseModel):
    id: str | None = None
    weekday: int = Field(ge=1, le=7)
    exercise: Literal["squat", "pushup"]
    view: ViewName | None = None
    sets: int | None = Field(default=None, ge=1, le=20)
    target_reps: int = Field(ge=1, le=500)
    note: str | None = Field(default=None, max_length=300)
    sort_order: int | None = Field(default=None, ge=0, le=1000)


class WeeklyPlanBatchBody(BaseModel):
    items: list[WeeklyPlanBody] = Field(max_length=50)


WEATHER_CODE_TEXT = {
    0: "晴",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    56: "冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷暴",
    96: "雷阵雨",
    99: "雷阵雨",
}

WEATHER_TTL_S = 1800.0

DEFAULT_MIRROR_MODULES = [
    {"id": "weather", "size": "1x2", "column": "left", "order": 0},
    {"id": "todo", "size": "1x2", "column": "left", "order": 1},
    {"id": "trend", "size": "1x2", "column": "right", "order": 0},
    {"id": "weekly_plan", "size": "1x2", "column": "right", "order": 1},
]


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
    client_connections = {"count": 0}
    client_sockets: dict[str, set[WebSocket]] = {}
    weather_cache: dict[str, Any] = {}

    async def publish_pairing_state() -> None:
        await hub.publish(
            "mirror",
            {
                "type": "pairing.state",
                "version": "v1",
                "payload": {"connected": client_connections["count"] > 0},
            },
        )

    async def disconnect_token_clients(token: str) -> None:
        sockets = tuple(client_sockets.pop(token, ()))
        if sockets:
            client_connections["count"] = max(
                0,
                client_connections["count"] - len(sockets),
            )
        await publish_pairing_state()
        for socket in sockets:
            try:
                await socket.close(code=4401)
            except RuntimeError:
                # The websocket task may already be finishing its disconnect path.
                pass

    def current_mirror_layout() -> dict[str, Any]:
        layout = database.get_mirror_layout()
        if layout is not None:
            return layout
        return {
            "version": 2,
            "modules": [dict(module) for module in DEFAULT_MIRROR_MODULES],
            "updated_at": utc_now(),
        }

    def current_mirror_theme() -> dict[str, str]:
        mode = database.get_setting("mirror.theme", "dark")
        if mode not in {"light", "dark"}:
            mode = "dark"
        return {"mode": mode}

    async def publish_todos(todo_date: str) -> dict[str, Any]:
        payload = database.list_todos(todo_date)
        message = {
            "type": "todo.updated",
            "version": "v1",
            "payload": payload,
        }
        await hub.publish("client", message)
        await hub.publish("mirror", message)
        return payload

    async def publish_weekly_plans() -> dict[str, Any]:
        payload = database.list_weekly_plans()
        message = {
            "type": "weekly_plan.updated",
            "version": "v1",
            "payload": payload,
        }
        await hub.publish("client", message)
        await hub.publish("mirror", message)
        return payload

    def persisted_weather_payload() -> dict[str, Any] | None:
        raw = database.get_setting("weather.last_payload")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    async def resolve_weather_location() -> dict[str, Any] | None:
        if (
            settings.weather_latitude is not None
            and settings.weather_longitude is not None
        ):
            location_name = settings.weather_location or (
                f"{settings.weather_latitude:.3f}, "
                f"{settings.weather_longitude:.3f}"
            )
            return {
                "name": location_name,
                "latitude": settings.weather_latitude,
                "longitude": settings.weather_longitude,
                "source": "config",
            }

        cached_location = weather_cache.get("location")
        if cached_location is not None:
            return cached_location

        raw_location = database.get_setting("weather.location")
        if raw_location:
            try:
                location = json.loads(raw_location)
                float(location["latitude"])
                float(location["longitude"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                location = None
            if isinstance(location, dict):
                weather_cache["location"] = location
                return location

        if not settings.weather_ip_geolocation:
            return None
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get("https://ipinfo.io/json")
                response.raise_for_status()
                data = response.json()
            loc = str(data.get("loc") or "")
            latitude_text, longitude_text = loc.split(",", 1)
            latitude = float(latitude_text)
            longitude = float(longitude_text)
            city = str(data.get("city") or "").strip()
            region = str(data.get("region") or "").strip()
            location_name = " · ".join(
                value for value in (city, region) if value
            ) or f"{latitude:.3f}, {longitude:.3f}"
            location = {
                "name": location_name,
                "latitude": latitude,
                "longitude": longitude,
                "source": "ip",
            }
            weather_cache["location"] = location
            database.set_setting(
                "weather.location",
                json.dumps(location, ensure_ascii=False),
            )
            return location
        except Exception:
            LOGGER.warning("weather location lookup failed", exc_info=True)
            return None

    async def weather_snapshot() -> dict[str, Any]:
        now = asyncio.get_running_loop().time()
        cached = weather_cache.get("payload")
        if cached is None:
            cached = persisted_weather_payload()
        cached_at = float(weather_cache.get("fetched_monotonic") or 0.0)
        if cached is not None and now - cached_at < WEATHER_TTL_S:
            return {**cached, "cached": True, "stale": False}
        location = await resolve_weather_location()
        if location is None:
            if cached is not None:
                return {**cached, "cached": True, "stale": True}
            return {
                "status": "unavailable",
                "reason": "location_unavailable",
                "fetched_at": utc_now(),
                "cached": False,
            }
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={location['latitude']}"
            f"&longitude={location['longitude']}"
            "&current=temperature_2m,relative_humidity_2m,weather_code"
            "&daily=temperature_2m_max,temperature_2m_min"
            "&forecast_days=1&timezone=auto"
        )
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
            current = data.get("current") or {}
            daily = data.get("daily") or {}
            code = current.get("weather_code")
            payload = {
                "status": "ok",
                "temperature_c": current.get("temperature_2m"),
                "humidity": current.get("relative_humidity_2m"),
                "weather_code": code,
                "weather_text": WEATHER_CODE_TEXT.get(code, "未知"),
                "temp_min_c": (daily.get("temperature_2m_min") or [None])[0],
                "temp_max_c": (daily.get("temperature_2m_max") or [None])[0],
                "location": location["name"],
                "location_source": location["source"],
                "fetched_at": utc_now(),
                "cached": False,
            }
            weather_cache["payload"] = payload
            weather_cache["fetched_monotonic"] = now
            database.set_setting(
                "weather.last_payload",
                json.dumps(payload, ensure_ascii=False),
            )
            return payload
        except Exception:
            if cached is not None:
                return {**cached, "cached": True, "stale": True}
            return {
                "status": "unavailable",
                "reason": "weather_provider_unavailable",
                "fetched_at": utc_now(),
                "cached": False,
            }

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        discovery_responder: DiscoveryResponder | None = None
        if settings.discovery_enabled:
            discovery_responder = DiscoveryResponder(
                port=settings.discovery_port,
                core_port=settings.port,
                device_name=settings.mirror_name,
            )
            try:
                discovery_responder.start()
            except OSError:
                LOGGER.warning(
                    "Unable to start UDP discovery on port %s",
                    settings.discovery_port,
                    exc_info=True,
                )
                discovery_responder.stop()
                discovery_responder = None
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
            if discovery_responder is not None:
                discovery_responder.stop()

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

    def purge_session(session_id: str, record: dict[str, Any]) -> None:
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

    async def publish_session_deleted(session_id: str) -> None:
        payload = {
            "type": "session.deleted",
            "version": "v1",
            "session_id": session_id,
            "payload": {},
        }
        await hub.publish("client", payload)
        await hub.publish("mirror", payload)

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
            "realtime_review": result.get("realtime_review"),
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
        events_path = artifacts.get("events_path")
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
                    "realtime_events_path": events_path,
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
            if record["status"] == "preparing":
                try:
                    await live.stop(session_id)
                except LiveEngineError:
                    pass
            response = {
                **record,
                "status": "cancelled",
                "ended_at": record["ended_at"] or utc_now(),
            }
            purge_session(session_id, record)
            await publish_session_deleted(session_id)
            return response
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

    @app.get("/api/v1/weather", dependencies=[Depends(require_token)])
    async def get_weather() -> dict[str, Any]:
        return await weather_snapshot()

    @app.get("/api/v1/stats/trend", dependencies=[Depends(require_token)])
    async def get_trend(
        days: int = Query(default=7, ge=1, le=31),
    ) -> dict[str, Any]:
        return {"days": database.daily_trend(days)}

    @app.get("/api/v1/mirror/layout", dependencies=[Depends(require_token)])
    async def get_mirror_layout() -> dict[str, Any]:
        return current_mirror_layout()

    @app.put("/api/v1/mirror/layout", dependencies=[Depends(require_token)])
    async def update_mirror_layout(
        payload: MirrorLayoutUpdate,
    ) -> dict[str, Any]:
        modules = [module.model_dump() for module in payload.modules]
        layout = database.set_mirror_layout(modules)
        message = {
            "type": "mirror.layout",
            "version": "v1",
            "payload": layout,
        }
        await hub.publish("mirror", message)
        await hub.publish("client", message)
        return layout

    @app.get("/api/v1/todos", dependencies=[Depends(require_token)])
    async def get_todos(day: date | None = Query(default=None)) -> dict[str, Any]:
        return database.list_todos(day.isoformat() if day else None)

    @app.post("/api/v1/todos", dependencies=[Depends(require_token)])
    async def create_todo(payload: TodoCreateBody) -> dict[str, Any]:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="todo_title_required")
        try:
            todo = database.create_todo(
                todo_id=f"t_{secrets.token_hex(8)}",
                title=title,
                todo_date=(
                    payload.todo_date.isoformat()
                    if payload.todo_date
                    else None
                ),
                sort_order=payload.sort_order,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await publish_todos(todo["todo_date"])
        return todo

    @app.patch("/api/v1/todos/{todo_id}", dependencies=[Depends(require_token)])
    async def update_todo(
        todo_id: str,
        payload: TodoUpdateBody,
    ) -> dict[str, Any]:
        changes = {
            key: value
            for key, value in payload.model_dump(exclude_unset=True).items()
            if value is not None
        }
        if not changes:
            raise HTTPException(status_code=400, detail="no_todo_changes")
        if "title" in changes:
            changes["title"] = str(changes["title"]).strip()
            if not changes["title"]:
                raise HTTPException(
                    status_code=400,
                    detail="todo_title_required",
                )
        try:
            todo = database.update_todo(todo_id, **changes)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="todo_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await publish_todos(todo["todo_date"])
        return todo

    @app.delete("/api/v1/todos/{todo_id}", dependencies=[Depends(require_token)])
    async def delete_todo(todo_id: str) -> dict[str, Any]:
        try:
            todo = database.delete_todo(todo_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="todo_not_found") from exc
        await publish_todos(todo["todo_date"])
        return {"id": todo_id, "deleted": True}

    @app.get("/api/v1/weekly-plans", dependencies=[Depends(require_token)])
    async def get_weekly_plans() -> dict[str, Any]:
        return database.list_weekly_plans()

    @app.put("/api/v1/weekly-plans", dependencies=[Depends(require_token)])
    async def replace_weekly_plans(
        payload: WeeklyPlanBatchBody,
    ) -> dict[str, Any]:
        items = [item.model_dump(exclude_none=True) for item in payload.items]
        database.replace_weekly_plans(items)
        return await publish_weekly_plans()

    @app.post("/api/v1/weekly-plans", dependencies=[Depends(require_token)])
    async def create_weekly_plan(payload: WeeklyPlanBody) -> dict[str, Any]:
        plan = database.create_weekly_plan(
            plan_id=payload.id or f"wp_{secrets.token_hex(8)}",
            weekday=payload.weekday,
            exercise=payload.exercise,
            view=payload.view,
            sets=payload.sets,
            target_reps=payload.target_reps,
            note=payload.note,
            sort_order=payload.sort_order,
        )
        await publish_weekly_plans()
        return plan

    @app.patch(
        "/api/v1/weekly-plans/{plan_id}",
        dependencies=[Depends(require_token)],
    )
    async def update_weekly_plan(
        plan_id: str,
        payload: WeeklyPlanBody,
    ) -> dict[str, Any]:
        updates = payload.model_dump(exclude={"id", "sort_order"})
        if payload.sort_order is not None:
            updates["sort_order"] = payload.sort_order
        try:
            plan = database.update_weekly_plan(plan_id, **updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="weekly_plan_not_found") from exc
        await publish_weekly_plans()
        return plan

    @app.delete(
        "/api/v1/weekly-plans/{plan_id}",
        dependencies=[Depends(require_token)],
    )
    async def delete_weekly_plan(plan_id: str) -> dict[str, Any]:
        try:
            database.delete_weekly_plan(plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="weekly_plan_not_found") from exc
        await publish_weekly_plans()
        return {"id": plan_id, "deleted": True}

    @app.post("/api/v1/control/focus", dependencies=[Depends(require_token)])
    async def update_mirror_focus(payload: MirrorFocusBody) -> dict[str, Any]:
        await hub.publish(
            "mirror",
            {
                "type": "mirror.page",
                "version": "v1",
                "payload": {"page": payload.page},
            },
        )
        return {"page": payload.page, "delivered": True}

    @app.post("/api/v1/control/theme", dependencies=[Depends(require_token)])
    async def update_mirror_theme(payload: MirrorThemeBody) -> dict[str, Any]:
        database.set_setting("mirror.theme", payload.mode)
        await hub.publish(
            "mirror",
            {
                "type": "mirror.theme",
                "version": "v1",
                "payload": {"mode": payload.mode},
            },
        )
        return {"mode": payload.mode, "delivered": True}

    @app.get("/api/v1/pairing/devices", dependencies=[Depends(require_token)])
    async def list_pairing_devices() -> dict[str, Any]:
        return {"items": database.list_paired_devices()}

    @app.get("/api/v1/pairing/me", dependencies=[Depends(require_token)])
    async def current_pairing_device(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        token = (authorization or "")[7:].strip()
        device = database.get_device_for_token(token)
        if device is None:
            raise HTTPException(status_code=401, detail="invalid_pairing_token")
        return device

    @app.delete("/api/v1/pairing/current", dependencies=[Depends(require_token)])
    async def revoke_current_pairing(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, bool]:
        token = (authorization or "")[7:].strip()
        revoked = database.revoke_token(token)
        if revoked:
            await disconnect_token_clients(token)
        return {"revoked": revoked}

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

        purge_session(session_id, record)
        await publish_session_deleted(session_id)
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

    @app.get("/dev/v1/weather")
    async def dev_weather(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return await weather_snapshot()

    @app.get("/dev/v1/trend")
    async def dev_trend(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return {"days": database.daily_trend(7)}

    @app.get("/dev/v1/todos")
    async def dev_todos(request: Request, day: date | None = Query(default=None)) -> dict[str, Any]:
        require_loopback(request)
        return database.list_todos(day.isoformat() if day else None)

    @app.get("/dev/v1/weekly-plans")
    async def dev_weekly_plans(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return database.list_weekly_plans()

    @app.get("/dev/v1/layout")
    async def dev_layout(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return current_mirror_layout()

    @app.get("/dev/v1/pairing/status")
    async def dev_pairing_status(request: Request) -> dict[str, Any]:
        require_loopback(request)
        return {
            "connected": client_connections["count"] > 0,
            "pairing_code": settings.pairing_code,
        }

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
        if channel == "mirror":
            await websocket.send_json(
                {
                    "type": "pairing.state",
                    "version": "v1",
                    "payload": {
                        "connected": client_connections["count"] > 0,
                    },
                }
            )
            await websocket.send_json(
                {
                    "type": "mirror.theme",
                    "version": "v1",
                    "payload": current_mirror_theme(),
                }
            )
            await websocket.send_json(
                {
                    "type": "mirror.layout",
                    "version": "v1",
                    "payload": current_mirror_layout(),
                }
            )
            await websocket.send_json(
                {
                    "type": "todo.updated",
                    "version": "v1",
                    "payload": database.list_todos(),
                }
            )
            await websocket.send_json(
                {
                    "type": "weekly_plan.updated",
                    "version": "v1",
                    "payload": database.list_weekly_plans(),
                }
            )
        else:
            if not token or not database.token_is_valid(token):
                await websocket.close(code=4401)
                return
            token_connections = client_sockets.setdefault(token, set())
            token_connections.add(websocket)
            client_connections["count"] += 1
            if client_connections["count"] == 1:
                await publish_pairing_state()
        try:
            async for event in hub.subscribe(channel):
                await websocket.send_json(event)
        except WebSocketDisconnect:
            return
        finally:
            if channel == "client":
                token_connections = client_sockets.get(token or "")
                if (
                    token_connections is not None
                    and websocket in token_connections
                ):
                    token_connections.remove(websocket)
                    if not token_connections:
                        client_sockets.pop(token or "", None)
                    client_connections["count"] = max(
                        0,
                        client_connections["count"] - 1,
                    )
                    if client_connections["count"] == 0:
                        await publish_pairing_state()

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
