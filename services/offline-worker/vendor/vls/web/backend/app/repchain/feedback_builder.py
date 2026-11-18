#!/usr/bin/env python3
"""Build evidence-backed feedback from assessment results.

The script deliberately keeps assessment, feedback ordering and rendering as
separate contracts. It can consume an existing assessment directory, or adapt
an arbitrary clip-analysis/RepChain directory at runtime without claiming a
benchmark split.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from jsonschema import Draft202012Validator

import assessment_measurement as measurement_stage
import assessment_decision as decision_stage
from motion_metrics import build_process_metrics


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ANALYSIS_DIR = ROOT / "results" / "clip_analysis_v1"
DEFAULT_OUTPUT_DIR = ROOT / "results" / "feedback_v2"
DEFAULT_TEMPLATES = ROOT / "configs" / "feedback_templates_v2.json"
DEFAULT_RULES = ROOT / "configs" / "assessment_rules.json"
DEFAULT_TAXONOMY = ROOT / "configs" / "issue_taxonomy.json"
DEFAULT_CONTRACTS = ROOT / "configs" / "metric_contracts.json"
DEFAULT_SCHEMA = ROOT / "schemas" / "feedback_v2.schema.json"

ACTIVE_DECISIONS = {"candidate", "reportable"}
SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
SEVERITY_LABEL = {"low": "低", "medium": "中", "high": "高"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument(
        "--assessment-dir",
        type=Path,
        default=None,
        help="Optional existing assessment root; rebuild when omitted.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--clip-id", nargs="+", default=None)
    parser.add_argument(
        "--split",
        default="all",
        help="Filter analysis split; use 'all' for the Web runtime adapter.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--generated-at", default=None)
    parser.add_argument(
        "--render-frames",
        action="store_true",
        help="Render annotated evidence frames into <output>/<clip>/frames/.",
    )
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "pose_landmarker_full.task")
    parser.add_argument(
        "--media-url-template",
        default="/api/v1/samples/{clip_id}/media",
        help="Standalone report video URL; supports {clip_id}.",
    )
    parser.add_argument(
        "--frame-url-template",
        default="/api/v1/samples/{clip_id}/frames/{frame_name}",
        help="Standalone report evidence-frame URL; supports {clip_id}, {frame_name}.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def rounded(value: float | None, digits: int = 6) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(value) else None


def severity_rank(value: str | None) -> int:
    return SEVERITY_RANK.get(str(value or "none"), 0)


def select_clip_ids(args: argparse.Namespace, analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = set(args.clip_id or [])
    selected = []
    for analysis in analyses:
        clip_id = str(analysis.get("clip_id") or "")
        if not clip_id or (wanted and clip_id not in wanted):
            continue
        split = str(analysis.get("split") or "unknown")
        if args.split != "all" and split != args.split:
            continue
        selected.append(analysis)
    selected.sort(key=lambda item: str(item["clip_id"]))
    if args.limit is not None:
        selected = selected[: max(0, args.limit)]
    if wanted:
        missing = wanted - {str(item["clip_id"]) for item in selected}
        if missing:
            raise FileNotFoundError("analysis missing for: " + ", ".join(sorted(missing)))
    return selected


def discover_analyses(root: Path) -> list[dict[str, Any]]:
    output = []
    for path in sorted(root.glob("*/analysis.json")):
        payload = load_json(path)
        payload["_analysis_dir"] = str(path.parent)
        output.append(payload)
    return output


def subject_id_for(clip_id: str, clip_meta: dict[str, Any], analysis: dict[str, Any]) -> str:
    explicit = clip_meta.get("subject_id") or analysis.get("subject_id")
    if explicit:
        return str(explicit)
    match = re.search(r"subject[_-](\d+)", clip_id, flags=re.IGNORECASE)
    if match:
        return f"subject_{match.group(1)}"
    return f"runtime_{clip_id}"


def runtime_clip(
    analysis: dict[str, Any], clip_meta: dict[str, Any]
) -> dict[str, Any]:
    clip_id = str(analysis["clip_id"])
    source_path = clip_meta.get("video_path") or clip_meta.get("source_path")
    source = Path(str(source_path)).expanduser() if source_path else None
    return {
        "clip_id": clip_id,
        "subject_id": subject_id_for(clip_id, clip_meta, analysis),
        "exercise": str(analysis["exercise"]),
        "view": str(analysis.get("view") or "unknown"),
        "split": str(analysis.get("split") or "dev"),
        "fps": as_float(analysis.get("fps")) or as_float(clip_meta.get("fps")) or 30.0,
        "source_path": str(source) if source else None,
        "sha256": sha256_file(source) if source else None,
        "runtime_adapter": True,
    }


def write_runtime_benchmark(
    output_dir: Path, clips: list[dict[str, Any]], generated_at: str
) -> Path:
    path = output_dir / "_runtime_benchmark.json"
    write_json(
        path,
        {
            "schema_version": "vls-runtime-benchmark-v1",
            "component": "runtime_benchmark_adapter",
            "generated_at": generated_at,
            "status": "runtime_adapter_not_a_benchmark_split",
            "clips": clips,
        },
    )
    return path


def build_runtime_assessment(
    clip: dict[str, Any],
    analysis_dir: Path,
    contracts: dict[str, Any],
    rules: dict[str, Any],
    taxonomy: dict[str, Any],
    benchmark_path: Path,
    contracts_path: Path,
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    data = measurement_stage.load_clip_data(clip, analysis_dir)
    measurement = measurement_stage.build_assessment(
        clip,
        data,
        contracts,
        benchmark_path,
        contracts_path,
        generated_at,
    )
    assessment = decision_stage.build_assessment(
        measurement,
        clip,
        rules,
        taxonomy,
        generated_at,
    )
    return measurement, assessment


def load_existing_assessment(assessment_dir: Path, clip_id: str) -> dict[str, Any] | None:
    path = assessment_dir / clip_id / "assessment.json"
    return load_json(path) if path.is_file() else None


def support_evaluations(issue: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in issue.get("support_metric_evaluations") or []
        if item.get("decision") in ACTIVE_DECISIONS
    ]


def choose_primary_evaluation(issue: dict[str, Any], templates: dict[str, Any]) -> dict[str, Any] | None:
    evaluations = support_evaluations(issue)
    if not evaluations:
        return None
    representative = issue.get("representative_measurement_id")
    if templates.get("evidence", {}).get("prefer_representative_measurement", True):
        for item in evaluations:
            if item.get("measurement_id") == representative:
                return item
    return max(
        evaluations,
        key=lambda item: (
            severity_rank(item.get("magnitude_severity")),
            as_float(item.get("deviation")) or 0.0,
            as_float(item.get("adjusted_confidence")) or 0.0,
        ),
    )


def choose_rep(issue: dict[str, Any], reps: list[dict[str, Any]], anchor: int | None) -> dict[str, Any] | None:
    by_id = {as_int(rep.get("rep_id")): rep for rep in reps if as_int(rep.get("rep_id")) is not None}
    if anchor is not None:
        for rep in reps:
            if int(rep["start_frame"]) <= anchor <= int(rep["end_frame"]):
                return rep
    representative = as_int(issue.get("representative_rep_id") or issue.get("rep_id"))
    if representative in by_id:
        return by_id[representative]
    for rep_id in issue.get("support_rep_ids") or []:
        rep = by_id.get(as_int(rep_id))
        if rep is not None:
            return rep
    if anchor is not None and reps:
        return min(
            reps,
            key=lambda rep: min(
                abs(int(rep["start_frame"]) - anchor),
                abs(int(rep["end_frame"]) - anchor),
            ),
        )
    return reps[0] if reps else None


def select_evidence(
    issue: dict[str, Any],
    reps: list[dict[str, Any]],
    fps: float,
    frame_count: int,
    trim_start: int,
    templates: dict[str, Any],
) -> dict[str, Any]:
    radius = int(templates.get("evidence", {}).get("radius_frames", 8))
    max_distance = int(templates.get("evidence", {}).get("max_fallback_distance_frames", 45))
    anchor = as_int(issue.get("anchor_frame"))
    rep = choose_rep(issue, reps, anchor)
    fallback = False
    lower = 0
    upper = max(0, frame_count - 1)
    preferred_anchor: int | None = None
    if rep is not None:
        lower = int(rep.get("start_frame") or 0)
        upper = int(rep.get("end_frame") or upper)
        preferred_anchor = as_int(rep.get("bottom_frame")) or lower
    if anchor is None or not (lower <= anchor <= upper):
        if preferred_anchor is not None and abs((anchor or 0) - preferred_anchor) > max_distance:
            fallback = True
        anchor = preferred_anchor if preferred_anchor is not None else max(lower, min(upper, anchor or lower))
        fallback = True
    anchor = max(lower, min(upper, int(anchor)))

    interval = issue.get("evidence_interval") or []
    start = as_int(interval[0]) if len(interval) >= 2 else None
    end = as_int(interval[1]) if len(interval) >= 2 else None
    if start is None or end is None or end < start:
        start, end = anchor - radius, anchor + radius
        fallback = True
    start = max(lower, min(upper, int(start)))
    end = max(start, min(upper, int(end)))
    if not (start <= anchor <= end):
        start = max(lower, anchor - radius)
        end = min(upper, anchor + radius)
        fallback = fallback or not (start <= anchor <= end)
        if not (start <= anchor <= end):
            anchor = min(max(anchor, start), end)

    source_anchor = issue.get("anchor_source_frame")
    if not fallback and source_anchor is not None:
        anchor_source = int(source_anchor)
    else:
        anchor_source = trim_start + anchor
    return {
        "anchor_frame": int(anchor),
        "anchor_source_frame": int(anchor_source),
        "anchor_time_s": round(anchor / fps, 6) if fps > 0 else 0.0,
        "evidence_interval_frames": [int(start), int(end)],
        "evidence_interval_s": [
            round(start / fps, 6) if fps > 0 else 0.0,
            round(end / fps, 6) if fps > 0 else 0.0,
        ],
        "anchor_fallback": bool(fallback),
        "representative_rep_id": as_int(rep.get("rep_id")) if rep else None,
    }


def format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "-"
    if unit == "deg":
        return f"{value:.1f}°"
    if unit == "ratio":
        return f"{value:.2f}"
    if unit == "variance":
        return f"{value:.3f}"
    if unit == "s":
        return f"{value:.2f}s"
    return f"{value:.2f}{unit or ''}"


def rep_phrase(issue: dict[str, Any], representative: int | None) -> str:
    rep_ids = [as_int(item) for item in issue.get("support_rep_ids") or [] if as_int(item) is not None]
    if len(rep_ids) == 1:
        return f"rep {rep_ids[0]} "
    if len(rep_ids) > 1:
        return f"rep {rep_ids[0]}–{rep_ids[-1]} "
    if representative is not None:
        return f"rep {representative} "
    return "本组 "


def card_frequency(issue: dict[str, Any], rep_count: int, templates: dict[str, Any]) -> tuple[str, float]:
    priority_cfg = templates.get("priority", {})
    if issue.get("scope") == "clip":
        ratio = float(priority_cfg.get("clip_scope_frequency_ratio", 1.0))
        return "整组", max(0.0, min(1.0, ratio))
    support_count = int(issue.get("support_rep_count") or len(issue.get("support_rep_ids") or []))
    if rep_count <= 0:
        return f"{support_count} rep", 0.0
    ratio = max(0.0, min(1.0, support_count / rep_count))
    ratio = max(float(priority_cfg.get("min_frequency_ratio", 0.0)), ratio)
    return f"{support_count}/{rep_count} reps", ratio


def build_card(
    issue: dict[str, Any],
    assessment: dict[str, Any],
    templates: dict[str, Any],
    trim_start: int,
) -> dict[str, Any]:
    primary = choose_primary_evaluation(issue, templates)
    if primary is None:
        raise ValueError(f"active issue has no product-eligible support: {issue.get('issue_id')}")
    evidence = select_evidence(
        issue,
        assessment.get("reps") or [],
        float(assessment.get("source_measurement", {}).get("input_contract", {}).get("fps") or 30.0),
        int(assessment.get("source_measurement", {}).get("input_contract", {}).get("frame_count") or 0),
        trim_start,
        templates,
    )
    template = (templates.get("issues") or {}).get(issue["issue_code"])
    if not template:
        raise ValueError(f"missing feedback template: {issue['issue_code']}")
    severity = str(issue["severity"])
    confidence = max(0.0, min(1.0, float(issue.get("confidence") or 0.0)))
    frequency_text, frequency_ratio = card_frequency(issue, int(assessment["summary"]["rep_count"]), templates)
    priority_cfg = templates.get("priority", {})
    priority_components = {
        "severity": float(priority_cfg.get("severity_weights", {}).get(severity, 1.0)),
        "frequency": round(frequency_ratio, 6),
        "confidence": round(confidence, 6),
        "actionability": float(
            priority_cfg.get("decision_weights", {}).get(issue.get("decision"), 0.0)
        ),
    }
    priority = (
        priority_components["severity"]
        * priority_components["frequency"]
        * priority_components["confidence"]
        * priority_components["actionability"]
    )
    metric = str(primary["metric"])
    unit = primary.get("unit")
    value = as_float(primary.get("raw_value"))
    detail = template["detail_template"].format(
        rep_phrase=rep_phrase(issue, evidence["representative_rep_id"]),
        metric_label=(templates.get("metric_labels") or {}).get(metric, metric),
        value_text=format_value(value, unit),
        severity_label=SEVERITY_LABEL.get(severity, severity),
    )
    threshold = primary.get("threshold") or {}
    thresholds = {
        key: threshold.get(key)
        for key in ("low", "medium", "high", "direction")
        if threshold.get(key) is not None
    }
    return {
        "issue_id": str(issue["issue_id"]),
        "issue_code": str(issue["issue_code"]),
        "issue_family": str(issue["issue_family"]),
        "decision": str(issue["decision"]),
        "severity": severity,
        "confidence": round(confidence, 6),
        "priority": round(priority, 6),
        "priority_components": priority_components,
        "title": template["title"],
        "detail": detail,
        "cue": template["cue"],
        "limitation": template["limitation"],
        "support_rep_ids": [int(item) for item in issue.get("support_rep_ids") or []],
        "representative_rep_id": evidence["representative_rep_id"],
        "support_measurement_ids": list(issue.get("support_measurement_ids") or []),
        "frequency": frequency_text,
        "frequency_ratio": round(frequency_ratio, 6),
        "primary_metric": metric,
        "primary_value": rounded(value),
        "unit": unit,
        "thresholds": thresholds,
        "highlight_joints": list(template.get("highlight_joints") or []),
        "anchor_frame": evidence["anchor_frame"],
        "anchor_source_frame": evidence["anchor_source_frame"],
        "anchor_time_s": evidence["anchor_time_s"],
        "evidence_interval_frames": evidence["evidence_interval_frames"],
        "evidence_interval_s": evidence["evidence_interval_s"],
        "anchor_fallback": evidence["anchor_fallback"],
        "frame_filename": None,
        "frame_available": False,
        "shadow_only": True,
        "product_actions_enabled": False,
    }


def prioritize_issues(
    assessment: dict[str, Any], templates: dict[str, Any], trim_start: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = [
        issue
        for issue in assessment.get("issues") or []
        if issue.get("decision") in ACTIVE_DECISIONS
    ]
    cards = [build_card(issue, assessment, templates, trim_start) for issue in active]
    cards.sort(
        key=lambda card: (
            -float(card["priority"]),
            -severity_rank(card["severity"]),
            card["issue_id"],
        )
    )
    selected = []
    omitted = []
    families = set()
    max_one = bool(templates.get("priority", {}).get("max_one_per_family", True))
    top_k = int(templates.get("priority", {}).get("top_k", 3))
    for card in cards:
        if len(selected) >= top_k:
            omitted.append({**card, "omission_reason": "top_k"})
            continue
        if max_one and card["issue_family"] in families:
            omitted.append({**card, "omission_reason": "family_duplicate"})
            continue
        selected.append(card)
        families.add(card["issue_family"])
    return selected, omitted


def feedback_state(assessment: dict[str, Any], cards: list[dict[str, Any]]) -> str:
    summary = assessment.get("summary") or {}
    quality = assessment.get("quality") or {}
    if quality.get("status") == "reject" or summary.get("abstain_reason") == "quality_reject":
        return "quality_reject"
    if int(summary.get("rep_count") or 0) == 0:
        return "no_reps"
    if cards:
        return "active_issues"
    if int(summary.get("evaluable_metric_count") or 0) == 0:
        return "low_confidence"
    if int(summary.get("diagnostic_issue_count") or 0) > 0:
        return "low_confidence"
    if summary.get("abstained"):
        return "low_confidence"
    return "no_reliable_issue"


def build_limitations(
    assessment: dict[str, Any], omitted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output = []
    summary = assessment.get("summary") or {}
    diagnostic = int(summary.get("diagnostic_issue_count") or 0)
    if diagnostic:
        output.append(
            {
                "code": "diagnostic_only",
                "message": f"{diagnostic} 个诊断项因视角、指标可用性或置信度不足未进入主卡。",
                "count": diagnostic,
            }
        )
    suppressed = int(summary.get("suppressed_issue_count") or 0)
    if suppressed:
        output.append(
            {
                "code": "suppressed",
                "message": f"{suppressed} 个问题组被质量、置信度或物理范围门抑制。",
                "count": suppressed,
            }
        )
    omitted_by_reason = Counter(item["omission_reason"] for item in omitted)
    if omitted_by_reason.get("family_duplicate"):
        count = omitted_by_reason["family_duplicate"]
        output.append(
            {
                "code": "family_deduplicated",
                "message": f"{count} 个同族候选已合并，仅保留排序最高的一张卡。",
                "count": count,
            }
        )
    if omitted_by_reason.get("top_k"):
        count = omitted_by_reason["top_k"]
        output.append(
            {
                "code": "top_k",
                "message": f"{count} 个低优先级候选未进入默认 top-k 反馈。",
                "count": count,
            }
        )
    if (assessment.get("quality") or {}).get("status") == "warn":
        output.append(
            {
                "code": "quality_warn",
                "message": "输入质量存在警告，问题严重度已按规则降级或封顶。",
                "count": 1,
            }
        )
    return output


def build_feedback(
    assessment: dict[str, Any],
    templates: dict[str, Any],
    *,
    runtime_adapter: bool,
    benchmark_path: str | None = None,
    benchmark_sha256: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    input_contract = assessment.get("source_measurement", {}).get("input_contract") or {}
    trim_start = int((input_contract.get("trim") or {}).get("start_frame") or 0)
    cards, omitted = prioritize_issues(assessment, templates, trim_start)
    state = feedback_state(assessment, cards)
    if state in {"quality_reject", "no_reps"}:
        cards = []
    summary = assessment.get("summary") or {}
    return {
        "schema_version": "vls-feedback-v2",
        "component": "feedback_builder",
        "generated_at": generated_at or utc_now(),
        "clip_id": str(assessment["clip_id"]),
        "exercise": str(assessment["exercise"]),
        "view": str(assessment["view"]),
        "state": state,
        "state_message": str(templates["states"][state]),
        "runtime_contract": {
            "runtime_adapter": bool(runtime_adapter),
            "source_assessment_schema": str(assessment.get("schema_version")),
            "source_split": str(assessment.get("split")),
            "benchmark_path": benchmark_path,
            "benchmark_sha256": benchmark_sha256,
        },
        "assessment_summary": {
            "conclusion": str(summary.get("conclusion")),
            "quality_status": str((assessment.get("quality") or {}).get("status") or "unknown"),
            "rep_count": int(summary.get("rep_count") or 0),
            "evaluable_metric_count": int(summary.get("evaluable_metric_count") or 0),
            "candidate_issue_count": int(summary.get("candidate_issue_count") or 0),
            "reportable_issue_count": int(summary.get("reportable_issue_count") or 0),
            "diagnostic_issue_count": int(summary.get("diagnostic_issue_count") or 0),
            "suppressed_issue_count": int(summary.get("suppressed_issue_count") or 0),
            "abstained": bool(summary.get("abstained")),
            "abstain_reason": summary.get("abstain_reason"),
            "shadow_only": True,
            "product_actions_enabled": False,
        },
        "cards": cards,
        "limitations": build_limitations(assessment, omitted),
        "policy": {
            "top_k": int(templates.get("priority", {}).get("top_k", 3)),
            "max_one_per_family": bool(
                templates.get("priority", {}).get("max_one_per_family", True)
            ),
            "shadow_only": True,
            "product_actions_enabled": False,
            "expert_calibrated": False,
        },
    }


def validate_schema(payload: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    ]


def invariant_checks(feedback: dict[str, Any], assessment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cards = feedback.get("cards") or []
    if feedback["state"] == "quality_reject" and cards:
        errors.append("quality reject must not emit cards")
    if feedback["state"] == "active_issues" and not cards:
        errors.append("active state requires at least one card")
    if len(cards) > int(feedback["policy"]["top_k"]):
        errors.append("top-k exceeded")
    families = Counter(card["issue_family"] for card in cards)
    if feedback["policy"]["max_one_per_family"] and any(count > 1 for count in families.values()):
        errors.append("multiple cards in one family")
    reps = {
        as_int(rep.get("rep_id")): rep
        for rep in assessment.get("reps") or []
        if as_int(rep.get("rep_id")) is not None
    }
    product_measurements = {
        item.get("measurement_id")
        for item in assessment.get("metric_evaluations") or []
        if item.get("eligibility") == "provisional" and item.get("report_view_allowed")
    }
    for card in cards:
        if card.get("decision") not in ACTIVE_DECISIONS:
            errors.append(f"non-active decision in cards: {card['issue_id']}")
        start, end = card.get("evidence_interval_frames") or [None, None]
        anchor = card.get("anchor_frame")
        if start is None or end is None or anchor is None or not (int(start) <= int(anchor) <= int(end)):
            errors.append(f"anchor outside evidence: {card['issue_id']}")
        if severity_rank(card.get("severity")) >= severity_rank("medium") and start is None:
            errors.append(f"medium/high lacks evidence: {card['issue_id']}")
        rep_id = card.get("representative_rep_id")
        rep = reps.get(rep_id)
        if rep is not None and start is not None and end is not None:
            if int(start) < int(rep["start_frame"]) or int(end) > int(rep["end_frame"]):
                errors.append(f"evidence crosses representative rep: {card['issue_id']}")
        if not set(card.get("support_measurement_ids") or []).intersection(product_measurements):
            errors.append(f"card lacks product-eligible support: {card['issue_id']}")
        if not (0.0 <= float(card.get("confidence") or 0.0) <= 1.0):
            errors.append(f"invalid card confidence: {card['issue_id']}")
    if feedback["policy"]["product_actions_enabled"]:
        errors.append("product actions must remain disabled")
    return errors


def frame_url(template: str, clip_id: str, frame_name: str | None) -> str | None:
    if not frame_name:
        return None
    return template.format(clip_id=clip_id, frame_name=frame_name)


def build_report_timeline(assessment: dict[str, Any]) -> dict[str, Any]:
    input_contract = (
        assessment.get("source_measurement", {}).get("input_contract") or {}
    )
    fps = as_float(input_contract.get("fps")) or 30.0
    frame_count = as_int(input_contract.get("frame_count")) or 0
    reps = []
    for rep in assessment.get("reps") or []:
        rep_id = as_int(rep.get("rep_id"))
        start_frame = as_int(rep.get("start_frame"))
        end_frame = as_int(rep.get("end_frame"))
        bottom_frame = as_int(rep.get("bottom_frame"))
        if rep_id is None or start_frame is None or end_frame is None or end_frame < start_frame:
            continue
        if bottom_frame is None:
            bottom_frame = start_frame + (end_frame - start_frame) / 2
        reps.append(
            {
                "rep_id": rep_id,
                "start_s": round(start_frame / fps, 6),
                "end_s": round(end_frame / fps, 6),
                "bottom_s": round(bottom_frame / fps, 6),
                "confidence": rounded(as_float(rep.get("boundary_confidence"))),
                "status": str(rep.get("status") or "unknown"),
            }
        )
    inferred_duration = max((rep["end_s"] for rep in reps), default=0.0)
    duration_s = frame_count / fps if fps > 0 and frame_count > 0 else inferred_duration
    return {
        "duration_s": round(max(duration_s, inferred_duration), 6),
        "reps": reps,
    }



MOTION_JOINT_IDS = (0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)
MOTION_BONES = (
    (0, 2),
    (2, 7),
    (0, 5),
    (5, 8),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (25, 27),
    (24, 26),
    (26, 28),
    (27, 29),
    (29, 31),
    (28, 30),
    (30, 32),
)
MOTION_JOINT_NAME_TO_INDEX = {
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}


def motion_joint_indices(names: Iterable[Any]) -> list[int]:
    indices = {
        MOTION_JOINT_NAME_TO_INDEX[str(name)]
        for name in names
        if str(name) in MOTION_JOINT_NAME_TO_INDEX
    }
    return sorted(indices)


MOTION_VIDEO_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8),
    (9, 10), (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21),
    (17, 19), (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (28, 30), (29, 31), (30, 32), (27, 31), (28, 32),
)
MOTION_RIGID_BONES = (
    (11, 13), (13, 15),
    (12, 14), (14, 16),
    (23, 25), (25, 27), (27, 29), (29, 31),
    (24, 26), (26, 28), (28, 30), (30, 32),
)
MOTION_SMOOTH_WINDOW = 7
MOTION_MAX_INTERPOLATION_GAP = 5
MOTION_MAX_PREDICTION_GAP = 15
MOTION_VISIBLE_MIN = 0.35
MOTION_HIGH_CONFIDENCE = 0.65
MOTION_FOOT_INDICES = (27, 28, 29, 30, 31, 32)
MOTION_HAND_INDICES = (15, 16, 17, 18, 19, 20, 21, 22)
MOTION_ANKLE_INDICES = (27, 28)
MOTION_BONE_CORRECTION_MAX_M = 0.03
MOTION_ANCHOR_MAX_STEP_M = 0.02


def _unit_vector(vector: np.ndarray) -> np.ndarray | None:
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-8:
        return None
    return vector / norm


def _smooth_landmarks(
    points: np.ndarray,
    visibility: np.ndarray,
    window: int = MOTION_SMOOTH_WINDOW,
) -> tuple[np.ndarray, np.ndarray]:
    """Fill short occlusions, predict medium gaps, and preserve long gaps."""
    from scipy.interpolate import PchipInterpolator
    from scipy.signal import savgol_filter

    output = np.full_like(points, np.nan, dtype=np.float64)
    predicted = np.zeros(visibility.shape, dtype=bool)
    frame_count = len(points)
    if frame_count < 3:
        return output, predicted
    frame_axis = np.arange(frame_count)
    effective_window = min(window, frame_count if frame_count % 2 == 1 else frame_count - 1)
    if effective_window < 3:
        effective_window = 3

    for joint_id in range(points.shape[1]):
        valid = (
            (visibility[:, joint_id] >= MOTION_VISIBLE_MIN)
            & np.isfinite(points[:, joint_id]).all(axis=1)
        )
        valid_indices = np.flatnonzero(valid)
        if len(valid_indices) < 2:
            continue
        output[valid, joint_id, :] = points[valid, joint_id, :]
        invalid_runs: list[tuple[int, int]] = []
        start = None
        for frame_index, is_valid in enumerate(valid):
            if not is_valid and start is None:
                start = frame_index
            elif is_valid and start is not None:
                invalid_runs.append((start, frame_index - 1))
                start = None
        if start is not None:
            invalid_runs.append((start, frame_count - 1))

        for gap_start, gap_end in invalid_runs:
            gap_length = gap_end - gap_start + 1
            if gap_length > MOTION_MAX_PREDICTION_GAP:
                continue
            before = valid_indices[valid_indices < gap_start]
            after = valid_indices[valid_indices > gap_end]
            target_frames = np.arange(gap_start, gap_end + 1)
            if len(before) and len(after):
                support_indices = np.concatenate((before[-3:], after[:3]))
                support_x = support_indices.astype(np.float64)
                if len(support_indices) >= 2:
                    for axis in range(3):
                        support_y = points[support_indices, joint_id, axis]
                        try:
                            interpolator = PchipInterpolator(support_x, support_y)
                            output[target_frames, joint_id, axis] = interpolator(target_frames)
                        except ValueError:
                            output[target_frames, joint_id, axis] = np.interp(
                                target_frames, support_x, support_y
                            )
            elif len(before) >= 2:
                first, second = before[-2], before[-1]
                for axis in range(3):
                    first_value = points[first, joint_id, axis]
                    second_value = points[second, joint_id, axis]
                    slope = (second_value - first_value) / max(1, second - first)
                    output[target_frames, joint_id, axis] = second_value + slope * (target_frames - second)
            elif len(after) >= 2:
                first, second = after[0], after[1]
                for axis in range(3):
                    first_value = points[first, joint_id, axis]
                    second_value = points[second, joint_id, axis]
                    slope = (second_value - first_value) / max(1, second - first)
                    output[target_frames, joint_id, axis] = first_value + slope * (target_frames - first)
            predicted[target_frames, joint_id] = np.isfinite(output[target_frames, joint_id]).all(axis=1)

        finite = np.isfinite(output[:, joint_id]).all(axis=1)
        if finite.sum() < 4:
            continue
        for axis in range(3):
            values = output[:, joint_id, axis]
            valid_finite = np.isfinite(values)
            if valid_finite.sum() < 4:
                continue
            filled = np.interp(frame_axis, frame_axis[valid_finite], values[valid_finite])
            smooth = savgol_filter(
                filled,
                window_length=effective_window,
                polyorder=min(2, effective_window - 1),
                mode="interp",
            )
            values[valid_finite] = smooth[valid_finite]
            output[:, joint_id, axis] = values
    return output, predicted


MOTION_SIDE_LIMB_CHAINS = {
    "side_left": {
        "arm": {"near": (11, 13, 15), "far": (12, 14, 16)},
        "leg": {"near": (23, 25, 27, 29, 31), "far": (24, 26, 28, 30, 32)},
    },
    "side_right": {
        "arm": {"near": (12, 14, 16), "far": (11, 13, 15)},
        "leg": {"near": (24, 26, 28, 30, 32), "far": (23, 25, 27, 29, 31)},
    },
}


def _predict_side_view_far_limbs(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    view: str,
    exercise: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Predict occluded far-side limb chains by reflecting visible limbs."""
    chains = MOTION_SIDE_LIMB_CHAINS.get(view)
    if exercise != "pushup" or not chains:
        return points, predicted
    output = points.copy()
    predicted_output = predicted.copy()
    for frame_index in range(len(output)):
        left_shoulder = output[frame_index, 11]
        right_shoulder = output[frame_index, 12]
        if not np.isfinite(left_shoulder).all() or not np.isfinite(right_shoulder).all():
            continue
        lateral = right_shoulder - left_shoulder
        lateral_norm = float(np.linalg.norm(lateral))
        if lateral_norm < 1e-6:
            continue
        lateral = lateral / lateral_norm

        def reflect(vector: np.ndarray) -> np.ndarray:
            return vector - 2.0 * float(np.dot(vector, lateral)) * lateral

        for chain in chains.values():
            near = chain["near"]
            far = chain["far"]
            near_visible = all(
                visibility[frame_index, joint_id] >= MOTION_HIGH_CONFIDENCE
                and np.isfinite(output[frame_index, joint_id]).all()
                for joint_id in near
            )
            far_occluded = any(
                visibility[frame_index, joint_id] < MOTION_HIGH_CONFIDENCE
                for joint_id in far[1:]
            )
            far_anchor = output[frame_index, far[0]]
            if not near_visible or not far_occluded or not np.isfinite(far_anchor).all():
                continue
            previous_near = output[frame_index, near[0]]
            previous_far = far_anchor
            for near_id, far_id in zip(near[1:], far[1:]):
                offset = output[frame_index, near_id] - previous_near
                output[frame_index, far_id] = previous_far + reflect(offset)
                predicted_output[frame_index, far_id] = True
                previous_near = output[frame_index, near_id]
                previous_far = output[frame_index, far_id]
    return output, predicted_output


