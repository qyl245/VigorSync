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

from jsonschema import Draft202012Validator

import assessment_measurement as measurement_stage
import assessment_decision as decision_stage


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


def build_report_html(
    feedback: dict[str, Any],
    *,
    media_url: str,
    frame_url_template: str,
    timeline: dict[str, Any] | None = None,
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
        cards_html.append(
            f"""
            <article class="issue-card {html.escape(card['severity'])}"
              data-issue-id="{html.escape(str(card['issue_id']))}"
              data-anchor="{card['anchor_time_s']}" data-start="{start}" data-end="{end}">
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
.hero{{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(280px,.55fr);gap:18px;align-items:stretch}}
.visual,.snapshot,.issue-card,.limitations{{border:1px solid var(--line);background:linear-gradient(145deg,rgba(255,255,255,.035),transparent 46%),var(--surface)}}
.visual{{overflow:hidden}}.video-frame{{position:relative;background:#020302;min-height:300px}}
video{{display:block;width:100%;aspect-ratio:4/3;max-height:68vh;object-fit:contain;background:#020302}}
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
.snapshot{{padding:24px}}.eyebrow{{margin:0 0 9px;color:var(--acid);font-size:10px;font-weight:800;letter-spacing:.16em}}
.snapshot h1{{margin:0;font-size:clamp(28px,4vw,48px);line-height:1.05;letter-spacing:-.04em}}
.snapshot h1 em{{display:block;margin-top:5px;color:transparent;font-style:normal;-webkit-text-stroke:1px var(--acid)}}
.status{{display:inline-flex;margin-top:18px;padding:7px 11px;border:1px solid rgba(240,197,107,.45);border-radius:999px;color:var(--amber);font-size:11px}}
.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;margin-top:22px;border:1px solid var(--line);background:var(--line)}}
.metric{{padding:16px;background:var(--panel)}}.metric span{{display:block;color:var(--muted);font-size:10px}}.metric strong{{display:block;margin-top:7px;color:var(--acid);font-size:28px}}
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
@media(max-width:820px){{.shell{{width:min(100% - 24px,720px);padding-top:16px}}.topbar{{align-items:flex-start}}.job-id{{max-width:46vw}}.hero{{grid-template-columns:1fr}}.video-frame{{min-height:210px}}video{{max-height:none}}.rep-timeline{{padding:13px 12px 11px}}.snapshot{{padding:20px}}.section-head{{align-items:flex-start;flex-direction:column;gap:5px}}.issue-footer{{align-items:stretch;flex-direction:column}}.issue-footer button{{width:100%}}.confidence{{margin-left:0}}}}
@media(max-width:520px){{.topbar{{flex-direction:column;align-items:stretch;gap:12px}}.job-id{{width:100%;max-width:none;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}}}
@media(prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style></head><body><main class="shell">
<header class="topbar"><div class="brand"><span class="mark">V/S</span><div class="brand-copy"><b>VIGORSYNC</b><span>OFFLINE REVIEW · REPCHAIN</span></div></div><div class="job-id">{html.escape(clip_id)}</div></header>
<section class="hero"><div class="visual"><div class="video-frame"><video id="player" controls playsinline preload="metadata" src="{html.escape(media_url)}"></video></div><div class="visual-foot"><span>{exercise_label} · {view_label}</span><span id="watch-status">点击 Rep 或问题证据可定位</span></div>
<section class="rep-timeline" aria-label="Rep 分割时间轴"><div class="timeline-head"><div class="timeline-title"><span class="timeline-kicker">REP TIMELINE</span><span class="timeline-state" id="timeline-state">动作切分</span></div><span class="timeline-clock" id="timeline-clock">0.00s / {timeline_payload['duration_s']:.2f}s</span></div><div id="timeline-body"></div></section></div>
<aside class="snapshot"><p class="eyebrow">SESSION SNAPSHOT</p><h1>{exercise_label}<em>离线复盘</em></h1><span class="status">{quality_label}</span>
<div class="metrics"><div class="metric"><span>离线计数</span><strong>{summary.get('rep_count',0)}</strong></div><div class="metric"><span>问题</span><strong>{issue_count}</strong></div><div class="metric"><span>可评估指标</span><strong>{summary.get('evaluable_metric_count',0)}</strong></div><div class="metric"><span>质量</span><strong>{html.escape(quality.upper())}</strong></div></div>
</aside></section>
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
        (output / "report_v2.html").write_text(
            build_report_html(
                feedback,
                media_url=media_url,
                frame_url_template=args.frame_url_template,
                timeline=build_report_timeline(assessment),
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
