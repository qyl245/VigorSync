"""Attach realtime form alerts to an offline feedback report."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.repchain.feedback_builder import (
    build_motion_payload,
    build_report_html,
    build_report_timeline,
    build_trajectory_section,
)


REVIEW_SCHEMA_VERSION = "vigorsync-realtime-review-v1"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _catalog(project_root: Path) -> dict[str, dict[str, Any]]:
    issues: dict[str, dict[str, Any]] = {}
    for exercise in ("squat", "pushup"):
        path = project_root / "configs" / "issues" / f"{exercise}.json"
        payload = _read_json(path)
        for issue in payload.get("issues") or []:
            issues[str(issue["code"])] = issue
    return issues


def summarize_realtime_events(
    events_path: Path,
    offline_codes: set[str],
    project_root: Path,
) -> dict[str, Any]:
    if not events_path.is_file():
        return {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "state": "missing",
            "event_count": 0,
            "issues": [],
            "matched_codes": [],
            "live_only_codes": [],
            "offline_only_codes": sorted(offline_codes),
            "parse_errors": 0,
        }

    catalog = _catalog(project_root)
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "first_ms": None,
            "last_ms": None,
            "severity": "low",
            "message": "",
        }
    )
    parse_errors = 0
    event_count = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if event.get("type") != "form.alert":
            continue
        payload = event.get("payload") or {}
        code = str(payload.get("code") or "").strip()
        if not code:
            parse_errors += 1
            continue
        try:
            t_ms = max(0, int(event.get("t_ms") or 0))
        except (TypeError, ValueError):
            parse_errors += 1
            continue
        event_count += 1
        item = stats[code]
        item["count"] += 1
        item["first_ms"] = (
            t_ms
            if item["first_ms"] is None
            else min(item["first_ms"], t_ms)
        )
        item["last_ms"] = (
            t_ms
            if item["last_ms"] is None
            else max(item["last_ms"], t_ms)
        )
        item["severity"] = str(payload.get("severity") or item["severity"])
        item["message"] = str(
            payload.get("message") or item["message"]
        )

    issues = []
    for code, item in sorted(stats.items()):
        metadata = catalog.get(code) or {}
        issues.append(
            {
                "code": code,
                "title": str(metadata.get("title") or code),
                "short_cue": str(
                    metadata.get("short_cue") or item["message"]
                ),
                "count": item["count"],
                "first_s": round(float(item["first_ms"]) / 1000.0, 3),
                "last_s": round(float(item["last_ms"]) / 1000.0, 3),
                "severity": item["severity"],
                "matched_offline": code in offline_codes,
            }
        )
    realtime_codes = {issue["code"] for issue in issues}
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "state": "available" if event_count else "empty",
        "event_count": event_count,
        "issues": issues,
        "matched_codes": sorted(realtime_codes & offline_codes),
        "live_only_codes": sorted(realtime_codes - offline_codes),
        "offline_only_codes": sorted(offline_codes - realtime_codes),
        "parse_errors": parse_errors,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _refresh_complete_manifest(
    job_dir: Path,
    artifacts_dir: Path,
) -> None:
    complete_path = job_dir / "complete.json"
    if not complete_path.is_file():
        return
    existing = _read_json(complete_path)
    existing["artifacts"] = [
        {
            "name": path.name,
            "relative_path": str(path.relative_to(artifacts_dir)).replace(
                "\\",
                "/",
            ),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(artifacts_dir.rglob("*"))
        if path.is_file()
    ]
    _write_json(complete_path, existing)


def _sync_evidence_frame_references(
    feedback: dict[str, Any],
    artifacts_dir: Path,
) -> None:
    frame_dir = artifacts_dir / "annotated_frames"
    for card in feedback.get("cards") or []:
        issue_id = str(card.get("issue_id") or "")
        filename = str(card.get("frame_filename") or "")
        if filename and (frame_dir / filename).is_file():
            card["frame_available"] = True
            continue

        anchor_frame = card.get("anchor_frame")
        expected = (
            f"{issue_id}_frame_{int(anchor_frame)}.png"
            if anchor_frame is not None
            else ""
        )
        if expected and (frame_dir / expected).is_file():
            card["frame_filename"] = expected
            card["frame_available"] = True
            continue

        prefix = f"{issue_id}_frame_"
        candidates = (
            sorted(
                path.name
                for path in frame_dir.iterdir()
                if path.is_file() and path.name.startswith(prefix)
            )
            if frame_dir.is_dir()
            else []
        )
        if len(candidates) == 1:
            card["frame_filename"] = candidates[0]
            card["frame_available"] = True
        else:
            card["frame_filename"] = None
            card["frame_available"] = False


def enrich_job_realtime_review(
    job_dir: Path,
    project_root: Path,
) -> dict[str, Any] | None:
    job_id = job_dir.name
    artifacts_dir = job_dir / "artifacts" / job_id
    feedback_path = artifacts_dir / "feedback_v2.json"
    assessment_path = artifacts_dir / "assessment_v2.json"
    if not feedback_path.is_file() or not assessment_path.is_file():
        return None

    feedback = _read_json(feedback_path)
    offline_codes = {
        str(card.get("issue_code"))
        for card in feedback.get("cards") or []
        if card.get("issue_code")
    }
    review = summarize_realtime_events(
        job_dir / "input" / "realtime-events.jsonl",
        offline_codes,
        project_root,
    )
    feedback["realtime_review"] = review
    _sync_evidence_frame_references(feedback, artifacts_dir)
    _write_json(feedback_path, feedback)
    _write_json(artifacts_dir / "realtime_review.json", review)

    assessment = _read_json(assessment_path)
    timeline = build_report_timeline(assessment)
    analysis_path = artifacts_dir / "analysis.json"
    analysis = _read_json(analysis_path) if analysis_path.is_file() else {}
    try:
        trajectory_html = build_trajectory_section(
            artifacts_dir,
            artifacts_dir.parent / "trajectory",
            job_id,
        )
    except Exception:
        trajectory_html = ""
    motion = None
    try:
        if not analysis:
            raise FileNotFoundError("analysis.json is unavailable")
        motion = build_motion_payload(
            job_dir / "work" / "analysis",
            analysis,
            timeline,
        )
    except Exception:
        motion = None
    if motion is not None:
        _write_json(artifacts_dir / "motion_payload.json", motion)
    report_html = build_report_html(
        feedback,
        media_url=f"/api/v1/jobs/{job_id}/media",
        frame_url_template=f"/api/v1/jobs/{job_id}/frames/{{frame_name}}",
        timeline=timeline,
        motion=motion,
        trajectory_html=trajectory_html,
    )
    (artifacts_dir / "report_v2.html").write_text(
        report_html,
        encoding="utf-8",
    )
    _refresh_complete_manifest(job_dir, artifacts_dir)
    return review