def _bone_length_targets(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
) -> dict[tuple[int, int], float]:
    targets: dict[tuple[int, int], float] = {}
    for parent, child in MOTION_RIGID_BONES:
        lengths = []
        for frame_index in range(len(points)):
            if (
                visibility[frame_index, parent] < MOTION_HIGH_CONFIDENCE
                or visibility[frame_index, child] < MOTION_HIGH_CONFIDENCE
                or predicted[frame_index, parent]
                or predicted[frame_index, child]
            ):
                continue
            first = points[frame_index, parent]
            second = points[frame_index, child]
            if not np.isfinite(first).all() or not np.isfinite(second).all():
                continue
            lengths.append(float(np.linalg.norm(second - first)))
        if lengths:
            targets[(parent, child)] = float(np.median(lengths))
    return targets


def _apply_bone_length_constraints(
    points: np.ndarray,
    visibility: np.ndarray,
    targets: dict[tuple[int, int], float],
    predicted: np.ndarray,
) -> np.ndarray:
    output = points.copy()
    for frame_index in range(len(output)):
        for parent, child in MOTION_RIGID_BONES:
            target = targets.get((parent, child))
            parent_visibility = visibility[frame_index, parent]
            child_visibility = visibility[frame_index, child]
            if not target or min(parent_visibility, child_visibility) < MOTION_VISIBLE_MIN:
                continue
            parent_point = output[frame_index, parent]
            child_point = output[frame_index, child]
            if not np.isfinite(parent_point).all() or not np.isfinite(child_point).all():
                continue
            vector = child_point - parent_point
            current = float(np.linalg.norm(vector))
            if current < 1e-6:
                continue
            is_predicted = bool(predicted[frame_index, parent] or predicted[frame_index, child])
            if is_predicted or min(parent_visibility, child_visibility) < MOTION_HIGH_CONFIDENCE:
                max_delta = MOTION_BONE_CORRECTION_MAX_M
                desired = current + float(np.clip(target - current, -max_delta, max_delta)) * 0.5
            else:
                desired = target
            output[frame_index, child] = parent_point + vector * (desired / current)
    return output


