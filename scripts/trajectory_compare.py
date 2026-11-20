#!/usr/bin/env python3
"""Overlay user reps on the expert trajectory template.

Shape comparison only: no score, no threshold, no issue classification.
Every qualified user rep is banded-DTW aligned to the template and exported in
absolute joint degrees over the single expert curve. The result is a JSON
payload consumed by the offline report canvas; no image is rendered here.

Usage:
    python scripts/trajectory_compare.py \
        --template reference_data/templates/pushup_side.json \
        --analysis-dir reference_data/test/analysis/pushup_test_side \
        --output-dir reference_data/test/overlay
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

RESAMPLE_POINTS = 101
MIN_CONFIDENCE = 0.80
MIN_VISIBILITY = 0.80
MIN_COVERAGE = 0.90
MIN_RANGE_DEG = 10.0
MAX_CYCLE_LEVEL_GAP = 0.20
DTW_BAND_RATIO = 0.20
TRIM_LEVEL = 0.90
MIN_TRIM_POINTS = 5
SIDE_PREFIXES = ("left_", "right_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def angle_columns(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def expand_bilateral_candidates(names: list[str], columns: list[str]) -> list[str]:
    """RepChain v3 may select a synthetic bilateral-mean signal with no column.

    Fall back to the per-side columns that make up that mean so the template's
    left/right joint can still be matched.
    """
    expanded: list[str] = []
    suffix = "_bilateral_mean"
    for name in names:
        if name.endswith(suffix) and name not in columns:
            base = name[: -len(suffix)]
            for prefix in SIDE_PREFIXES:
                candidate = f"{prefix}{base}"
                if candidate in columns:
                    expanded.append(candidate)
        expanded.append(name)
    return expanded


def hold_trim_bounds(normalized: np.ndarray) -> tuple[int, int]:
    """Trim the standing hold: keep top-band exit through top-band re-entry."""
    below = np.nonzero(normalized < TRIM_LEVEL)[0]
    if below.size == 0:
        return 0, len(normalized) - 1
    start, end = int(below[0]), int(below[-1])
    if end - start < MIN_TRIM_POINTS:
        return 0, len(normalized) - 1
    return start, end


def canonical_angle(name: str) -> str:
    for prefix in SIDE_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def view_family(view: str | None) -> str:
    """Side-left and side-right recordings of the same movement are comparable."""
    view = (view or "").strip().lower()
    return "side" if view.startswith("side_") else view


def load_series(path: Path, names: list[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rows = read_csv_rows(path)
    frame_ids = np.asarray([int(row["frame_id"]) for row in rows], dtype=np.int64)
    series = {
        name: np.asarray(
            [float(row[name]) if row[name] not in {"", "nan"} else np.nan for row in rows],
            dtype=np.float64,
        )
        for name in names
    }
    return frame_ids, series


def boundary_level(value: float, p5: float, p95: float) -> float:
    span = p95 - p5
    if span <= 1e-6 or not np.isfinite(value):
        return 0.0
    return float((value - p5) / span)


def qualify_rep(
    rep: dict[str, str],
    frame_count: int,
    signal: np.ndarray,
    p5: float,
    p95: float,
) -> tuple[bool, str | None]:
    if rep.get("status") != "complete":
        return False, "status_not_complete"
    start_frame = int(rep["start_frame"])
    end_frame = int(rep["end_frame"])
    if start_frame <= 0:
        if boundary_level(float(signal[start_frame]), p5, p95) < 0.90:
            return False, "truncated_at_clip_start"
    if end_frame >= frame_count - 1:
        if boundary_level(float(signal[end_frame]), p5, p95) < 0.90:
            return False, "truncated_at_clip_end"
    if float(rep.get("confidence") or 0.0) < MIN_CONFIDENCE:
        return False, "low_confidence"
    if float(rep.get("visibility") or 0.0) < MIN_VISIBILITY:
        return False, "low_visibility"
    return True, None


def normalize(values: np.ndarray) -> np.ndarray | None:
    if not np.all(np.isfinite(values)):
        return None
    low, high = float(values.min()), float(values.max())
    if high - low < MIN_RANGE_DEG:
        return None
    return (values - low) / (high - low)


def resample(values: np.ndarray) -> np.ndarray:
    source = np.linspace(0.0, 1.0, num=len(values))
    target = np.linspace(0.0, 1.0, num=RESAMPLE_POINTS)
    return np.interp(target, source, values)


def dtw_path(
    query: np.ndarray,
    reference: np.ndarray,
    band_ratio: float = DTW_BAND_RATIO,
) -> tuple[list[tuple[int, int]] | None, bool | None]:
    """Return the banded DTW alignment path from query indices to reference indices.

    The band keeps the warp from flattening real timing differences; points where
    the path rides the band edge are reported so the caller can flag them.
    """
    count, ref_count = len(query), len(reference)
    band = max(1, int(round(max(count, ref_count) * band_ratio)))
    inf = float("inf")
    cost = np.full((count + 1, ref_count + 1), inf)
    cost[0, 0] = 0.0
    for i in range(1, count + 1):
        for j in range(max(1, i - band), min(ref_count, i + band) + 1):
            cost[i, j] = abs(query[i - 1] - reference[j - 1]) + min(
                cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1]
            )
    if not np.isfinite(cost[count, ref_count]):
        return None, None

    pairs: list[tuple[int, int]] = []
    i, j = count, ref_count
    while i > 0 and j > 0:
        pairs.append((i - 1, j - 1))
        options = (cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1])
        move = int(np.argmin(options))
        if move == 0:
            i, j = i - 1, j - 1
        elif move == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    band_hit = any(abs(query_index - ref_index) >= band for query_index, ref_index in pairs)
    return pairs, band_hit


def apply_path(
    values: np.ndarray,
    pairs: list[tuple[int, int]],
    ref_count: int,
) -> np.ndarray:
    buckets: list[list[float]] = [[] for _ in range(ref_count)]
    for query_index, ref_index in pairs:
        buckets[ref_index].append(float(values[query_index]))
    warped = np.asarray(
        [np.mean(bucket) if bucket else np.nan for bucket in buckets], dtype=np.float64
    )
    if np.isnan(warped).any():
        axis = np.arange(ref_count)
        valid = ~np.isnan(warped)
        warped = np.interp(axis, axis[valid], warped[valid])
    return warped


def build_comparison(
    template: dict,
    analysis_dir: Path,
) -> dict:
    analysis = json.loads((analysis_dir / "analysis.json").read_text(encoding="utf-8"))
    template_view = template["view"]
    user_view = analysis.get("view")
    if view_family(template_view) != view_family(user_view):
        raise RuntimeError(
            f"view mismatch: template={template_view}, user={user_view}; "
            "absolute angles are not comparable across viewpoints"
        )
    segmentation = analysis.get("segmentation") or {}
    selection = segmentation.get("signal_selection") or {}
    template_angles = template["angles"]
    candidates = [
        selection.get("selected_angle"),
        segmentation.get("phase_angle"),
        segmentation.get("angle"),
    ]
    candidates = [name for name in dict.fromkeys(candidates) if name]
    if not candidates:
        raise RuntimeError("analysis has no usable angle")
    candidates = expand_bilateral_candidates(
        candidates, angle_columns(analysis_dir / "angles" / "angles_wide.csv")
    )

    # Prefer the same joint name as the template; fall back to the same joint role.
    user_angle = next((name for name in candidates if name in template_angles), None)
    if user_angle is not None:
        matched_template_angle = user_angle
    else:
        by_canonical = {canonical_angle(name): name for name in template_angles}
        user_angle = next(
            (name for name in candidates if canonical_angle(name) in by_canonical),
            None,
        )
        matched_template_angle = (
            by_canonical.get(canonical_angle(user_angle)) if user_angle else None
        )
    if user_angle is None or matched_template_angle is None:
        raise RuntimeError(
            f"no template angle matches user angles {candidates}: {template_angles}"
        )

    user_side = user_angle.split("_", 1)[0]
    template_side = matched_template_angle.split("_", 1)[0]

    frame_ids, series = load_series(
        analysis_dir / "angles" / "angles_wide.csv", [user_angle]
    )
    frame_count = int(analysis.get("frame_count") or len(frame_ids))
    reps = read_csv_rows(analysis_dir / "reps.csv")

    template_normalized = np.asarray(
        template["template"]["normalized"][matched_template_angle], dtype=np.float64
    )
    template_deg = resample(
        np.asarray(
            template["template"]["raw_deg"][matched_template_angle], dtype=np.float64
        )
    )
    trim_start, trim_end = hold_trim_bounds(template_normalized)
    template_normalized = resample(template_normalized[trim_start : trim_end + 1])
    template_deg = resample(template_deg[trim_start : trim_end + 1])

    user_signal = series[user_angle]
    finite_values = user_signal[np.isfinite(user_signal)]
    p5, p95 = (
        (float(np.percentile(finite_values, 5.0)), float(np.percentile(finite_values, 95.0)))
        if finite_values.size
        else (0.0, 0.0)
    )

    curves: list[dict] = []
    excluded: list[dict] = []
    band_hit_reps: list[int] = []
    for rep in reps:
        rep_id = int(rep["rep_id"])
        ok, reason = qualify_rep(rep, frame_count, user_signal, p5, p95)
        if not ok:
            excluded.append({"rep_id": rep_id, "reason": reason})
            continue
        mask = (frame_ids >= int(rep["start_frame"])) & (
            frame_ids <= int(rep["end_frame"])
        )
        values = series[user_angle][mask]
        if values.size == 0 or float(np.isfinite(values).mean()) < MIN_COVERAGE:
            excluded.append({"rep_id": rep_id, "reason": "insufficient_coverage"})
            continue
        normalized = normalize(values)
        if normalized is None:
            excluded.append({"rep_id": rep_id, "reason": "no_dynamic_range"})
            continue
        if abs(float(normalized[-1]) - float(normalized[0])) > MAX_CYCLE_LEVEL_GAP:
            excluded.append({"rep_id": rep_id, "reason": "incomplete_cycle"})
            continue
        trim_start, trim_end = hold_trim_bounds(normalized)
        trimmed_values = values[trim_start : trim_end + 1]
        trimmed_norm = normalize(trimmed_values)
        if trimmed_norm is None:
            excluded.append({"rep_id": rep_id, "reason": "no_dynamic_range"})
            continue
        curve_norm = resample(trimmed_norm)
        curve_deg = resample(trimmed_values)
        # Warp on shape so amplitude differences do not steer the path,
        # then apply that path to the absolute-angle curve that gets plotted.
        path, band_hit = dtw_path(curve_norm, template_normalized)
        if path is None:
            excluded.append({"rep_id": rep_id, "reason": "dtw_infeasible"})
            continue
        if band_hit:
            band_hit_reps.append(rep_id)
        curve = apply_path(curve_deg, path, len(template_deg))
        curves.append(
            {
                "rep_id": rep_id,
                "duration_s": float(rep["duration_s"]),
                "curve": curve.round(6).tolist(),
            }
        )

    if not curves:
        raise RuntimeError(f"no qualified reps: {excluded}")

    flexed_index = int(np.argmin(template_deg))
    return {
        "schema_version": "vigorsync-trajectory-comparison-v1",
        "exercise": template["exercise"],
        "view": {"template": template["view"], "user": analysis.get("view")},
        "angles": {"template": matched_template_angle, "user": user_angle},
        "side": {
            "template": template_side,
            "user": user_side,
            "crossed": user_side != template_side and {user_side, template_side} == {"left", "right"},
        },
        "units": "degrees",
        "align": "dtw",
        "dtw_band_ratio": DTW_BAND_RATIO,
        "dtw_band_hit_reps": band_hit_reps,
        "progress": np.linspace(0.0, 100.0, num=RESAMPLE_POINTS).round(4).tolist(),
        "template_curve": template_deg.round(6).tolist(),
        "template_flexed_progress_pct": round(
            flexed_index / (RESAMPLE_POINTS - 1) * 100.0, 2
        ),
        "user_rep_count": len(curves),
        "user_reps": curves,
        "excluded_reps": excluded,
    }


def main() -> int:
    args = parse_args()
    template = json.loads(args.template.read_text(encoding="utf-8"))
    try:
        comparison = build_comparison(template, args.analysis_dir.resolve())
    except RuntimeError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    output_dir = args.output_dir or (args.analysis_dir.parent / "overlay")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "overlay.json"

    json_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "data": str(json_path.resolve()),
                "align": comparison["align"],
                "user_rep_count": comparison["user_rep_count"],
                "excluded_reps": comparison["excluded_reps"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
