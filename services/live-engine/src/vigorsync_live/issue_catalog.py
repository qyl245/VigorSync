"""Shared issue metadata used by realtime detection and speech cues."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(
    os.getenv(
        "VIGORSYNC_RESOURCE_ROOT",
        str(Path(__file__).resolve().parents[4]),
    )
).resolve()
ISSUES_ROOT = PROJECT_ROOT / "configs" / "issues"
SCHEMA_PATH = ISSUES_ROOT / "schema.json"
CATALOG_VERSION = "vigorsync-issue-catalog-v1"


@dataclass(frozen=True, slots=True)
class IssueDefinition:
    code: str
    exercise: str
    family: str
    title: str
    short_cue: str
    correction: str
    detail: str
    default_severity: str
    live_joints: tuple[str, ...]
    offline_joints: tuple[str, ...]
    views: tuple[str, ...]
    live_supported: bool
    offline_supported: bool
    offline_metrics: tuple[str, ...]
    limitation: str


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def load_issue_catalog(
    project_root: str | None = None,
) -> Mapping[str, IssueDefinition]:
    root = Path(project_root).resolve() if project_root else PROJECT_ROOT
    issues_root = root / "configs" / "issues"
    schema = _read_json(issues_root / "schema.json")
    validator = Draft202012Validator(schema)
    catalog: dict[str, IssueDefinition] = {}
    for exercise in ("squat", "pushup"):
        path = issues_root / f"{exercise}.json"
        payload = _read_json(path)
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path)
            raise ValueError(
                f"{path.name}:{location}: {first.message}"
            )
        if payload["exercise"] != exercise:
            raise ValueError(
                f"{path.name}: exercise must be {exercise}"
            )
        for item in payload["issues"]:
            code = item["code"]
            if not code.startswith(f"{exercise}_"):
                raise ValueError(
                    f"{path.name}: code {code} does not match {exercise}"
                )
            if code in catalog:
                raise ValueError(f"duplicate issue code: {code}")
            catalog[code] = IssueDefinition(
                code=code,
                exercise=exercise,
                family=item["family"],
                title=item["title"],
                short_cue=item["short_cue"],
                correction=item["correction"],
                detail=item["detail"],
                default_severity=item["default_severity"],
                live_joints=tuple(item["live_joints"]),
                offline_joints=tuple(item["offline_joints"]),
                views=tuple(item["views"]),
                live_supported=item["live_supported"],
                offline_supported=item["offline_supported"],
                offline_metrics=tuple(item["offline_metrics"]),
                limitation=item["limitation"],
            )
    return MappingProxyType(catalog)


def catalog_for_exercise(
    exercise: str,
    project_root: str | None = None,
) -> Mapping[str, IssueDefinition]:
    return MappingProxyType(
        {
            code: issue
            for code, issue in load_issue_catalog(project_root).items()
            if issue.exercise == exercise
        }
    )


def issue_for(
    code: str,
    project_root: str | None = None,
) -> IssueDefinition:
    try:
        return load_issue_catalog(project_root)[code]
    except KeyError as exc:
        raise KeyError(f"unknown issue code: {code}") from exc


def live_speech_texts(
    project_root: str | None = None,
) -> tuple[str, ...]:
    texts = {
        issue.short_cue
        for issue in load_issue_catalog(project_root).values()
        if issue.live_supported
    }
    texts.update(("完成五次", "完成十次", "目标完成"))
    return tuple(sorted(texts))