def _fit_ground_and_anchor(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    exercise: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Rotate into the support plane and hold a stable ground anchor."""
    support_indices = MOTION_FOOT_INDICES
    if exercise == "pushup":
        support_indices = tuple(dict.fromkeys((*MOTION_HAND_INDICES, *MOTION_FOOT_INDICES)))
    support_cloud = []
    lateral_vectors = []
    for frame_index in range(len(points)):
        for joint_id in support_indices:
            if (
                visibility[frame_index, joint_id] >= MOTION_HIGH_CONFIDENCE
                and not predicted[frame_index, joint_id]
                and np.isfinite(points[frame_index, joint_id]).all()
            ):
                support_cloud.append(points[frame_index, joint_id])
        for left_id, right_id in ((27, 28), (29, 30)):
            if (
                visibility[frame_index, left_id] >= MOTION_HIGH_CONFIDENCE
                and visibility[frame_index, right_id] >= MOTION_HIGH_CONFIDENCE
                and not predicted[frame_index, left_id]
                and not predicted[frame_index, right_id]
                and np.isfinite(points[frame_index, left_id]).all()
                and np.isfinite(points[frame_index, right_id]).all()
            ):
                lateral_vectors.append(points[frame_index, right_id] - points[frame_index, left_id])

    raw_up = np.asarray([0.0, -1.0, 0.0], dtype=np.float64)
    raw_x = np.asarray([1.0, 0.0, 0.0], dtype=np.float64)
    y_axis = None
    if len(support_cloud) >= 6:
        cloud = np.asarray(support_cloud, dtype=np.float64)
        center = np.median(cloud, axis=0)
        _, _, vh = np.linalg.svd(cloud - center, full_matrices=False)
        candidate = _unit_vector(vh[-1])
        if candidate is not None:
            if float(np.dot(candidate, raw_up)) < 0:
                candidate = -candidate
            tilt = math.acos(min(1.0, max(-1.0, float(np.dot(candidate, raw_up)))))
            if tilt <= math.radians(8.0):
                y_axis = candidate
    if y_axis is None:
        center = np.median(np.asarray(support_cloud), axis=0) if support_cloud else np.zeros(3, dtype=np.float64)
        y_axis = raw_up

    lateral = np.mean(np.asarray(lateral_vectors), axis=0) if lateral_vectors else raw_x
    lateral = lateral - float(np.dot(lateral, y_axis)) * y_axis
    candidate_x = _unit_vector(lateral)
    if candidate_x is None:
        candidate_x = raw_x - float(np.dot(raw_x, y_axis)) * y_axis
        candidate_x = _unit_vector(candidate_x)
    x_axis = candidate_x
    if x_axis is not None:
        lateral_tilt = math.acos(min(1.0, max(-1.0, abs(float(np.dot(x_axis, raw_x))))))
        if lateral_tilt > math.radians(18.0):
            x_axis = _unit_vector(raw_x - float(np.dot(raw_x, y_axis)) * y_axis)
    if x_axis is None:
        x_axis = raw_x
    z_axis = _unit_vector(np.cross(x_axis, y_axis))
    if z_axis is None:
        z_axis = np.asarray([0.0, 0.0, 1.0])
    corrected_x = _unit_vector(np.cross(y_axis, z_axis))
    if corrected_x is not None:
        x_axis = corrected_x
    axes = np.stack([x_axis, y_axis, z_axis], axis=0)
    rotated = (points - center) @ axes.T

    support_centers: list[np.ndarray] = []
    support_minima: list[float] = []
    frame_valid: list[bool] = []
    for frame_index in range(len(rotated)):
        valid_points = [
            rotated[frame_index, joint_id]
            for joint_id in support_indices
            if visibility[frame_index, joint_id] >= MOTION_HIGH_CONFIDENCE
            and not predicted[frame_index, joint_id]
            and np.isfinite(rotated[frame_index, joint_id]).all()
        ]
        if valid_points:
            support_centers.append(np.mean(np.asarray(valid_points), axis=0))
            support_minima.append(float(np.min([point[1] for point in valid_points])))
            frame_valid.append(True)
        else:
            support_centers.append(np.zeros(3, dtype=np.float64))
            support_minima.append(np.nan)
            frame_valid.append(False)

    anchor = np.zeros(3, dtype=np.float64)
    has_anchor = False
    for frame_index in range(len(rotated)):
        if frame_valid[frame_index]:
            candidate_anchor = np.asarray(
                [support_centers[frame_index][0], support_minima[frame_index], support_centers[frame_index][2]],
                dtype=np.float64,
            )
            if not has_anchor:
                anchor = candidate_anchor
                has_anchor = True
            else:
                delta = candidate_anchor - anchor
                distance = float(np.linalg.norm(delta))
                if distance > MOTION_ANCHOR_MAX_STEP_M:
                    anchor = anchor + delta * (MOTION_ANCHOR_MAX_STEP_M / distance)
                else:
                    anchor = candidate_anchor
        rotated[frame_index, :, 0] -= anchor[0]
        rotated[frame_index, :, 1] -= anchor[1]
        rotated[frame_index, :, 2] -= anchor[2]

    return rotated, {
        "plane_normal": [round(float(value), 6) for value in y_axis],
        "lateral_axis": [round(float(value), 6) for value in x_axis],
        "depth_axis": [round(float(value), 6) for value in z_axis],
        "support_mode": "hands_and_feet" if exercise == "pushup" else "feet",
        "support_points": len(support_cloud),
        "valid_support_frames": int(sum(frame_valid)),
        "anchor_max_step_m": MOTION_ANCHOR_MAX_STEP_M,
    }


def build_motion_payload(
    analysis_dir: Path,
    analysis: dict[str, Any],
    timeline: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a stabilized, foot-anchored 2D/3D motion track for the report."""
    clip_id = str(analysis.get("clip_id") or "")
    if not clip_id:
        return None
    clip_dir = analysis_dir / clip_id
    body_path = clip_dir / "angles" / "landmarks_body_frame.json"
    if not body_path.is_file():
        return None
    body_records = load_json(body_path)
    if not isinstance(body_records, list) or not body_records:
        return None

    pose_by_frame: dict[int, dict[str, Any]] = {}
    pose_path = clip_dir / "angles" / "landmarks.json"
    if pose_path.is_file():
        pose_records = load_json(pose_path)
        if isinstance(pose_records, list):
            for record in pose_records:
                frame_id = as_int(record.get("frame_id"))
                if frame_id is not None:
                    pose_by_frame[frame_id] = record

    def parse_world(value: Any) -> np.ndarray | None:
        if not isinstance(value, list) or len(value) < 33:
            return None
        points = np.full((33, 3), np.nan, dtype=np.float64)
        for index, point in enumerate(value[:33]):
            if not isinstance(point, list) or len(point) < 3:
                continue
            values = [as_float(point[axis]) for axis in range(3)]
            if all(item is not None for item in values):
                points[index] = values
        return points

    def parse_normalized(value: Any) -> np.ndarray | None:
        if not isinstance(value, list) or len(value) < 33:
            return None
        points = np.full((33, 2), np.nan, dtype=np.float64)
        for index, point in enumerate(value[:33]):
            if not isinstance(point, list) or len(point) < 2:
                continue
            values = [as_float(point[axis]) for axis in range(2)]
            if all(item is not None for item in values):
                points[index] = values
        return points

    frame_count = len(body_records)
    world = np.full((frame_count, 33, 3), np.nan, dtype=np.float64)
    normalized = np.full((frame_count, 33, 2), np.nan, dtype=np.float64)
    visibility = np.zeros((frame_count, 33), dtype=np.float64)
    times = np.zeros(frame_count, dtype=np.float64)
    detections = np.zeros(frame_count, dtype=bool)
    reps_by_frame: list[int | None] = [None] * frame_count
    used_world = False
    used_body = False

    fps = as_float(analysis.get("fps")) or 30.0
    rep_payload = [
        {
            "rep_id": as_int(rep.get("rep_id")),
            "start_s": rounded(as_float(rep.get("start_s"))) or 0.0,
            "end_s": rounded(as_float(rep.get("end_s"))) or 0.0,
            "bottom_s": rounded(as_float(rep.get("bottom_s"))) or 0.0,
        }
        for rep in timeline.get("reps") or []
        if as_int(rep.get("rep_id")) is not None
    ]
    rep_payload.sort(key=lambda rep: (rep["start_s"], rep["end_s"]))

    for index, body_record in enumerate(body_records):
        frame_id = as_int(body_record.get("frame_id"))
        if frame_id is None:
            frame_id = index
        timestamp_ms = as_float(body_record.get("timestamp_ms"))
        time_s = timestamp_ms / 1000.0 if timestamp_ms is not None else frame_id / fps
        times[index] = time_s
        reps_by_frame[index] = next(
            (
                rep["rep_id"]
                for rep in rep_payload
                if rep["start_s"] <= time_s <= rep["end_s"]
            ),
            None,
        )
        pose_record = pose_by_frame.get(frame_id) or {}
        detections[index] = bool(body_record.get("detected") or pose_record.get("detected"))
        visibility_raw = pose_record.get("visibility") or body_record.get("visibility") or []
        if isinstance(visibility_raw, list):
            for joint_id, item in enumerate(visibility_raw[:33]):
                value = as_float(item)
                visibility[index, joint_id] = 1.0 if value is None else min(1.0, max(0.0, value))

        world_raw = pose_record.get("landmarks_world")
        parsed_world = parse_world(world_raw)
        if parsed_world is not None:
            world[index] = parsed_world
            used_world = True
        else:
            parsed_body = parse_world(body_record.get("landmarks_body_frame"))
            if parsed_body is not None:
                world[index] = parsed_body
                used_body = True
        parsed_normalized = parse_normalized(pose_record.get("landmarks_normalized"))
        if parsed_normalized is not None:
            normalized[index] = parsed_normalized

    smoothed, predicted = _smooth_landmarks(world, visibility)
    smoothed, predicted = _predict_side_view_far_limbs(
        smoothed,
        visibility,
        predicted,
        str(analysis.get("view") or "unknown"),
        str(analysis.get("exercise") or "squat"),
    )
    bone_targets = _bone_length_targets(smoothed, visibility, predicted)
    constrained = _apply_bone_length_constraints(smoothed, visibility, bone_targets, predicted)
    stabilized, ground_metadata = _fit_ground_and_anchor(
        constrained,
        visibility,
        predicted,
        str(analysis.get("exercise") or "squat"),
    )
    process_metrics = build_process_metrics(
        stabilized,
        visibility,
        predicted,
        times,
        reps_by_frame,
        rep_payload,
        fps=fps,
        exercise=str(analysis.get("exercise") or "squat"),
        view=str(analysis.get("view") or "unknown"),
    )

    stride = max(1, int(round(fps / 15.0)))
    keep_indices = set(range(0, frame_count, stride))
    keep_indices.add(frame_count - 1)
    for rep in rep_payload:
        for key in ("start_s", "bottom_s", "end_s"):
            index = int(round(float(rep[key]) * fps))
            keep_indices.update(
                value
                for value in (index - 1, index, index + 1)
                if 0 <= value < frame_count
            )

    frames: list[list[Any]] = []
    for index in sorted(keep_indices):
        time_s = float(times[index])
        rep_id = reps_by_frame[index]
        points_3d = stabilized[index, MOTION_JOINT_IDS]
        if not np.isfinite(points_3d).any():
            frames.append([round(time_s, 4), rep_id, [], [], [], [], []])
            continue
        normalized_frame = normalized[index]
        has_2d = bool(np.isfinite(normalized_frame).any())
        frames.append(
            [
                round(time_s, 4),
                rep_id,
                [
                    round(float(value), 3) if np.isfinite(value) else None
                    for value in points_3d.reshape(-1)
                ],
                [int(round(float(visibility[index, joint_id]) * 255)) for joint_id in MOTION_JOINT_IDS],
                [
                    round(float(value), 4) if np.isfinite(value) else None
                    for value in normalized_frame.reshape(-1)
                ] if has_2d else [],
                [int(round(float(value) * 255)) for value in visibility[index]] if has_2d else [],
                [bool(value) for value in predicted[index, MOTION_JOINT_IDS]],
            ]
        )

    duration_s = max(
        float(np.nanmax(times)) if frame_count else 0.0,
        as_float(timeline.get("duration_s")) or 0.0,
    )
    if used_world and used_body:
        source = "mixed_world_stabilized"
    elif used_world:
        source = "mediapipe_world_stabilized"
    else:
        source = "mediapipe_body_frame_stabilized"
    return {
        "schema_version": "vls-motion-trace-v5",
        "source": source,
        "fps": round(fps, 3),
        "duration_s": round(duration_s, 4),
        "joints": list(MOTION_JOINT_IDS),
        "bones": [list(bone) for bone in MOTION_BONES],
        "video_joints": list(range(33)),
        "video_connections": [list(connection) for connection in MOTION_VIDEO_CONNECTIONS],
        "coordinate_system": {
            "name": "foot_ground",
            "unit": "m",
            "origin": "support_foot_center",
            "axes": {"x": "left_right", "y": "up", "z": "front_back"},
            "floor_y": 0.0,
        },
        "stabilization": {
            "smoothing": "savitzky_golay",
            "window": MOTION_SMOOTH_WINDOW,
            "max_interpolation_gap": MOTION_MAX_INTERPOLATION_GAP,
            "max_prediction_gap": MOTION_MAX_PREDICTION_GAP,
            "visible_threshold": MOTION_VISIBLE_MIN,
            "high_confidence_threshold": MOTION_HIGH_CONFIDENCE,
            "bone_correction_max_m": MOTION_BONE_CORRECTION_MAX_M,
            "rigid_bones": [list(bone) for bone in MOTION_RIGID_BONES],
            "bone_targets": {
                f"{parent}-{child}": round(float(length), 6)
                for (parent, child), length in bone_targets.items()
            },
            **ground_metadata,
        },
        "anchor": {
            "mode": "support_phase_ground_hold",
            "max_step_m": MOTION_ANCHOR_MAX_STEP_M,
        },
        "process_metrics": process_metrics,
        "reps": rep_payload,
        "frames": frames,
    }


def render_card_frames(
    feedback: dict[str, Any],
    clip_meta: dict[str, Any],
    analysis: dict[str, Any],
    frames_dir: Path,
    model_path: Path,
) -> tuple[int, list[str]]:
    """Render optional evidence frames; failures degrade to timestamp-only evidence."""
    cards = feedback.get("cards") or []
    if not cards:
        return 0, []
    source = Path(str(clip_meta.get("video_path") or clip_meta.get("source_path") or ""))
    if not source.is_file() or not model_path.is_file():
        return 0, ["evidence_frame_source_or_model_unavailable"]

    import cv2

    from render_clip_report import (
        VideoFrameReader,
        create_landmarker,
        detect_normalized_landmarks,
        render_issue_frame,
    )

    frames_dir.mkdir(parents=True, exist_ok=True)
    generated = 0
    warnings: list[str] = []
    landmarker = None
    try:
        landmarker = create_landmarker(model_path)
        with VideoFrameReader(source, str(analysis.get("rotation") or "none")) as reader:
            for card in cards:
                filename = f"{card['issue_id']}_frame_{int(card['anchor_frame'])}.png"
                target = frames_dir / filename
                card["frame_filename"] = filename
                if target.is_file():
                    card["frame_available"] = True
                    generated += 1
                    continue
                try:
                    frame = reader.read(int(card["anchor_source_frame"]))
                    landmarks = detect_normalized_landmarks(landmarker, frame)
                    fake_issue = {
                        "issue_id": card["issue_id"],
                        "issue_code": card["issue_code"],
                        "severity": card["severity"],
                        "confidence": card["confidence"],
                        "anchor_frame": card["anchor_source_frame"],
                        "anchor_time_s": card["anchor_source_frame"] / float(analysis["fps"]),
                        "representative_rep_id": card["representative_rep_id"],
                        "text": {
                            "title": card["title"],
                            "detail": card["detail"],
                            "cue": card["cue"],
                        },
                        "measurements": {
                            "primary_metric": card["primary_metric"],
                            "primary_value": card["primary_value"],
                            "unit": card["unit"],
                            "thresholds": card["thresholds"],
                        },
                        "visual_overlay": {
                            "highlight_joints": card["highlight_joints"],
                            "camera_zoom": 1.2,
                        },
                    }
                    annotated, _ = render_issue_frame(
                        frame,
                        landmarks,
                        fake_issue,
                        {},
                        float(analysis["trim"]["start_frame"]) / float(analysis["fps"]),
                    )
                    if not cv2.imwrite(str(target), annotated):
                        raise RuntimeError("cv2.imwrite returned false")
                    card["frame_available"] = True
                    generated += 1
                except Exception as exc:  # noqa: BLE001 - evidence fallback must not abort feedback
                    warnings.append(f"{card['issue_id']}:frame_render_failed:{type(exc).__name__}")
                    card["frame_available"] = False
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"frame_render_unavailable:{type(exc).__name__}")
    finally:
        if landmarker is not None:
            landmarker.close()
    return generated, warnings



