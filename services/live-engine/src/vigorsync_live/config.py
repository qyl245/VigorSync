"""Runtime configuration for the VigorSync Live Engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CAMERA_INDEX = 1


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    session_root: Path
    host: str
    port: int
    camera_index: int
    camera_width: int
    camera_height: int
    camera_fps: int
    camera_backend: str
    jpeg_quality: int
    pose_model_complexity: int
    min_visibility: float
    metric_hz: float
    internal_token: str | None
    enable_dev_api: bool
    allowed_origins: tuple[str, ...]
    inference_fps: float = 20.0
    preview_fps: float = 15.0
    core_url: str | None = None
    core_internal_token: str | None = None
    tts_enabled: bool = False
    tts_voice: str | None = None
    tts_rate: int = 170
    tts_volume: float = 1.0
    tts_queue_size: int = 4
    tts_min_confidence: float = 0.75
    tts_code_cooldown_ms: int = 5000
    tts_global_cooldown_ms: int = 3000

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(
            os.getenv("VIGORSYNC_PROJECT_ROOT", str(PROJECT_ROOT))
        ).resolve()
        session_root = Path(
            os.getenv(
                "VIGORSYNC_SESSION_ROOT",
                str(project_root / "webdata" / "sessions"),
            )
        ).resolve()
        origins = tuple(
            item.strip()
            for item in os.getenv(
                "VIGORSYNC_ALLOWED_ORIGINS",
                "http://127.0.0.1:8080,http://localhost:8080",
            ).split(",")
            if item.strip()
        )
        return cls(
            project_root=project_root,
            session_root=session_root,
            host=os.getenv("VIGORSYNC_LIVE_HOST", "127.0.0.1"),
            port=_env_int("VIGORSYNC_LIVE_PORT", 8020),
            camera_index=_env_int(
                "VIGORSYNC_CAMERA_INDEX",
                DEFAULT_CAMERA_INDEX,
            ),
            camera_width=_env_int("VIGORSYNC_CAMERA_WIDTH", 640),
            camera_height=_env_int("VIGORSYNC_CAMERA_HEIGHT", 480),
            camera_fps=_env_int("VIGORSYNC_CAMERA_FPS", 30),
            camera_backend=os.getenv(
                "VIGORSYNC_CAMERA_BACKEND",
                "dshow" if os.name == "nt" else "auto",
            ).lower(),
            jpeg_quality=max(
                30, min(95, _env_int("VIGORSYNC_JPEG_QUALITY", 80))
            ),
            pose_model_complexity=max(
                0,
                min(
                    2,
                    _env_int("VIGORSYNC_POSE_MODEL_COMPLEXITY", 0),
                ),
            ),
            min_visibility=max(
                0.1,
                min(
                    0.99,
                    float(os.getenv("VIGORSYNC_MIN_VISIBILITY", "0.5")),
                ),
            ),
            metric_hz=max(
                1.0,
                min(10.0, float(os.getenv("VIGORSYNC_METRIC_HZ", "5"))),
            ),
            internal_token=os.getenv("VIGORSYNC_INTERNAL_TOKEN") or None,
            enable_dev_api=_env_bool("VIGORSYNC_ENABLE_DEV_API", True),
            allowed_origins=origins,
            inference_fps=max(
                5.0,
                min(
                    60.0,
                    float(os.getenv("VIGORSYNC_INFERENCE_FPS", "20")),
                ),
            ),
            preview_fps=max(
                5.0,
                min(
                    30.0,
                    float(os.getenv("VIGORSYNC_PREVIEW_FPS", "15")),
                ),
            ),
            core_url=os.getenv("VIGORSYNC_CORE_URL") or None,
            core_internal_token=os.getenv("VIGORSYNC_CORE_INTERNAL_TOKEN") or None,
            tts_enabled=_env_bool(
                "VIGORSYNC_TTS_ENABLED",
                os.name == "nt",
            ),
            tts_voice=os.getenv("VIGORSYNC_TTS_VOICE") or None,
            tts_rate=max(
                80,
                min(320, _env_int("VIGORSYNC_TTS_RATE", 170)),
            ),
            tts_volume=max(
                0.0,
                min(
                    1.0,
                    float(os.getenv("VIGORSYNC_TTS_VOLUME", "1.0")),
                ),
            ),
            tts_queue_size=max(
                1,
                min(16, _env_int("VIGORSYNC_TTS_QUEUE_SIZE", 4)),
            ),
            tts_min_confidence=max(
                0.0,
                min(
                    1.0,
                    float(
                        os.getenv("VIGORSYNC_TTS_MIN_CONFIDENCE", "0.75")
                    ),
                ),
            ),
            tts_code_cooldown_ms=max(
                0,
                _env_int("VIGORSYNC_TTS_CODE_COOLDOWN_MS", 5000),
            ),
            tts_global_cooldown_ms=max(
                0,
                _env_int("VIGORSYNC_TTS_GLOBAL_COOLDOWN_MS", 3000),
            ),
        )

    def ensure_directories(self) -> None:
        self.session_root.mkdir(parents=True, exist_ok=True)
