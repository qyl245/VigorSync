#!/usr/bin/env python3
"""Build a trajectory-comparison reference template from an analyzed clip.

The template is the medoid rep of an expert video: the rep whose
time-normalized, amplitude-normalized angle curve is closest to every other
qualified rep. No score or threshold is produced here; the template is only
used to overlay user reps against a single expert curve later.

Usage:
    python scripts/build_reference_template.py \
        --analysis-dir reference_data/analysis/pushup_reference_side

For a reference clip holding a single clean cycle, pass --rep-range START:END;
consistency is then reported as unavailable instead of failing.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA_VERSION = "vigorsync-trajectory-template-v1"
RESAMPLE_POINTS = 101
MIN_CONFIDENCE = 0.80
MIN_VISIBILITY = 0.80
MIN_COVERAGE = 0.90
MIN_RANGE_DEG = 10.0
MAX_CYCLE_LEVEL_GAP = 0.20
MIN_REPS_FOR_CONFIDENCE = 3
MAX_MEDIAN_DISTANCE = 0.15
MAX_DISPERSION_RATIO = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--angles", nargs="*", default=None)
    parser.add_argument("--exercise", default=None)
    parser.add_argument("--view", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--rep-range",
        default=None,
        help=(
            "Use a single explicit START:END frame range as the only rep. "
            "For reference clips that contain one clean cycle; consistency "
            "is then reported as unavailable."
        ),
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip the consistency overlay PNG.",
    )
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_angle_series(path: Path, names: list[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rows = read_csv_rows(path)
    frame_ids = np.asarray([int(row["frame_id"]) for row in rows], dtype=np.int64)
    series: dict[str, np.ndarray] = {}
    for name in names:
        if name not in rows[0]:
            raise KeyError(f"angle column not found: {name}")
        series[name] = np.asarray(
            [float(row[name]) if row[name] not in {"", "nan"} else np.nan for row in rows],
            dtype=np.float64,
        )
    return frame_ids, series


def template_view(view: str) -> str:
    """Side-left and side-right recordings share one mirrored standard."""
    view = (view or "").strip().lower()
    return "side" if view in {"side_left", "side_right"} else view


def default_angles(analysis: dict) -> list[str]:
    selection = (analysis.get("segmentation") or {}).get("signal_selection") or {}
    selected = selection.get("selected_angle")
    if selected:
        return [selected]
    view = str(analysis.get("view", ""))
    if view == "side_left":
        return ["left_elbow_flexion"]
    if view == "side_right":
        return ["right_elbow_flexion"]
    raise ValueError("cannot infer angle; pass --angles explicitly")


def crop_rep(
    frame_ids: np.ndarray,
    series: dict[str, np.ndarray],
    start_frame: int,
    end_frame: int,
) -> dict[str, np.ndarray]:
    mask = (frame_ids >= start_frame) & (frame_ids <= end_frame)
    return {name: values[mask] for name, values in series.items()}


def normalize_curve(values: np.ndarray) -> np.ndarray | None:
    if not np.all(np.isfinite(values)):
        return None
    low = float(values.min())
    high = float(values.max())
    if high - low < MIN_RANGE_DEG:
        return None
    return (values - low) / (high - low)


def resample(values: np.ndarray, points: int = RESAMPLE_POINTS) -> np.ndarray:
    source = np.linspace(0.0, 1.0, num=len(values))
    target = np.linspace(0.0, 1.0, num=points)
    return np.interp(target, source, values)


def rms_distance(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.sqrt(np.mean((first - second) ** 2)))


def landmark_progress(curve: np.ndarray) -> dict[str, float]:
    """Locate the extended (max angle) and flexed (min angle) points on the progress axis."""
    return {
        "extended_progress_pct": round(float(np.argmax(curve) / (len(curve) - 1) * 100.0), 2),
        "flexed_progress_pct": round(float(np.argmin(curve) / (len(curve) - 1) * 100.0), 2),
    }


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
        # A clip-start rep is complete only if it already starts near the extended top.
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


def parse_rep_range(value: str, frame_count: int) -> tuple[int, int]:
    try:
        start_text, end_text = value.split(":", 1)
        start_frame, end_frame = int(start_text), int(end_text)
    except ValueError as exc:
        raise ValueError(f"invalid --rep-range: {value}") from exc
    if start_frame < 0 or end_frame <= start_frame or end_frame > frame_count - 1:
        raise ValueError(
            f"--rep-range {value} is outside the clip (0:{frame_count - 1})"
        )
    return start_frame, end_frame


def build_template(
    analysis_dir: Path,
    angles: list[str] | None,
    exercise: str | None,
    view: str | None,
    rep_range: str | None = None,
) -> dict:
    analysis = json.loads((analysis_dir / "analysis.json").read_text(encoding="utf-8"))
    angles = angles or default_angles(analysis)
    exercise = exercise or str(analysis["exercise"])
    analysis_view = view or str(analysis["view"])
    view = template_view(analysis_view)

    frame_ids, series = load_angle_series(analysis_dir / "angles" / "angles_wide.csv", angles)
    frame_count = int(analysis.get("frame_count") or len(frame_ids))
    reps = read_csv_rows(analysis_dir / "reps.csv")
    if rep_range:
        start_frame, end_frame = parse_rep_range(rep_range, frame_count)
        fps = float(analysis.get("fps") or 0.0)
        reps = [
            {
                "rep_id": "1",
                "start_frame": str(start_frame),
                "end_frame": str(end_frame),
                "duration_s": f"{(end_frame - start_frame) / fps:.6f}" if fps else "0",
                "confidence": "1.0",
                "visibility": "1.0",
                "status": "complete",
            }
        ]

    primary = series[angles[0]]
    finite = primary[np.isfinite(primary)]
    p5, p95 = (
        (float(np.percentile(finite, 5.0)), float(np.percentile(finite, 95.0)))
        if finite.size
        else (0.0, 0.0)
    )

    included: list[dict] = []
    excluded: list[dict] = []
    for rep in reps:
        rep_id = int(rep["rep_id"])
        ok, reason = qualify_rep(rep, frame_count, primary, p5, p95)
        if not ok:
            excluded.append({"rep_id": rep_id, "reason": reason})
            continue

        cropped = crop_rep(
            frame_ids, series, int(rep["start_frame"]), int(rep["end_frame"])
        )
        curves: dict[str, np.ndarray] = {}
        fail_reason = None
        for name in angles:
            values = cropped[name]
            finite_ratio = float(np.isfinite(values).mean()) if values.size else 0.0
            if finite_ratio < MIN_COVERAGE:
                fail_reason = f"{name}:insufficient_coverage"
                break
            normalized = normalize_curve(values)
            if normalized is None:
                fail_reason = f"{name}:no_dynamic_range"
                break
            if abs(float(normalized[-1]) - float(normalized[0])) > MAX_CYCLE_LEVEL_GAP:
                fail_reason = f"{name}:incomplete_cycle"
                break
            curves[name] = resample(normalized)
        if fail_reason:
            excluded.append({"rep_id": rep_id, "reason": fail_reason})
            continue

        included.append(
            {
                "rep_id": rep_id,
                "start_frame": int(rep["start_frame"]),
                "end_frame": int(rep["end_frame"]),
                "duration_s": float(rep["duration_s"]),
                "confidence": float(rep["confidence"]),
                "visibility": float(rep["visibility"]),
                "curves": curves,
                "raw_deg": {name: cropped[name] for name in angles},
            }
        )

    if not included:
        raise RuntimeError(f"need at least 1 qualified rep, got 0: {excluded}")

    count = len(included)
    distances = np.zeros((count, count), dtype=np.float64)
    for i in range(count):
        for j in range(i + 1, count):
            value = float(
                np.mean(
                    [
                        rms_distance(included[i]["curves"][name], included[j]["curves"][name])
                        for name in angles
                    ]
                )
            )
            distances[i, j] = value
            distances[j, i] = value

    if count >= 2:
        mean_to_others = distances.sum(axis=1) / (count - 1)
        medoid_index = int(np.argmin(mean_to_others))
        off_diagonal = distances[np.triu_indices(count, k=1)]
        median_distance = float(np.median(off_diagonal))
        max_distance = float(off_diagonal.max())
        dispersion = (
            max_distance / median_distance if median_distance > 1e-9 else float("inf")
        )
        stable = median_distance <= MAX_MEDIAN_DISTANCE and dispersion <= MAX_DISPERSION_RATIO
    else:
        # A single-rep reference has nothing to compare against.
        mean_to_others = np.zeros(1, dtype=np.float64)
        medoid_index = 0
        median_distance = None
        max_distance = None
        dispersion = None
        stable = False
    medoid = included[medoid_index]

    progress = np.linspace(0.0, 100.0, num=RESAMPLE_POINTS).round(4).tolist()
    angle_meta_full = json.loads(
        (analysis_dir / "angles" / "meta.json").read_text(encoding="utf-8")
    )

    template = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exercise": exercise,
        "view": view,
        "angles": angles,
        "source": {
            "clip_id": analysis.get("clip_id"),
            "analysis_view": analysis_view,
            "analysis_dir": str(analysis_dir.resolve()),
            "fps": analysis.get("fps"),
            "frame_count": frame_count,
            "medoid_rep_id": medoid["rep_id"],
            "medoid_start_frame": medoid["start_frame"],
            "medoid_end_frame": medoid["end_frame"],
            "medoid_duration_s": medoid["duration_s"],
        },
        "processing": {
            "normalization": "per_rep_per_angle_min_max",
            "resample": "linear_time",
            "resample_points": RESAMPLE_POINTS,
            "progress_unit": "percent_of_rep",
            "smoothing": angle_meta_full.get("smoothing"),
            "angle_schema_version": angle_meta_full.get("schema_version"),
            "visibility_threshold": angle_meta_full.get("visibility_threshold"),
        },
        "template": {
            "progress": progress,
            "normalized": {
                name: medoid["curves"][name].round(6).tolist() for name in angles
            },
            "raw_deg": {
                name: medoid["raw_deg"][name].round(4).tolist() for name in angles
            },
            "landmarks": {
                name: landmark_progress(medoid["curves"][name]) for name in angles
            },
        },
        "consistency": {
            "included_rep_count": count,
            "included_rep_ids": [item["rep_id"] for item in included],
            "excluded_reps": excluded,
            "medoid_rep_id": medoid["rep_id"],
            "pairwise_distance": {
                "labels": [f"rep_{item['rep_id']}" for item in included],
                "matrix": distances.round(6).tolist(),
            },
            "mean_distance_to_others": {
                f"rep_{included[i]['rep_id']}": round(float(mean_to_others[i]), 6)
                for i in range(count)
            },
            "median_pairwise_distance": None if median_distance is None else round(median_distance, 6),
            "max_pairwise_distance": None if max_distance is None else round(max_distance, 6),
            "dispersion_ratio": None if dispersion is None else round(dispersion, 4),
            "thresholds": {
                "max_median_distance": MAX_MEDIAN_DISTANCE,
                "max_dispersion_ratio": MAX_DISPERSION_RATIO,
            },
            "stable": bool(stable),
            "low_confidence": count < MIN_REPS_FOR_CONFIDENCE,
        },
    }
    template["_curves"] = {item["rep_id"]: item["curves"] for item in included}
    return template


def write_plot(template: dict, curves: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    angles = template["angles"]
    medoid_id = template["source"]["medoid_rep_id"]
    progress = template["template"]["progress"]

    fig, axes = plt.subplots(
        1, len(angles), figsize=(6 * len(angles), 4.2), squeeze=False
    )
    for axis, name in zip(axes[0], angles):
        for rep_id, rep_curves in curves.items():
            axis.plot(
                progress,
                rep_curves[name],
                linewidth=1.2,
                alpha=1.0 if rep_id == medoid_id else 0.35,
                label=f"rep {rep_id}" + (" (medoid)" if rep_id == medoid_id else ""),
            )
        axis.set_title(name)
        axis.set_xlabel("rep progress (%)")
        axis.set_ylabel("normalized angle")
        axis.grid(alpha=0.25)
        axis.legend(fontsize=7)
    fig.suptitle(
        f"{template['exercise']} / {template['view']} consistency "
        f"(stable={template['consistency']['stable']})"
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    analysis_dir = args.analysis_dir.resolve()
    template = build_template(
        analysis_dir, args.angles, args.exercise, args.view, args.rep_range
    )
    curves = template.pop("_curves")

    output = args.output
    if output is None:
        output = (
            Path("reference_data")
            / "templates"
            / f"{template['exercise']}_{template['view']}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)

    if not args.no_plot:
        plot_path = output.with_name(output.stem + "_consistency.png")
        write_plot(template, curves, plot_path)

    output.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    consistency = template["consistency"]
    print(
        json.dumps(
            {
                "template": str(output.resolve()),
                "angles": template["angles"],
                "included_reps": consistency["included_rep_ids"],
                "excluded_reps": consistency["excluded_reps"],
                "medoid_rep_id": consistency["medoid_rep_id"],
                "median_pairwise_distance": consistency["median_pairwise_distance"],
                "dispersion_ratio": consistency["dispersion_ratio"],
                "stable": consistency["stable"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