MOTION_TRACE_SCRIPT = r"""
(() => {
  const data = __MOTION_DATA__;
  window.__VIGORYSYNC_PROCESS_METRICS__ = data?.process_metrics || null;
  const canvas = document.getElementById('motion-canvas');
  const stage = document.getElementById('motion-stage');
  const empty = document.getElementById('motion-empty');
  const clock = document.getElementById('motion-time');
  const resetButton = document.getElementById('motion-reset');
  const predictionNote = document.getElementById('motion-prediction');
  const media = document.getElementById('player');
  const videoCanvas = document.getElementById('skeleton-canvas');
  const videoFrame = videoCanvas?.parentElement || null;
  const videoCtx = videoCanvas?.getContext('2d') || null;
  if (!canvas) return;

  const showEmpty = (message) => {
    if (empty) {
      empty.textContent = message;
      empty.hidden = false;
    }
  };
  if (!data || !Array.isArray(data.frames) || !data.frames.length) {
    showEmpty('本次没有可用的 3D 姿态数据');
    if (clock) clock.textContent = '无轨迹';
    canvas.setAttribute('aria-hidden', 'true');
    return;
  }

  const ctx = canvas.getContext('2d');
  const jointOrder = data.joints.map((id) => Number(id));
  const jointIndex = new Map(jointOrder.map((id, index) => [id, index]));
  const frames = data.frames;
  const reps = Array.isArray(data.reps) ? data.reps : [];
  const colors = {
    cyan: [134, 232, 255],
    acid: [217, 255, 67],
    ink: [241, 240, 231],
    signal: [255, 100, 77]
  };
  const jointNames = {
    0: '头部', 2: '左眼', 5: '右眼', 7: '左耳', 8: '右耳',
    11: '左肩', 12: '右肩', 13: '左肘', 14: '右肘', 15: '左手', 16: '右手',
    23: '左髋', 24: '右髋', 25: '左膝', 26: '右膝', 27: '左踝', 28: '右踝',
    29: '左脚跟', 30: '右脚跟', 31: '左脚尖', 32: '右脚尖'
  };
  const videoConnections = Array.isArray(data.video_connections) ? data.video_connections : [];
  const videoLeftJoints = new Set([2, 7, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31]);
  const videoRightJoints = new Set([5, 8, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32]);
  const poseLeftJoints = new Set([2, 7, 11, 13, 15, 23, 25, 27, 29, 31]);
  const poseRightJoints = new Set([5, 8, 12, 14, 16, 24, 26, 28, 30, 32]);
  const coordinateBox = buildCoordinateBox();

  let width = 0;
  let height = 0;
  let dpr = 1;
  let yaw = -0.5;
  let pitch = 0.1;
  let zoom = 1;
  let dirty = true;
  let lastTime = -1;
  let drag = null;
  const cameraDistance = 2.8;
  const targetHeight = 0.92;

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const rgba = (rgb, alpha) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
  const jointColor = (jointId) => poseLeftJoints.has(Number(jointId)) ? colors.cyan : (poseRightJoints.has(Number(jointId)) ? colors.acid : colors.ink);

  function resizeVideoOverlay() {
    if (!videoCanvas || !videoCtx || !videoFrame) return;
    const rect = videoFrame.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    videoCanvas.width = Math.max(1, Math.round(rect.width * dpr));
    videoCanvas.height = Math.max(1, Math.round(rect.height * dpr));
    videoCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function resize() {
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    width = Math.max(240, rect.width);
    height = Math.max(280, rect.height);
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    resizeVideoOverlay();
    dirty = true;
  }

  function project(point) {
    const x = Number(point[0]) || 0;
    const y = (Number(point[1]) || 0) - targetHeight;
    const z = Number(point[2]) || 0;
    const cy = Math.cos(yaw);
    const sy = Math.sin(yaw);
    const x1 = x * cy - z * sy;
    const z1 = x * sy + z * cy;
    const cp = Math.cos(pitch);
    const sp = Math.sin(pitch);
    const y1 = y * cp - z1 * sp;
    const z2 = y * sp + z1 * cp;
    const depth = z2 + cameraDistance;
    const scale = Math.min(width, height) * 1.22 * zoom;
    const perspective = scale / Math.max(0.35, depth);
    return {
      x: width * 0.5 + x1 * perspective,
      y: height * 0.52 - y1 * perspective,
      z: depth
    };
  }

  function drawLine(first, second, color, alpha, lineWidth, dash = []) {
    ctx.save();
    ctx.strokeStyle = rgba(color, alpha);
    ctx.lineWidth = lineWidth;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(first.x, first.y);
    ctx.lineTo(second.x, second.y);
    ctx.stroke();
    ctx.restore();
  }

  function buildCoordinateBox() {
    let maxX = 0.5;
    let maxY = 1.2;
    let maxZ = 0.5;
    for (const frame of frames) {
      const points = frame[2] || [];
      for (let jointId = 0; jointId < jointOrder.length; jointId += 1) {
        const offset = jointId * 3;
        if (offset + 2 >= points.length) continue;
        const x = Number(points[offset]);
        const y = Number(points[offset + 1]);
        const z = Number(points[offset + 2]);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
        maxX = Math.max(maxX, Math.abs(x));
        maxY = Math.max(maxY, y);
        maxZ = Math.max(maxZ, Math.abs(z));
      }
    }
    const tick = 0.5;
    const xExtent = Math.max(0.5, Math.ceil((maxX + 0.12) / tick) * tick);
    const zExtent = Math.max(0.5, Math.ceil((maxZ + 0.12) / tick) * tick);
    const yMax = Math.max(2.0, Math.ceil((maxY + 0.2) / tick) * tick);
    return { xMin: -xExtent, xMax: xExtent, yMin: 0, yMax, zMin: -zExtent, zMax: zExtent, tick };
  }

  function tickValues(minimum, maximum, step) {
    const values = [];
    for (let value = Math.ceil(minimum / step) * step; value <= maximum + 1e-6; value += step) {
      values.push(Math.abs(value) < 1e-8 ? 0 : value);
    }
    return values;
  }

  function formatMeters(value) {
    return `${Number(value).toFixed(1)}m`;
  }

  function drawCoordinateBox() {
    const box = coordinateBox;
    const xticks = tickValues(box.xMin, box.xMax, box.tick);
    const yticks = tickValues(box.yMin, box.yMax, box.tick);
    const zticks = tickValues(box.zMin, box.zMax, box.tick);

    for (const x of xticks) {
      drawLine(project([x, box.yMin, box.zMin]), project([x, box.yMin, box.zMax]), colors.ink, 0.09, 1);
      drawLine(project([x, box.yMin, box.zMin]), project([x, box.yMax, box.zMin]), colors.ink, 0.07, 1);
    }
    for (const z of zticks) {
      drawLine(project([box.xMin, box.yMin, z]), project([box.xMax, box.yMin, z]), colors.ink, 0.09, 1);
      drawLine(project([box.xMin, box.yMin, z]), project([box.xMin, box.yMax, z]), colors.ink, 0.07, 1);
    }
    for (const y of yticks) {
      drawLine(project([box.xMin, y, box.zMin]), project([box.xMax, y, box.zMin]), colors.ink, 0.07, 1);
      drawLine(project([box.xMin, y, box.zMin]), project([box.xMin, y, box.zMax]), colors.ink, 0.07, 1);
    }

    drawLine(project([box.xMin, box.yMin, box.zMax]), project([box.xMax, box.yMin, box.zMax]), colors.signal, 0.78, 1.8);
    drawLine(project([box.xMax, box.yMin, box.zMin]), project([box.xMax, box.yMin, box.zMax]), colors.cyan, 0.78, 1.8);
    drawLine(project([box.xMin, box.yMin, box.zMin]), project([box.xMin, box.yMax, box.zMin]), colors.acid, 0.78, 1.8);
    drawLine(project([box.xMin, box.yMin, box.zMax]), project([box.xMin, box.yMax, box.zMax]), colors.ink, 0.2, 1);
    drawLine(project([box.xMax, box.yMin, box.zMin]), project([box.xMax, box.yMax, box.zMin]), colors.ink, 0.2, 1);
    drawLine(project([box.xMin, box.yMax, box.zMin]), project([box.xMax, box.yMax, box.zMin]), colors.ink, 0.2, 1);

    ctx.save();
    ctx.font = '8px ui-monospace, Consolas, monospace';
    ctx.fillStyle = 'rgba(241,240,231,.48)';
    ctx.textAlign = 'right';
    for (const y of yticks) {
      const point = project([box.xMin, y, box.zMin]);
      ctx.fillText(formatMeters(y), point.x - 5, point.y + 3);
    }
    ctx.textAlign = 'center';
    for (const x of xticks) {
      const point = project([x, box.yMin, box.zMax]);
      ctx.fillText(formatMeters(x), point.x, point.y + 13);
    }
    ctx.textAlign = 'left';
    for (const z of zticks) {
      const point = project([box.xMax, box.yMin, z]);
      ctx.fillText(formatMeters(z), point.x + 5, point.y + 12);
    }
    ctx.font = '700 8px ui-monospace, Consolas, monospace';
    const xLabel = project([(box.xMin + box.xMax) / 2, box.yMin, box.zMax]);
    const yLabel = project([box.xMin, box.yMax, box.zMin]);
    const zLabel = project([box.xMax, box.yMin, (box.zMin + box.zMax) / 2]);
    ctx.fillStyle = rgba(colors.signal, 0.9);
    ctx.fillText('X 左右 / m', xLabel.x, xLabel.y + 25);
    ctx.fillStyle = rgba(colors.acid, 0.9);
    ctx.fillText('Y 高度 / m', yLabel.x - 8, yLabel.y - 8);
    ctx.fillStyle = rgba(colors.cyan, 0.9);
    ctx.fillText('Z 前后 / m', zLabel.x + 5, zLabel.y + 24);
    ctx.fillStyle = 'rgba(241,240,231,.32)';
    ctx.font = '9px ui-monospace, Consolas, monospace';
    ctx.fillText('FOOT GROUND FRAME · Y=0 · m', 12, height - 12);
    ctx.restore();
  }

  function nearestFrameIndex(time) {
    let low = 0;
    let high = frames.length - 1;
    let found = -1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (frames[middle][0] <= time) {
        found = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return found;
  }

  function nearestFrame(time) {
    const index = nearestFrameIndex(time);
    return index >= 0 ? frames[index] : null;
  }

  function samplePose(time) {
    const lowerIndex = nearestFrameIndex(time);
    if (lowerIndex < 0) return null;
    const lower = frames[lowerIndex];
    const upper = frames[lowerIndex + 1] || null;
    const lowerPoints = lower[2] || [];
    const upperPoints = upper && upper[2] ? upper[2] : [];
    let points = lowerPoints;
    let visibility = lower[3] || [];
    let predicted = lower[6] || [];
    let factor = 0;
    if (lowerPoints.length && upperPoints.length && upper[0] > lower[0]) {
      factor = clamp((time - lower[0]) / (upper[0] - lower[0]), 0, 1);
      points = lowerPoints.map((value, index) => {
        const first = Number(value);
        const second = Number(upperPoints[index]);
        if (Number.isFinite(first) && Number.isFinite(second)) return first + (second - first) * factor;
        return Number.isFinite(first) ? first : second;
      });
      const upperVisibility = upper[3] || [];
      visibility = points.map((_, index) => {
        const a = Number(lower[3]?.[index] ?? 255);
        const b = Number(upperVisibility[index] ?? 255);
        return a + (b - a) * factor;
      });
      const upperPredicted = upper[6] || [];
      predicted = points.map((_, index) => Boolean(predicted[index] || upperPredicted[index]));
    } else if (!lowerPoints.length && upperPoints.length && upper[0] - lower[0] < 0.18) {
      points = upperPoints;
      visibility = upper[3] || [];
      predicted = upper[6] || [];
    } else if (lowerPoints.length && !upperPoints.length && time - lower[0] > 0.18) {
      return null;
    }
    if (!points.length) return null;
    const byId = new Map();
    for (const jointId of jointOrder) {
      const index = jointIndex.get(jointId);
      if (index === undefined) continue;
      const offset = index * 3;
      if (offset + 2 >= points.length) continue;
      const x = points[offset];
      const y = points[offset + 1];
      const z = points[offset + 2];
      if (x === null || y === null || z === null || !Number.isFinite(Number(x)) || !Number.isFinite(Number(y)) || !Number.isFinite(Number(z))) continue;
      byId.set(jointId, {
        point: [Number(x), Number(y), Number(z)],
        visibility: Number(visibility[index] ?? 255),
        predicted: Boolean(predicted[index])
      });
    }
    return byId.size ? byId : null;
  }

  function sampleVideoPose(time) {
    const lowerIndex = nearestFrameIndex(time);
    if (lowerIndex < 0) return null;
    const lower = frames[lowerIndex];
    const upper = frames[lowerIndex + 1] || null;
    const lowerPoints = lower[4] || [];
    const upperPoints = upper && upper[4] ? upper[4] : [];
    if (lowerPoints.length !== 66 && upperPoints.length !== 66) return null;
    let points = lowerPoints.length === 66 ? lowerPoints.slice() : upperPoints.slice();
    let visibility = (lowerPoints.length === 66 ? lower[5] : upper[5]) || [];
    if (lowerPoints.length === 66 && upperPoints.length === 66 && upper[0] > lower[0]) {
      const factor = clamp((time - lower[0]) / (upper[0] - lower[0]), 0, 1);
      points = lowerPoints.map((value, index) => value + (upperPoints[index] - value) * factor);
      const lowerVisibility = lower[5] || [];
      const upperVisibility = upper[5] || [];
      visibility = points.map((_, index) => {
        const first = Number(lowerVisibility[index] ?? 255);
        const second = Number(upperVisibility[index] ?? 255);
        return first + (second - first) * factor;
      });
    }
    return { points, visibility };
  }

  function videoJointColor(jointId) {
    if (videoLeftJoints.has(jointId)) return colors.cyan;
    if (videoRightJoints.has(jointId)) return colors.acid;
    return colors.ink;
  }

  function drawVideoSkeleton(time) {
    if (!videoCtx || !videoFrame || !media?.videoWidth) return;
    const rect = videoFrame.getBoundingClientRect();
    videoCtx.clearRect(0, 0, rect.width, rect.height);
    const sampled = sampleVideoPose(time);
    if (!sampled) return;
    const videoWidth = Number(media.videoWidth);
    const videoHeight = Number(media.videoHeight);
    const scale = Math.min(rect.width / videoWidth, rect.height / videoHeight);
    const drawWidth = videoWidth * scale;
    const drawHeight = videoHeight * scale;
    const offsetX = (rect.width - drawWidth) / 2;
    const offsetY = (rect.height - drawHeight) / 2;
    const points = sampled.points;
    const visibility = sampled.visibility;
    const position = (jointId) => ({
      x: offsetX + Number(points[jointId * 2] || 0) * drawWidth,
      y: offsetY + Number(points[jointId * 2 + 1] || 0) * drawHeight
    });
    videoCtx.save();
    videoCtx.lineCap = 'round';
    videoCtx.lineJoin = 'round';
    for (const [firstId, secondId] of videoConnections) {
      const minimumVisibility = Math.min(Number(visibility[firstId] ?? 0), Number(visibility[secondId] ?? 0));
      if (minimumVisibility < 90) continue;
      const first = position(firstId);
      const second = position(secondId);
      videoCtx.strokeStyle = rgba(colors.ink, minimumVisibility < 166 ? 0.45 : 0.82);
      videoCtx.lineWidth = minimumVisibility < 166 ? 1.1 : 1.4;
      videoCtx.beginPath();
      videoCtx.moveTo(first.x, first.y);
      videoCtx.lineTo(second.x, second.y);
      videoCtx.stroke();
    }
    for (let jointId = 0; jointId < 33; jointId += 1) {
      const jointVisibility = Number(visibility[jointId] ?? 0);
      if (jointVisibility < 90) continue;
      const point = position(jointId);
      const color = videoJointColor(jointId);
      videoCtx.fillStyle = rgba(color, jointVisibility < 166 ? 0.56 : 0.88);
      videoCtx.beginPath();
      videoCtx.arc(point.x, point.y, jointId <= 10 ? 1.8 : 2.4, 0, Math.PI * 2);
      videoCtx.fill();
    }
    videoCtx.restore();
  }

  function currentRep(time) {
    return reps.find((rep) => time >= Number(rep.start_s) && time <= Number(rep.end_s)) || null;
  }

  function activeIssueJoints(time) {
    const active = new Set();
    for (const card of document.querySelectorAll('.issue-card')) {
      const start = Number(card.dataset.start);
      const end = Number(card.dataset.end);
      if (!Number.isFinite(start) || !Number.isFinite(end) || time < start || time > end) continue;
      for (const value of String(card.dataset.joints || '').split(',')) {
        const jointId = Number(value);
        if (Number.isFinite(jointId)) active.add(jointId);
      }
    }
    return active;
  }

  function drawSkeleton(pose, highlighted) {
    if (!pose) return;
    const segments = [];
    for (const [firstId, secondId] of data.bones) {
      const first = pose.get(Number(firstId));
      const second = pose.get(Number(secondId));
      if (!first || !second) continue;
      const predictedSegment = Boolean(first.predicted || second.predicted);
      const minimumVisibility = Math.min(first.visibility, second.visibility);
      if (!predictedSegment && minimumVisibility < 89) continue;
      const a = project(first.point);
      const b = project(second.point);
      segments.push({
        a,
        b,
        firstId: Number(firstId),
        secondId: Number(secondId),
        opacity: predictedSegment ? 0.48 : clamp(minimumVisibility / 255, 0.42, 1),
        predicted: predictedSegment,
        uncertain: minimumVisibility < 166,
        highlighted: highlighted.has(Number(firstId)) || highlighted.has(Number(secondId))
      });
    }
    segments.sort((left, right) => (right.a.z + right.b.z) / 2 - (left.a.z + left.b.z) / 2);
    for (const segment of segments) {
      if (segment.highlighted) {
        drawLine(segment.a, segment.b, colors.signal, segment.predicted ? 0.72 : 0.95, 4.2, segment.predicted ? [6, 4] : []);
        if (!segment.predicted) drawLine(segment.a, segment.b, colors.signal, 0.18, 10);
      } else if (segment.predicted) {
        const firstId = Number(segment.firstId);
        const secondId = Number(segment.secondId);
        const predictionColor = poseRightJoints.has(firstId) && poseRightJoints.has(secondId)
          ? colors.acid
          : (poseLeftJoints.has(firstId) && poseLeftJoints.has(secondId) ? colors.cyan : colors.ink);
        drawLine(segment.a, segment.b, predictionColor, 0.9, 2.6, [7, 4]);
      } else if (segment.uncertain) {
        drawLine(segment.a, segment.b, colors.ink, 0.44, 1.8);
      } else {
        drawLine(segment.a, segment.b, colors.ink, 0.2 * segment.opacity, 6);
        drawLine(segment.a, segment.b, colors.ink, 0.86 * segment.opacity, 2.4);
      }
    }
    const joints = [...pose.entries()]
      .map(([id, value]) => ({ id, value, projected: project(value.point) }))
      .filter((item) => item.value.predicted || item.value.visibility >= 89)
      .sort((left, right) => right.projected.z - left.projected.z);
    for (const item of joints) {
      const color = highlighted.has(item.id) ? colors.signal : jointColor(item.id);
      const radius = highlighted.has(item.id) ? 4.6 : (item.value.predicted ? 2.8 : 3.4);
      ctx.save();
      if (item.value.predicted) {
        ctx.strokeStyle = rgba(color, 0.72);
        ctx.lineWidth = 1.6;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.arc(item.projected.x, item.projected.y, radius, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        const visibilityAlpha = clamp(item.value.visibility / 255, 0.45, 1);
        ctx.fillStyle = rgba(color, item.value.visibility < 166 ? 0.58 : 0.55 + 0.45 * visibilityAlpha);
        ctx.shadowColor = rgba(color, 0.8);
        ctx.shadowBlur = highlighted.has(item.id) ? 12 : 6;
        ctx.beginPath();
        ctx.arc(item.projected.x, item.projected.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }

  function draw(time) {
    lastTime = time;
    ctx.clearRect(0, 0, width, height);
    drawCoordinateBox();
    const pose = samplePose(time);
    const rep = currentRep(time);
    if (pose) {
      if (empty) empty.hidden = true;
      drawSkeleton(pose, activeIssueJoints(time));
      const predictedSegments = data.bones.filter(([firstId, secondId]) => {
        const first = pose.get(Number(firstId));
        const second = pose.get(Number(secondId));
        return first && second && (first.predicted || second.predicted);
      });
      if (predictionNote) {
        if (predictedSegments.length) {
          const labels = predictedSegments.map(([firstId, secondId]) =>
            `${jointNames[firstId] || `关节${firstId}`}–${jointNames[secondId] || `关节${secondId}`}`
          );
          predictionNote.textContent = `遮挡预测：${[...new Set(labels)].join('、')}`;
          predictionNote.hidden = false;
        } else {
          predictionNote.hidden = true;
          predictionNote.textContent = '';
        }
      }
    } else {
      showEmpty('本帧未检测到可靠姿态');
      if (predictionNote) predictionNote.hidden = true;
    }
    if (clock) {
      clock.textContent = `${Number(time).toFixed(2)}s${rep ? ` · REP ${String(rep.rep_id).padStart(2, '0')}` : ''}`;
    }
  }

  function animate() {
    const time = media ? Number(media.currentTime) || 0 : 0;
    if (dirty || Math.abs(time - lastTime) > 0.0005) {
      draw(time);
      drawVideoSkeleton(time);
      dirty = false;
    }
    requestAnimationFrame(animate);
  }

  canvas.addEventListener('pointerdown', (event) => {
    drag = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!drag) return;
    yaw += (event.clientX - drag.x) * 0.008;
    pitch = clamp(pitch + (event.clientY - drag.y) * 0.008, -1.15, 1.15);
    drag = { x: event.clientX, y: event.clientY };
    dirty = true;
  });
  const endDrag = () => { drag = null; };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    zoom = clamp(zoom * (event.deltaY > 0 ? 0.9 : 1.1), 0.55, 2.2);
    dirty = true;
  }, { passive: false });
  canvas.addEventListener('dblclick', () => {
    yaw = -0.5;
    pitch = 0.1;
    zoom = 1;
    dirty = true;
  });
  resetButton?.addEventListener('click', () => {
    yaw = -0.5;
    pitch = 0.1;
    zoom = 1;
    dirty = true;
  });
  media?.addEventListener('timeupdate', () => { dirty = true; });
  media?.addEventListener('seeked', () => { dirty = true; });
  media?.addEventListener('play', () => { dirty = true; });
  media?.addEventListener('pause', () => { dirty = true; });
  media?.addEventListener('loadedmetadata', () => { dirty = true; });
  if (window.ResizeObserver && stage) {
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(stage);
    if (videoFrame) resizeObserver.observe(videoFrame);
  } else {
    window.addEventListener('resize', resize);
  }
  resize();
  animate();
})();
"""


