"""Configuration for the VigorSync Core API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    db_path: Path
    session_root: Path
    host: str = "0.0.0.0"
    port: int = 8011
    live_url: str = "http://127.0.0.1:8020"
    live_internal_token: str | None = None
    offline_url: str = "http://127.0.0.1:8030"
    offline_internal_token: str = "vigorsync-offline-dev"
    offline_data_root: Path | None = None
    offline_analysis_enabled: bool = True
    mirror_token: str = "vigorsync-mirror-dev"
    pairing_code: str = "482913"
    weather_latitude: float = 31.2304
    weather_longitude: float = 121.4737
    discovery_enabled: bool = True
    discovery_port: int = 8765
    mirror_name: str = "VigorSync Mirror"

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(
            os.getenv("VIGORSYNC_PROJECT_ROOT", str(PROJECT_ROOT))
        ).resolve()
        webdata = Path(
            os.getenv("VIGORSYNC_WEBDATA_ROOT", str(project_root / "webdata"))
        ).resolve()
        return cls(
            project_root=project_root,
            db_path=Path(
                os.getenv("VIGORSYNC_CORE_DB", str(webdata / "core" / "core.db"))
            ).resolve(),
            session_root=Path(
                os.getenv(
                    "VIGORSYNC_SESSION_ROOT",
                    str(webdata / "sessions"),
                )
            ).resolve(),
            host=os.getenv("VIGORSYNC_CORE_HOST", "0.0.0.0"),
            port=int(os.getenv("VIGORSYNC_CORE_PORT", "8011")),
            live_url=os.getenv(
                "VIGORSYNC_LIVE_URL",
                "http://127.0.0.1:8020",
            ).rstrip("/"),
            live_internal_token=(
                os.getenv("VIGORSYNC_LIVE_INTERNAL_TOKEN") or None
            ),
            offline_url=os.getenv(
                "VIGORSYNC_OFFLINE_URL",
                "http://127.0.0.1:8030",
            ).rstrip("/"),
            offline_internal_token=os.getenv(
                "VIGORYSYNC_OFFLINE_INTERNAL_TOKEN",
                "vigorsync-offline-dev",
            ),
            offline_data_root=Path(
                os.getenv(
                    "VIGORYSYNC_OFFLINE_DATA_ROOT",
                    str(webdata / "offline"),
                )
            ).resolve(),
            offline_analysis_enabled=_env_bool(
                "VIGORYSYNC_OFFLINE_ANALYSIS_ENABLED",
                True,
            ),
            mirror_token=os.getenv(
                "VIGORSYNC_MIRROR_TOKEN",
                "vigorsync-mirror-dev",
            ),
            pairing_code=os.getenv("VIGORSYNC_PAIRING_CODE", "482913"),
            weather_latitude=float(
                os.getenv("VIGORSYNC_WEATHER_LATITUDE", "31.2304")
            ),
            weather_longitude=float(
                os.getenv("VIGORSYNC_WEATHER_LONGITUDE", "121.4737")
            ),
            discovery_enabled=os.getenv(
                "VIGORYSYNC_DISCOVERY_ENABLED",
                "1",
            ).strip().lower()
            not in {"0", "false", "no", "off"},
            discovery_port=int(os.getenv("VIGORYSYNC_DISCOVERY_PORT", "8765")),
            mirror_name=os.getenv("VIGORYSYNC_MIRROR_NAME", "VigorSync Mirror"),
        )

    def ensure_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_root.mkdir(parents=True, exist_ok=True)
        if self.offline_data_root is not None:
            self.offline_data_root.mkdir(parents=True, exist_ok=True)