def build_process_metric_cards(motion: dict[str, Any] | None) -> str:
    process_metrics = (motion or {}).get("process_metrics") or {}
    metrics = process_metrics.get("metrics") or {}
    if not metrics:
        return (
            '<div class="process-empty">3D 轨迹可用后，将在这里显示重心、躯干与左右对称性曲线。</div>'
        )
    status_class = {
        "normal": "normal",
        "attention": "attention",
        "abnormal": "abnormal",
        "unavailable": "unavailable",
    }
    cards = []
    for index, metric_id in enumerate(("com_ap_drift", "torso_variability", "symmetry_index"), 1):
        metric = metrics.get(metric_id) or {}
        status = str(metric.get("status") or "unavailable")
        status_label = str(metric.get("status_label") or "数据不足")
        chart = metric.get("chart") or {}
        series = chart.get("series") or {}
        has_series = bool(series.get("t"))
        live_unit = str(metric.get("unit") or "")
        live_chart_unit = str(chart.get("y_label") or "")
        details = []
        if metric.get("definition"):
            details.append(f"<p><b>定义：</b>{html.escape(str(metric['definition']))}</p>")
        if metric.get("threshold_text"):
            details.append(f"<p><b>分级：</b>{html.escape(str(metric['threshold_text']))}</p>")
        if metric.get("basis"):
            details.append(f"<p><b>依据：</b>{html.escape(str(metric['basis']))}</p>")
        if metric.get("view_note"):
            details.append(f"<p class=\"process-view-note\">{html.escape(str(metric['view_note']))}</p>")
        cards.append(
            f"""
            <article class="process-metric {status_class.get(status, 'unavailable')}"
              data-process-metric="{html.escape(metric_id)}"
              data-live-unit="{html.escape(live_unit)}">
              <header class="process-metric-head">
                <div><span class="process-index">0{index}</span><h3>{html.escape(str(metric.get('title') or metric_id))}</h3></div>
                <span class="process-status {status_class.get(status, 'unavailable')}">{html.escape(status_label)}</span>
              </header>
              <div class="process-reading"><strong>{html.escape(str(metric.get('value_text') or '—'))}</strong><span>{html.escape(live_unit)}</span></div>
              <p class="process-headline">{html.escape(str(metric.get('headline') or ''))}</p>
              <p class="process-detail">{html.escape(str(metric.get('detail') or ''))}</p>
              <div class="process-chart-wrap">
                <canvas class="process-chart" role="img"
                  aria-label="{html.escape(str(metric.get('title') or metric_id))}时间序列曲线"
                  data-chart-kind="{html.escape(str(chart.get('kind') or 'positive'))}"></canvas>
                <div class="process-chart-empty" {'hidden' if has_series else ''}>暂无可绘制数据</div>
              </div>
              <div class="process-live">
                <span>当前 <b data-live-value>--</b> <em data-live-extra></em></span>
                <span title="{html.escape(live_chart_unit)}">点击曲线可跳转视频</span>
              </div>
              <details class="process-basis">
                <summary>定义与阈值依据</summary>
                {''.join(details) or '<p>数据不足，暂无说明。</p>'}
              </details>
            </article>
            """
        )
    return "".join(cards)


PROCESS_METRICS_SCRIPT = r"""
(() => {
  const payload = window.__VIGORYSYNC_PROCESS_METRICS__ || null;
  const cards = [...document.querySelectorAll('[data-process-metric]')];
  const media = document.getElementById('player');
  if (!cards.length) return;
  const setEmpty = (entry, message) => {
    if (entry.empty) {
      entry.empty.textContent = message;
      entry.empty.hidden = false;
    }
  };
  if (!payload || !payload.metrics) {
    for (const card of cards) setEmpty({ empty: card.querySelector('.process-chart-empty') }, '3D 轨迹不可用');
    return;
  }
  const duration = Math.max(0.001, Number(payload.duration_s) || 0.001);
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const fmt = (value, digits = 1) => Number.isFinite(value) ? value.toFixed(digits) : '--';
  const entries = cards.map((card) => {
    const metricId = card.dataset.processMetric;
    return {
      card,
      metric: payload.metrics[metricId] || {},
      canvas: card.querySelector('.process-chart'),
      wrap: card.querySelector('.process-chart-wrap'),
      empty: card.querySelector('.process-chart-empty'),
      liveValue: card.querySelector('[data-live-value]'),
      liveExtra: card.querySelector('[data-live-extra]'),
      width: 0,
      height: 0,
      dpr: 1
    };
  }).filter((entry) => entry.canvas && entry.metric && entry.metric.chart);

  function resize(entry) {
    const rect = entry.wrap.getBoundingClientRect();
    entry.width = Math.max(220, rect.width);
    entry.height = Math.max(150, rect.height);
    entry.dpr = Math.min(2, window.devicePixelRatio || 1);
    entry.canvas.width = Math.round(entry.width * entry.dpr);
    entry.canvas.height = Math.round(entry.height * entry.dpr);
    entry.canvas.style.width = `${entry.width}px`;
    entry.canvas.style.height = `${entry.height}px`;
    entry.canvas.getContext('2d').setTransform(entry.dpr, 0, 0, entry.dpr, 0, 0);
  }

  function domain(entry) {
    const chart = entry.metric.chart;
    const values = (chart.series?.v || []).map(Number).filter(Number.isFinite);
    const warn = Number(chart.warn) || 0;
    const alarm = Number(chart.alarm) || 0;
    if (chart.kind === 'signed') {
      const observed = values.reduce((peak, value) => Math.max(peak, Math.abs(value)), 0);
      const limit = Math.max(observed * 1.18, warn * 1.55, alarm * 1.25, 1);
      return { min: -limit, max: limit };
    }
    const observed = values.reduce((peak, value) => Math.max(peak, value), 0);
    return { min: 0, max: Math.max(observed * 1.15, alarm * 1.28, warn * 1.5, 1) };
  }

  function draw(entry, currentTime) {
    const chart = entry.metric.chart;
    const series = chart.series || {};
    const times = (series.t || []).map(Number);
    const values = (series.v || []).map(Number);
    const ctx = entry.canvas.getContext('2d');
    ctx.clearRect(0, 0, entry.width, entry.height);
    if (!times.length || !values.length) {
      setEmpty(entry, '暂无可绘制数据');
      return;
    }
    entry.empty.hidden = true;
    const plot = { left: 43, right: 12, top: 19, bottom: 27 };
    const plotWidth = Math.max(1, entry.width - plot.left - plot.right);
    const plotHeight = Math.max(1, entry.height - plot.top - plot.bottom);
    const x = (time) => plot.left + clamp(time / duration, 0, 1) * plotWidth;
    const limits = domain(entry);
    const y = (value) => plot.top + (limits.max - value) / (limits.max - limits.min) * plotHeight;
    const warn = Number(chart.warn);
    const alarm = Number(chart.alarm);
    const positiveWarn = Number.isFinite(warn) ? y(warn) : null;
    const positiveAlarm = Number.isFinite(alarm) ? y(alarm) : null;

    ctx.save();
    ctx.fillStyle = 'rgba(241,240,231,.018)';
    ctx.fillRect(plot.left, plot.top, plotWidth, plotHeight);
    const reps = Array.isArray(chart.reps) ? chart.reps : [];
    reps.forEach((rep, index) => {
      const left = x(Number(rep.start_s) || 0);
      const right = x(Number(rep.end_s) || 0);
      ctx.fillStyle = index % 2 ? 'rgba(134,232,255,.035)' : 'rgba(217,255,67,.035)';
      ctx.fillRect(left, plot.top, Math.max(1, right - left), plotHeight);
      if (right - left > 33) {
        ctx.fillStyle = 'rgba(241,240,231,.42)';
        ctx.font = '9px ui-monospace,Consolas,monospace';
        ctx.fillText(`R${rep.rep_id}`, left + 4, plot.top + 11);
      }
    });

    if (chart.kind === 'signed' && Number.isFinite(warn) && Number.isFinite(alarm)) {
      const warnY = y(warn), alarmY = y(alarm), lowWarnY = y(-warn), lowAlarmY = y(-alarm);
      ctx.fillStyle = 'rgba(240,197,107,.10)';
      ctx.fillRect(plot.left, warnY, plotWidth, Math.max(0, alarmY - warnY));
      ctx.fillRect(plot.left, lowWarnY, plotWidth, Math.max(0, lowAlarmY - lowWarnY));
      ctx.fillStyle = 'rgba(255,100,77,.12)';
      ctx.fillRect(plot.left, plot.top, plotWidth, Math.max(0, alarmY - plot.top));
      ctx.fillRect(plot.left, lowAlarmY, plotWidth, Math.max(0, plot.top + plotHeight - lowAlarmY));
    } else if (Number.isFinite(warn) && Number.isFinite(alarm)) {
      ctx.fillStyle = 'rgba(240,197,107,.10)';
      ctx.fillRect(plot.left, positiveWarn, plotWidth, Math.max(0, positiveAlarm - positiveWarn));
      ctx.fillStyle = 'rgba(255,100,77,.12)';
      ctx.fillRect(plot.left, plot.top, plotWidth, Math.max(0, positiveAlarm - plot.top));
    }

    ctx.strokeStyle = 'rgba(241,240,231,.11)';
    ctx.lineWidth = 1;
    ctx.font = '9px ui-monospace,Consolas,monospace';
    ctx.fillStyle = 'rgba(158,163,154,.86)';
    for (let step = 0; step <= 4; step += 1) {
      const value = limits.max - (limits.max - limits.min) * step / 4;
      const py = y(value);
      ctx.beginPath();
      ctx.moveTo(plot.left, py);
      ctx.lineTo(plot.left + plotWidth, py);
      ctx.stroke();
      ctx.fillText(`${value.toFixed(value === 0 ? 0 : 1)}`, 4, py + 3);
    }

    const thresholdLine = (value, label, color) => {
      if (!Number.isFinite(value)) return;
      const py = y(value);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plot.left, py);
      ctx.lineTo(plot.left + plotWidth, py);
      ctx.stroke();
      ctx.fillStyle = color;
      ctx.font = '8px ui-monospace,Consolas,monospace';
      ctx.fillText(label, plot.left + plotWidth - 58, py - 4);
      ctx.restore();
    };
    thresholdLine(warn, '关注', 'rgba(240,197,107,.9)');
    thresholdLine(alarm, '异常', 'rgba(255,100,77,.92)');
    if (chart.kind === 'signed') {
      thresholdLine(-warn, '关注', 'rgba(240,197,107,.72)');
      thresholdLine(-alarm, '异常', 'rgba(255,100,77,.75)');
    }

    const color = entry.metric.status === 'abnormal'
      ? '#ff644d'
      : entry.metric.status === 'attention' ? '#f0c56b' : '#86e8ff';
    ctx.save();
    ctx.beginPath();
    let started = false;
    for (let index = 0; index < times.length; index += 1) {
      if (!Number.isFinite(values[index])) {
        started = false;
        continue;
      }
      const px = x(times[index]);
      const py = y(values[index]);
      if (!started) {
        ctx.moveTo(px, py);
        started = true;
      } else {
        ctx.lineTo(px, py);
      }
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.2;
    ctx.shadowColor = color;
    ctx.shadowBlur = 7;
    ctx.stroke();
    ctx.restore();

    const cursorX = x(currentTime);
    ctx.strokeStyle = 'rgba(241,240,231,.7)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cursorX, plot.top);
    ctx.lineTo(cursorX, plot.top + plotHeight);
    ctx.stroke();
    let nearest = 0;
    for (let index = 1; index < times.length; index += 1) {
      if (Math.abs(times[index] - currentTime) < Math.abs(times[nearest] - currentTime)) nearest = index;
    }
    const pointX = x(times[nearest]);
    const pointY = y(values[nearest]);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(pointX, pointY, 3.5, 0, Math.PI * 2);
    ctx.fill();

    if (entry.liveValue) {
      const value = values[nearest];
      if (entry.metric.id === 'symmetry_index') {
        const si = Number(series.si?.[nearest]);
        entry.liveValue.textContent = `角差 ${fmt(value, 1)}°`;
        if (entry.liveExtra) entry.liveExtra.textContent = Number.isFinite(si) ? `· SI ${fmt(si, 0)}` : '';
      } else if (entry.metric.id === 'com_ap_drift') {
        entry.liveValue.textContent = `${value >= 0 ? '+' : ''}${fmt(value, 1)} cm`;
        if (entry.liveExtra) entry.liveExtra.textContent = '';
      } else {
        entry.liveValue.textContent = `${fmt(value, 1)}°`;
        if (entry.liveExtra) entry.liveExtra.textContent = '';
      }
    }
    ctx.restore();
  }

  function drawAll() {
    const currentTime = Number(media?.currentTime) || 0;
    for (const entry of entries) draw(entry, currentTime);
  }

  for (const entry of entries) {
    resize(entry);
    entry.canvas.addEventListener('click', (event) => {
      const rect = entry.canvas.getBoundingClientRect();
      const plotLeft = 43;
      const plotRight = 12;
      const ratio = clamp((event.clientX - rect.left - plotLeft) / Math.max(1, rect.width - plotLeft - plotRight), 0, 1);
      const target = ratio * duration;
      if (typeof seekTo === 'function') {
        seekTo(target, `查看过程指标 · ${target.toFixed(2)}s`);
      } else if (media) {
        media.currentTime = target;
      }
    });
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => { resize(entry); drawAll(); });
      observer.observe(entry.wrap);
    }
  }
  media?.addEventListener('timeupdate', drawAll);
  media?.addEventListener('seeked', drawAll);
  media?.addEventListener('loadedmetadata', drawAll);
  window.addEventListener('resize', drawAll);
  drawAll();
})();
"""


def build_report_html(
    feedback: dict[str, Any],
    *,
    media_url: str,
    frame_url_template: str,
    timeline: dict[str, Any] | None = None,
    motion: dict[str, Any] | None = None,
) -> str:
    clip_id = feedback["clip_id"]
    timeline = timeline or {"duration_s": 0.0, "reps": []}
    timeline_payload = {
        "duration_s": rounded(as_float(timeline.get("duration_s"))) or 0.0,
        "reps": [
            rep
            for rep in timeline.get("reps") or []
            if as_int(rep.get("rep_id")) is not None
        ],
        "evidence_intervals": [
            {
                "issue_id": str(card.get("issue_id") or "unknown"),
                "severity": str(card.get("severity") or "low"),
                "start_s": rounded((card.get("evidence_interval_s") or [0, 0])[0]) or 0.0,
                "end_s": rounded((card.get("evidence_interval_s") or [0, 0])[1]) or 0.0,
            }
            for card in feedback.get("cards") or []
            if len(card.get("evidence_interval_s") or []) >= 2
        ],
    }
    timeline_json = json.dumps(
        timeline_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    motion_json = json.dumps(
        motion or None,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    motion_script = MOTION_TRACE_SCRIPT.replace("__MOTION_DATA__", motion_json)
    process_script = PROCESS_METRICS_SCRIPT
    process_cards = build_process_metric_cards(motion)
    summary = feedback.get("assessment_summary") or {}
    quality = str(summary.get("quality_status") or "unknown")
    quality_label = {
        "pass": "质量通过",
        "warn": "质量有限",
        "limited": "质量有限",
        "reject": "质量拒答",
        "unknown": "质量未知",
    }.get(quality, quality)
    exercise_label = "深蹲" if feedback.get("exercise") == "squat" else "俯卧撑"
    view_label = {
        "front": "正面",
        "rear": "背面",
        "side_left": "左侧",
        "side_right": "右侧",
        "oblique_left": "左斜侧",
        "oblique_right": "右斜侧",
        "unknown": "未知机位",
    }.get(str(feedback.get("view") or "unknown"), str(feedback.get("view") or "unknown"))
    severity_label = {
        "high": "高优先级",
        "medium": "中优先级",
        "low": "提示",
    }
    issue_count = len(feedback.get("cards") or [])
    cards_html = []
    for card in feedback.get("cards") or []:
        image = None
        if card.get("frame_available") and card.get("frame_filename"):
            image = frame_url(frame_url_template, clip_id, card["frame_filename"])
        image_html = (
            f'<figure class="evidence-frame"><img src="{html.escape(image)}" '
            f'alt="动作问题证据帧"><figcaption>证据帧 · rep '
            f'{html.escape(str(card.get("representative_rep_id") or "-"))}</figcaption></figure>'
            if image
            else
            '<div class="evidence-empty">本次问题未生成可用证据帧。</div>'
        )
        start, end = card["evidence_interval_s"]
        confidence = round(float(card.get("confidence") or 0.0) * 100)
        highlight_attr = ",".join(
            str(index) for index in motion_joint_indices(card.get("highlight_joints") or [])
        )
        cards_html.append(
            f"""
            <article class="issue-card {html.escape(card['severity'])}"
              data-issue-id="{html.escape(str(card['issue_id']))}"
              data-anchor="{card['anchor_time_s']}" data-start="{start}" data-end="{end}"
              data-joints="{highlight_attr}">
              <div class="issue-head">
                <span class="severity">{html.escape(severity_label.get(card['severity'], card['severity']))}</span>
                <span class="candidate">研究候选</span>
                <span class="confidence">{confidence}% 置信</span>
              </div>
              <h3>{html.escape(card['title'])}</h3>
              <p class="detail">{html.escape(card['detail'])}</p>
              <div class="cue">
                <span>下一次这样做</span>
                <p>{html.escape(card['cue'])}</p>
              </div>
              <div class="issue-meta">
                <span>rep {html.escape(str(card['representative_rep_id'] or '-'))}</span>
                <span>{html.escape(card['frequency'])}</span>
                <span>{start:.2f}–{end:.2f}s</span>
                {'<span>锚点回退</span>' if card['anchor_fallback'] else ''}
              </div>
              {image_html}
              <div class="issue-footer">
                <p class="limit">限制：{html.escape(card['limitation'])}</p>
                <button type="button" class="play">播放证据区间</button>
              </div>
            </article>
            """
        )
    limitations = "".join(
        f"<li>{html.escape(item['message'])}</li>"
        for item in feedback.get("limitations") or []
    ) or "<li>本次没有额外限制项。</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#090b0a">
<title>{html.escape(clip_id)} · VigorSync 离线复盘</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='10' fill='%23070908'/%3E%3Cpath d='M13 47 27 17h10L23 47H13Zm19 0 14-30h10L42 47H32Z' fill='%23d9ff43'/%3E%3C/svg%3E">
<style>
:root{{--bg:#090b0a;--surface:#111510;--panel:#171c17;--ink:#f1f0e7;--muted:#9ea39a;--line:rgba(241,240,231,.16);--acid:#d9ff43;--cyan:#86e8ff;--signal:#ff644d;--amber:#f0c56b}}
*{{box-sizing:border-box}}html{{background:var(--bg)}}body{{margin:0;min-height:100vh;background:
linear-gradient(rgba(217,255,67,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(217,255,67,.035) 1px,transparent 1px),
radial-gradient(circle at 10% 0,rgba(134,232,255,.09),transparent 28%),radial-gradient(circle at 90% 18%,rgba(217,255,67,.07),transparent 26%),var(--bg);
background-size:54px 54px,54px 54px,auto,auto,auto;color:var(--ink);font-family:"Bahnschrift","Aptos","Microsoft YaHei",system-ui,sans-serif}}
.shell{{width:min(1240px,calc(100% - 40px));margin:auto;padding:28px 0 52px}}
.topbar{{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:28px;padding-bottom:18px;border-bottom:1px solid var(--line)}}
.brand{{display:flex;align-items:center;gap:13px}}.mark{{display:grid;place-items:center;width:50px;height:38px;border:1px solid rgba(217,255,67,.72);color:var(--acid);font-weight:900;transform:skewX(-7deg)}}
.brand-copy b{{display:block;letter-spacing:.18em;font-size:13px}}.brand-copy span{{display:block;margin-top:4px;color:var(--muted);font-size:10px;letter-spacing:.16em}}
.job-id{{color:var(--muted);font-family:ui-monospace,Consolas,monospace;font-size:11px;text-align:right;overflow-wrap:anywhere}}
.hero{{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(320px,.72fr);gap:18px;align-items:start}}
.visual,.snapshot,.motion-card,.issue-card,.limitations{{border:1px solid var(--line);background:linear-gradient(145deg,rgba(255,255,255,.035),transparent 46%),var(--surface)}}
.visual{{overflow:hidden}}.video-frame{{position:relative;background:#020302;min-height:300px}}video{{display:block;width:100%;aspect-ratio:4/3;max-height:68vh;object-fit:contain;background:#020302}}#skeleton-canvas{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1}}
.visual-foot{{display:flex;justify-content:space-between;gap:12px;padding:13px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}
.rep-timeline{{padding:15px 16px 12px;border-top:1px solid var(--line);background:linear-gradient(180deg,rgba(217,255,67,.025),transparent)}}
.timeline-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}}.timeline-title{{display:flex;align-items:center;gap:8px}}.timeline-kicker{{color:var(--acid);font-size:9px;font-weight:800;letter-spacing:.16em}}.timeline-state{{color:var(--muted);font-size:9px}}
.timeline-clock{{color:var(--muted);font-family:ui-monospace,Consolas,monospace;font-size:10px}}
.timeline-track{{position:relative;padding-top:4px;cursor:crosshair;touch-action:manipulation}}.rep-track{{position:relative;height:26px;border-bottom:1px solid var(--line)}}
.rep-segment{{position:absolute;top:3px;height:20px;min-width:3px;padding:0;border:0;border-radius:2px;background:#5f7c24;color:#071006;cursor:pointer;opacity:.76;transition:opacity .14s ease,outline-color .14s ease}}
.rep-segment:hover,.rep-segment.active{{opacity:1;outline:1px solid var(--ink)}}.rep-segment span{{position:absolute;inset:0;display:grid;place-items:center;font:800 9px/1 ui-monospace,Consolas,monospace;pointer-events:none}}
.rep-segment::after{{content:"";position:absolute;bottom:-4px;left:var(--bottom,50%);width:5px;height:5px;border-radius:50%;background:var(--ink);transform:translateX(-50%);pointer-events:none}}
.evidence-track{{position:relative;height:9px;margin-top:4px}}.evidence-segment{{position:absolute;top:2px;height:5px;min-width:3px;padding:0;border:0;border-radius:2px;background:var(--amber);cursor:pointer;opacity:.62;transition:opacity .14s ease,box-shadow .14s ease}}
.evidence-segment.high{{background:var(--signal)}}.evidence-segment.low{{background:var(--cyan)}}.evidence-segment:hover,.evidence-segment.active{{opacity:1;box-shadow:0 0 0 1px rgba(241,240,231,.8)}}
.timeline-cursor{{position:absolute;top:0;bottom:8px;left:0;width:1px;background:var(--ink);pointer-events:none}}.timeline-cursor::before{{content:"";position:absolute;top:-2px;left:-3px;border-right:3px solid transparent;border-left:3px solid transparent;border-top:5px solid var(--ink)}}
.timeline-axis{{position:relative;height:15px;margin-top:5px;color:var(--muted);font:9px/1 ui-monospace,Consolas,monospace}}.timeline-tick{{position:absolute;transform:translateX(-50%)}}
.timeline-empty{{padding:10px 0 2px;color:var(--muted);font-size:11px}}
.snapshot{{display:grid;grid-column:1/-1;grid-template-columns:minmax(190px,.55fr) minmax(0,1fr);gap:22px;align-items:center;margin-top:18px;padding:20px 22px}}.eyebrow{{margin:0 0 9px;color:var(--acid);font-size:10px;font-weight:800;letter-spacing:.16em}}
.snapshot h1{{margin:0;font-size:clamp(24px,3vw,36px);line-height:1.05;letter-spacing:-.04em}}
.snapshot h1 em{{display:block;margin-top:4px;color:transparent;font-style:normal;-webkit-text-stroke:1px var(--acid)}}
.status{{display:inline-flex;margin-top:12px;padding:6px 10px;border:1px solid rgba(240,197,107,.45);border-radius:999px;color:var(--amber);font-size:10px}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;margin:0;border:1px solid var(--line);background:var(--line)}}
.metric{{padding:13px 14px;background:var(--panel)}}.metric span{{display:block;color:var(--muted);font-size:10px}}.metric strong{{display:block;margin-top:6px;color:var(--acid);font-size:24px}}
.motion-card{{padding:18px;overflow:hidden}}.motion-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:13px}}.motion-head h2{{margin:0;font-size:22px}}.motion-time{{color:var(--cyan);font:10px/1.4 ui-monospace,Consolas,monospace;white-space:nowrap}}
.motion-stage{{position:relative;height:430px;overflow:hidden;border:1px solid var(--line);border-radius:8px;background:radial-gradient(circle at 50% 38%,rgba(134,232,255,.08),transparent 50%),linear-gradient(180deg,rgba(217,255,67,.035),transparent 70%),#080b09}}
.motion-stage canvas{{display:block;width:100%;height:100%;touch-action:none;cursor:grab}}.motion-stage canvas:active{{cursor:grabbing}}.motion-badge{{position:absolute;top:10px;left:10px;padding:4px 7px;border:1px solid rgba(134,232,255,.22);background:rgba(5,7,6,.66);color:var(--muted);font:9px/1.2 ui-monospace,Consolas,monospace;pointer-events:none}}
.motion-legend{{position:absolute;right:10px;top:10px;display:flex;gap:8px;align-items:center;padding:4px 7px;background:rgba(5,7,6,.66);border:1px solid var(--line);color:var(--muted);font-size:9px;pointer-events:none}}.motion-legend span{{display:inline-flex;align-items:center;gap:4px}}.motion-dot{{display:inline-block;width:6px;height:6px;border-radius:50}}.motion-dot.left{{background:var(--cyan)}}.motion-dot.right{{background:var(--acid)}}.motion-dash{{display:inline-block;width:12px;border-top:1px dashed rgba(241,240,231,.8)}}
.motion-empty{{position:absolute;inset:0;display:grid;place-items:center;padding:24px;background:rgba(5,7,6,.72);color:var(--muted);font-size:11px;text-align:center;pointer-events:none}}.motion-empty[hidden]{{display:none}}
.motion-controls{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:12px}}.motion-mode{{color:var(--muted);font-size:10px}}.motion-controls button{{padding:7px 10px;font-size:10px}}.motion-prediction{{margin-top:8px;padding:6px 8px;border-left:2px solid var(--amber);background:rgba(240,197,107,.07);color:var(--amber);font-size:10px;line-height:1.45}}.motion-prediction[hidden]{{display:none}}.motion-note{{margin:9px 0 0;color:var(--muted);font-size:9px}}
.process-card{{margin-top:18px;padding:20px 22px;border:1px solid var(--line);background:linear-gradient(145deg,rgba(255,255,255,.035),transparent 46%),var(--surface)}}
.process-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:6px}}.process-head h2{{margin:0;font-size:26px}}.process-policy{{padding:5px 9px;border:1px solid rgba(240,197,107,.36);border-radius:999px;color:var(--amber);font-size:9px;white-space:nowrap}}
.process-intro{{margin:0 0 14px;max-width:900px;color:var(--muted);font-size:11px;line-height:1.65}}.process-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
.process-metric{{min-width:0;padding:15px;border:1px solid var(--line);border-top:2px solid var(--cyan);background:var(--panel)}}.process-metric.attention{{border-top-color:var(--amber)}}.process-metric.abnormal{{border-top-color:var(--signal)}}.process-metric.unavailable{{border-top-color:var(--muted)}}
.process-metric-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}}.process-metric-head>div{{display:flex;align-items:baseline;gap:7px;min-width:0}}.process-index{{color:var(--muted);font:9px/1 ui-monospace,Consolas,monospace}}.process-metric h3{{margin:0;font-size:16px;line-height:1.25}}
.process-status{{padding:4px 7px;border-radius:999px;font-size:9px;font-weight:800;white-space:nowrap}}.process-status.normal{{background:rgba(134,232,255,.12);color:var(--cyan)}}.process-status.attention{{background:rgba(240,197,107,.12);color:var(--amber)}}.process-status.abnormal{{background:rgba(255,100,77,.14);color:var(--signal)}}.process-status.unavailable{{background:rgba(158,163,154,.1);color:var(--muted)}}
.process-reading{{display:flex;align-items:baseline;gap:5px;margin-top:14px}}.process-reading strong{{font-size:31px;line-height:1;color:var(--ink)}}.process-reading span{{color:var(--muted);font-size:10px}}.process-headline{{margin:10px 0 0;color:#d8dccf;font-size:11px;font-weight:700;line-height:1.45}}.process-detail{{min-height:32px;margin:5px 0 0;color:var(--muted);font-size:9px;line-height:1.5}}
.process-chart-wrap{{position:relative;height:190px;margin-top:11px;border:1px solid rgba(241,240,231,.08);background:#080b09}}.process-chart{{display:block;width:100%;height:100%;cursor:crosshair}}.process-chart-empty{{position:absolute;inset:0;display:grid;place-items:center;padding:14px;background:rgba(8,11,9,.88);color:var(--muted);font-size:10px;text-align:center;pointer-events:none}}.process-chart-empty[hidden]{{display:none}}
.process-live{{display:flex;justify-content:space-between;gap:8px;margin-top:8px;color:var(--muted);font:9px/1.45 ui-monospace,Consolas,monospace}}.process-live b{{color:var(--ink);font-weight:700}}.process-live em{{color:var(--cyan);font-style:normal}}.process-basis{{margin-top:10px;border-top:1px solid var(--line);padding-top:9px;color:var(--muted);font-size:9px;line-height:1.55}}.process-basis summary{{cursor:pointer;color:#c9cec3;font-weight:700}}.process-basis p{{margin:8px 0 0}}.process-basis b{{color:var(--ink)}}.process-view-note{{color:var(--amber)}}.process-empty{{padding:22px;border:1px dashed var(--line);color:var(--muted);font-size:11px}}.process-footnote{{margin:12px 0 0;color:var(--muted);font-size:9px;line-height:1.55}}
.banner{{margin:18px 0;padding:16px 18px;border:1px solid rgba(240,197,107,.35);border-left:3px solid var(--amber);background:rgba(240,197,107,.07);color:#e7d8ad;line-height:1.65}}
.banner small{{display:block;margin-top:7px;color:var(--muted)}}
.section-head{{display:flex;align-items:end;justify-content:space-between;gap:16px;margin:32px 0 14px}}
.section-head h2{{margin:0;font-size:24px}}.section-head span{{color:var(--muted);font-size:11px}}
.issues{{display:grid;gap:14px}}.issue-card{{cursor:pointer;padding:20px;border-left:4px solid var(--cyan);transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease}}.issue-card.active{{box-shadow:inset 0 0 0 1px rgba(241,240,231,.22)}}
.issue-card:hover{{transform:translateY(-2px)}}.issue-card.high{{border-left-color:var(--signal)}}.issue-card.medium{{border-left-color:var(--amber)}}
.issue-head{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}.severity,.candidate,.confidence{{padding:4px 8px;border-radius:999px;font-size:10px;font-weight:750}}
.severity{{background:rgba(255,100,77,.12);color:var(--signal)}}.candidate{{border:1px solid var(--line);color:var(--muted)}}.confidence{{margin-left:auto;color:var(--cyan)}}
.issue-card h3{{margin:13px 0 7px;font-size:24px}}.detail{{margin:0;color:#c6cbc2;line-height:1.65}}
.cue{{margin-top:15px;padding:13px 15px;border-left:2px solid var(--acid);background:rgba(217,255,67,.055)}}.cue span{{color:var(--acid);font-size:9px;font-weight:800;letter-spacing:.13em}}.cue p{{margin:5px 0 0;line-height:1.6}}
.issue-meta{{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px}}.issue-meta span{{padding:5px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:10px}}
.evidence-frame{{margin:16px 0 0;overflow:hidden;border:1px solid var(--line);background:#050706}}.evidence-frame img{{display:block;width:100%;max-height:430px;object-fit:contain}}
.evidence-frame figcaption{{padding:9px 12px;color:var(--muted);font-size:10px}}.evidence-empty{{margin-top:16px;padding:14px;border:1px dashed var(--line);color:var(--muted);font-size:11px}}
.issue-footer{{display:flex;align-items:end;justify-content:space-between;gap:14px;margin-top:14px}}.limit{{max-width:720px;margin:0;color:var(--muted);font-size:10px;line-height:1.55}}
button{{flex:0 0 auto;padding:9px 13px;border:1px solid rgba(134,232,255,.4);border-radius:3px;background:rgba(134,232,255,.08);color:var(--cyan);font-weight:750;cursor:pointer}}
.empty{{padding:22px;border:1px dashed var(--line);color:var(--muted)}}.limitations{{padding:20px 22px}}.limitations ul{{margin:0;padding-left:18px;color:var(--muted);line-height:1.8}}
@media(max-width:960px){{.shell{{width:min(100% - 24px,720px);padding-top:16px}}.topbar{{align-items:flex-start}}.job-id{{max-width:46vw}}.hero{{grid-template-columns:1fr}}.video-frame{{min-height:210px}}video{{max-height:none}}.rep-timeline{{padding:13px 12px 11px}}.snapshot{{grid-template-columns:1fr;padding:20px}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:4px}}.motion-stage{{height:430px}}.process-grid{{grid-template-columns:1fr}}.process-card{{padding:17px}}.section-head{{align-items:flex-start;flex-direction:column;gap:5px}}.issue-footer{{align-items:stretch;flex-direction:column}}.issue-footer button{{width:100%}}.confidence{{margin-left:0}}}}
@media(max-width:520px){{.topbar{{flex-direction:column;align-items:stretch;gap:12px}}.job-id{{width:100%;max-width:none;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}}}
@media(prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style></head><body><main class="shell">
<header class="topbar"><div class="brand"><span class="mark">V/S</span><div class="brand-copy"><b>VIGORSYNC</b><span>OFFLINE REVIEW · REPCHAIN</span></div></div><div class="job-id">{html.escape(clip_id)}</div></header>
<section class="hero"><div class="visual"><div class="video-frame"><video id="player" controls playsinline preload="metadata" src="{html.escape(media_url)}"></video><canvas id="skeleton-canvas" aria-hidden="true"></canvas></div><div class="visual-foot"><span>{exercise_label} · {view_label}</span><span id="watch-status">点击 Rep 或问题证据可定位</span></div>
<section class="rep-timeline" aria-label="Rep 分割时间轴"><div class="timeline-head"><div class="timeline-title"><span class="timeline-kicker">REP TIMELINE</span><span class="timeline-state" id="timeline-state">动作切分</span></div><span class="timeline-clock" id="timeline-clock">0.00s / {timeline_payload['duration_s']:.2f}s</span></div><div id="timeline-body"></div></section></div>
<section class="motion-card" aria-labelledby="motion-title">
<div class="motion-head"><div><p class="eyebrow">3D POSE REVIEW</p><h2 id="motion-title">3D 骨架</h2></div><span class="motion-time" id="motion-time">0.00s</span></div>
<div class="motion-stage" id="motion-stage">
<canvas id="motion-canvas" role="img" aria-label="脚底锚定的三维人体骨架和坐标系"></canvas>
<div class="motion-badge">脚底地面坐标 · Y=0 · 单位 m</div>
<div class="motion-legend"><span><i class="motion-dot left"></i>左侧</span><span><i class="motion-dot right"></i>右侧</span><span><i class="motion-dash"></i>遮挡预测</span></div>
<div class="motion-empty" id="motion-empty" hidden>本次没有可用的 3D 姿态数据</div>
</div>
<div class="motion-controls">
<span class="motion-mode">当前帧骨架 · 脚底锚定</span>
<button type="button" id="motion-reset">重置视角</button>
</div>
<div class="motion-prediction" id="motion-prediction" hidden></div>
<p class="motion-note">坐标：X 左右 · Y 垂直向上 · Z 前后 · Y=0 为脚底地面<br>虚线部分为遮挡期间的姿态预测<br>拖拽旋转 · 滚轮缩放 · 双击复位 · 骨架与视频、Rep 和问题证据同步</p>
</section>
</section>
<section class="process-card" aria-labelledby="process-title">
<div class="process-head"><div><p class="eyebrow">PROCESS METRICS</p><h2 id="process-title">过程指标</h2></div><span class="process-policy">工程阈值 · 非临床</span></div>
<p class="process-intro">基于已有 2D 关键点重建的 3D 轨迹，观察同一个 Rep 内的重心、躯干和左右差异。曲线与视频播放、Rep 时间轴同步，点击任一曲线可跳转到对应时刻。</p>
<div class="process-grid">{process_cards}</div>
<p class="process-footnote">曲线按约 12 Hz 抽样；遮挡段沿用骨架预测并降低置信度。阈值用于产品内复盘与趋势比较，并非临床诊断标准。</p>
</section>
<section class="snapshot"><div><p class="eyebrow">SESSION SNAPSHOT</p><h1>{exercise_label}<em>离线复盘</em></h1><span class="status">{quality_label}</span></div>
<div class="metrics"><div class="metric"><span>离线计数</span><strong>{summary.get('rep_count',0)}</strong></div><div class="metric"><span>问题</span><strong>{issue_count}</strong></div><div class="metric"><span>可评估指标</span><strong>{summary.get('evaluable_metric_count',0)}</strong></div><div class="metric"><span>质量</span><strong>{html.escape(quality.upper())}</strong></div></div>
</section>
<div class="banner">{html.escape(feedback['state_message'])}<small>研究候选结果 · Shadow-only · 不能替代教练或医学判断</small></div>
<div class="section-head"><h2>问题卡</h2><span>按严重度、持续性、置信度与可执行性排序</span></div>
<section class="issues">{''.join(cards_html) or '<div class="empty">本次没有达到展示门槛的可靠问题。</div>'}</section>
<div class="section-head"><h2>限制与未展示项</h2></div><section class="limitations"><ul>{limitations}</ul></section>
<script>
const player=document.getElementById('player');
const watchStatus=document.getElementById('watch-status');
const cards=[...document.querySelectorAll('.issue-card')];
const timeline={timeline_json};
const timelineBody=document.getElementById('timeline-body');
const timelineClock=document.getElementById('timeline-clock');
const timelineState=document.getElementById('timeline-state');
const duration=Number(timeline.duration_s)||0;
let timelineTrack=null;
let timelineCursor=null;
let repButtons=[];
let evidenceButtons=[];

function pct(value){{
  if(!duration)return 0;
  return Math.min(100,Math.max(0,(Number(value)||0)/duration*100));
}}

function syncTimeline(){{
  const now=Number(player.currentTime)||0;
  timelineClock.textContent=`${{now.toFixed(2)}}s / ${{duration.toFixed(2)}}s`;
  if(!timelineTrack)return;
  timelineCursor.style.left=`${{pct(now)}}%`;
  const activeRep=timeline.reps.find(rep=>now>=rep.start_s&&now<=rep.end_s);
  for(const button of repButtons){{
    button.classList.toggle('active',Number(button.dataset.repId)===activeRep?.rep_id);
  }}
  timelineState.textContent=activeRep?`REP ${{String(activeRep.rep_id).padStart(2,'0')}}`:'动作切分';
  const activeEvidence=new Set(
    timeline.evidence_intervals
      .filter(item=>now>=item.start_s&&now<=item.end_s)
      .map(item=>item.issue_id)
  );
  for(const button of evidenceButtons){{
    button.classList.toggle('active',activeEvidence.has(button.dataset.issueId));
  }}
  for(const card of cards){{
    card.classList.toggle('active',activeEvidence.has(card.dataset.issueId));
  }}
}}

function seekTo(value,label,autoplay=false){{
  const upper=Number(player.duration)||duration||value;
  player.currentTime=Math.min(Math.max(Number(value)||0,0),upper);
  if(label)watchStatus.textContent=label;
  syncTimeline();
  if(autoplay)player.play().catch(()=>{{}});
}}

function renderTimeline(){{
  if(!timeline.reps.length){{
    timelineBody.innerHTML='<div class="timeline-empty">本次未检测到可展示的 Rep 分割。</div>';
    timelineState.textContent='无动作切分';
    return;
  }}
  const repMarkup=timeline.reps.map((rep,index)=>{{
    const span=Math.max(rep.end_s-rep.start_s,0.001);
    const left=pct(rep.start_s);
    const width=Math.max(pct(rep.end_s)-left,0.35);
    const eccentric=Math.min(100,Math.max(0,(rep.bottom_s-rep.start_s)/span*100));
    return `<button type="button" class="rep-segment" data-index="${{index}}" data-rep-id="${{rep.rep_id}}" style="left:${{left}}%;width:${{width}}%;--bottom:${{eccentric}}%;background:linear-gradient(to right,#4d681b ${{eccentric}}%,#d9ff43 ${{eccentric}}%)" title="rep ${{rep.rep_id}} · ${{rep.start_s.toFixed(2)}}–${{rep.end_s.toFixed(2)}}s · 边界置信度 ${{(rep.confidence??0).toFixed(2)}}"><span>${{rep.rep_id}}</span></button>`;
  }}).join('');
  const evidenceMarkup=timeline.evidence_intervals.map((item,index)=>{{
    const severity=['high','medium','low'].includes(item.severity)?item.severity:'medium';
    const left=pct(item.start_s);
    return `<button type="button" class="evidence-segment ${{severity}}" data-index="${{index}}" style="left:${{left}}%;width:${{Math.max(pct(item.end_s)-left,0.35)}}%" title="问题证据 · ${{item.start_s.toFixed(2)}}–${{item.end_s.toFixed(2)}}s"></button>`;
  }}).join('');
  const step=duration>60?10:duration>20?5:2;
  let ticks='';
  for(let value=0;value<=duration+0.001;value+=step){{
    ticks+=`<span class="timeline-tick" style="left:${{pct(value)}}%">${{Math.round(value)}}s</span>`;
  }}
  timelineBody.innerHTML=`<div class="timeline-track" aria-label="点击时间轴跳转"><div class="rep-track">${{repMarkup}}</div><div class="evidence-track">${{evidenceMarkup}}</div><div class="timeline-cursor"></div></div><div class="timeline-axis">${{ticks}}</div>`;
  timelineTrack=timelineBody.querySelector('.timeline-track');
  timelineCursor=timelineBody.querySelector('.timeline-cursor');
  repButtons=[...timelineBody.querySelectorAll('.rep-segment')];
  evidenceButtons=[...timelineBody.querySelectorAll('.evidence-segment')];
  timelineTrack.addEventListener('click',event=>{{
    if(event.target.closest('.rep-segment,.evidence-segment'))return;
    const rect=timelineTrack.getBoundingClientRect();
    if(!rect.width)return;
    const ratio=Math.min(1,Math.max(0,(event.clientX-rect.left)/rect.width));
    seekTo(ratio*duration,`跳转到 ${{(ratio*duration).toFixed(2)}}s`);
  }});
  for(const button of repButtons){{
    button.addEventListener('click',()=>{{
      const rep=timeline.reps[Number(button.dataset.index)];
      seekTo(rep.start_s,`从 rep ${{rep.rep_id}} 开始播放`);
    }});
  }}
  for(const button of evidenceButtons){{
    button.addEventListener('click',()=>{{
      const item=timeline.evidence_intervals[Number(button.dataset.index)];
      seekTo(item.start_s,`查看问题证据 · ${{item.start_s.toFixed(2)}}s`);
    }});
  }}
  syncTimeline();
}}

for(const card of cards){{
  card.addEventListener('click',()=>{{
    const start=Number(card.dataset.start||card.dataset.anchor);
    seekTo(start,`正在查看 ${{start.toFixed(2)}}s 证据`,true);
  }});
}}

player.addEventListener('timeupdate',syncTimeline);
player.addEventListener('seeked',syncTimeline);
renderTimeline();
</script>
<script>
{motion_script}
</script>
<script>
{process_script}
</script>
</main></body></html>
"""


def clip_meta_for(clip_id: str, analysis_dir: Path) -> dict[str, Any]:
    path = analysis_dir / clip_id / "clip.json"
    return load_json(path) if path.is_file() else {}


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    templates = load_json(args.templates)
    rules = load_json(args.rules)
    taxonomy = load_json(args.taxonomy)
    contracts = load_json(args.contracts)
    schema = load_json(args.schema)
    generated_at = args.generated_at or utc_now()
    analyses = select_clip_ids(args, discover_analyses(args.analysis_dir))
    if not analyses:
        raise ValueError("no clip matched")

    clips = [
        runtime_clip(analysis, clip_meta_for(str(analysis["clip_id"]), args.analysis_dir))
        for analysis in analyses
    ]
    runtime_benchmark = write_runtime_benchmark(args.output_dir, clips, generated_at)
    summaries = []
    for analysis, clip in zip(analyses, clips):
        clip_id = str(clip["clip_id"])
        output = args.output_dir / clip_id
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"output exists (use --overwrite): {output}")
        if args.assessment_dir is not None:
            assessment = load_existing_assessment(args.assessment_dir, clip_id)
            if assessment is None:
                raise FileNotFoundError(f"assessment missing: {args.assessment_dir / clip_id}")
            runtime_adapter = False
        else:
            _, assessment = build_runtime_assessment(
                clip,
                args.analysis_dir,
                contracts,
                rules,
                taxonomy,
                runtime_benchmark,
                args.contracts,
                generated_at,
            )
            runtime_adapter = True
        benchmark_path = (assessment.get("source_measurement") or {}).get("input_contract", {}).get(
            "benchmark_path"
        )
        benchmark_sha = (assessment.get("source_measurement") or {}).get("input_contract", {}).get(
            "benchmark_sha256"
        )
        feedback = build_feedback(
            assessment,
            templates,
            runtime_adapter=runtime_adapter,
            benchmark_path=benchmark_path,
            benchmark_sha256=benchmark_sha,
            generated_at=generated_at,
        )
        clip_meta = clip_meta_for(clip_id, args.analysis_dir)
        frame_warnings: list[str] = []
        if args.render_frames:
            _, frame_warnings = render_card_frames(
                feedback,
                clip_meta,
                analysis,
                output / "frames",
                args.model,
            )
        feedback["limitations"].extend(
            {
                "code": "evidence_frame_fallback",
                "message": warning,
                "count": 1,
            }
            for warning in frame_warnings
        )
        feedback["cards"] = [
            card for card in feedback["cards"] if card.get("decision") in ACTIVE_DECISIONS
        ]
        feedback_errors = validate_schema(feedback, schema)
        invariant_errors = invariant_checks(feedback, assessment)
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "assessment_v2.json", assessment)
        write_json(output / "feedback_v2.json", feedback)
        write_json(
            output / "evidence.json",
            {
                "schema_version": "vls-feedback-evidence-v1",
                "clip_id": clip_id,
                "policy": templates.get("evidence", {}),
                "cards": [
                    {
                        key: card[key]
                        for key in (
                            "issue_id",
                            "anchor_frame",
                            "anchor_source_frame",
                            "anchor_time_s",
                            "evidence_interval_frames",
                            "evidence_interval_s",
                            "anchor_fallback",
                            "support_measurement_ids",
                            "frame_filename",
                            "frame_available",
                        )
                    }
                    for card in feedback["cards"]
                ],
            },
        )
        media_url = args.media_url_template.format(clip_id=clip_id)
        timeline_payload = build_report_timeline(assessment)
        motion_payload = build_motion_payload(args.analysis_dir, analysis, timeline_payload)
        (output / "report_v2.html").write_text(
            build_report_html(
                feedback,
                media_url=media_url,
                frame_url_template=args.frame_url_template,
                timeline=timeline_payload,
                motion=motion_payload,
            ),
            encoding="utf-8",
        )
        write_json(
            output / "checks" / "schema_validation.json",
            {"ok": not feedback_errors, "errors": feedback_errors},
        )
        write_json(
            output / "checks" / "invariants.json",
            {"ok": not invariant_errors, "errors": invariant_errors},
        )
        if feedback_errors or invariant_errors:
            raise ValueError(
                f"feedback validation failed for {clip_id}: "
                + "; ".join(feedback_errors + invariant_errors)
            )
        summaries.append(
            {
                "clip_id": clip_id,
                "exercise": feedback["exercise"],
                "view": feedback["view"],
                "state": feedback["state"],
                "card_count": len(feedback["cards"]),
                "candidate_cards": sum(card["decision"] == "candidate" for card in feedback["cards"]),
                "reportable_cards": sum(card["decision"] == "reportable" for card in feedback["cards"]),
                "evidence_frames": sum(card["frame_available"] for card in feedback["cards"]),
                "runtime_adapter": runtime_adapter,
            }
        )

    write_json(
        args.output_dir / "summary.json",
        {
            "schema_version": "vls-feedback-summary-v2",
            "component": "feedback_builder",
            "generated_at": generated_at,
            "clip_count": len(summaries),
            "state_counts": dict(sorted(Counter(item["state"] for item in summaries).items())),
            "card_count": sum(item["card_count"] for item in summaries),
            "results": summaries,
            "policy": {
                "shadow_only": True,
                "product_actions_enabled": False,
                "expert_calibrated": False,
            },
        },
    )
    artifact_paths = sorted(
        path
        for path in args.output_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "checksums.sha256"}
    )
    checksums = [f"{sha256_file(path)}  {path.relative_to(args.output_dir).as_posix()}" for path in artifact_paths]
    (args.output_dir / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    write_json(
        args.output_dir / "manifest.json",
        {
            "schema_version": "vls-feedback-manifest-v2",
            "component": "feedback_builder",
            "generated_at": generated_at,
            "clip_count": len(summaries),
            "artifacts": [
                {
                    "relative_path": path.relative_to(args.output_dir).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in artifact_paths
            ],
            "policy": {
                "shadow_only": True,
                "product_actions_enabled": False,
                "expert_calibrated": False,
            },
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
