"""Derive interpretable process metrics from stabilized 3D pose tracks."""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np


SCHEMA_VERSION = "vls-process-metrics-v1"

STATUS_LABELS = {
    "normal": "正常",
    "attention": "关注",
    "abnormal": "异常",
    "unavailable": "数据不足",
}
STATUS_RANK = {"normal": 0, "attention": 1, "abnormal": 2, "unavailable": -1}
TREND_ONLY_LABEL = "仅趋势"
SIDE_VIEWS = {"side_left", "side_right", "oblique_left", "oblique_right"}
FRONTAL_VIEWS = {"front", "rear"}

POLICY = {
    "name": "provisional_engineering_process_metrics_v1",
    "expert_calibrated": False,
    "clinical_cutoff": False,
    "description": "过程指标用于复盘趋势和定位偏差，不替代教练、医学判断或现有问题卡判定。",
}

COM_SEGMENTS: tuple[tuple[tuple[int, ...], float], ...] = (
    ((0,), 0.081),
    ((11, 12, 23, 24), 0.497),
    ((13,), 0.028),
    ((14,), 0.028),
    ((15,), 0.016),
    ((16,), 0.016),
    ((15,), 0.006),
    ((16,), 0.006),
    ((25,), 0.100),
    ((26,), 0.100),
    ((27,), 0.0465),
    ((28,), 0.0465),
    ((27, 29, 31), 0.0145),
    ((28, 30, 32), 0.0145),
)
COM_TOTAL_WEIGHT = sum(weight for _, weight in COM_SEGMENTS)
CORE_BODY_JOINTS = (11, 12, 23, 24, 27, 28)

COM_WARN_RATIO = 0.04
COM_ALARM_RATIO = 0.07

TORSO_CONFIG = {
    "squat": {
        "warn_deg": 35.0,
        "alarm_deg": 45.0,
        "warn_variance_deg2": 16.0,
        "alarm_variance_deg2": 25.0,
    },
    "pushup": {
        "warn_deg": 15.0,
        "alarm_deg": 25.0,
        "warn_variance_deg2": 9.0,
        "alarm_variance_deg2": 16.0,
    },
}

SYMMETRY_CONFIG = {
    "squat": {"joint": "膝角", "warn_deg": 15.0, "alarm_deg": 22.0},
    "pushup": {"joint": "肘角", "warn_deg": 12.0, "alarm_deg": 20.0},
}

DEPTH_CONFIG = {
    "squat": {
        "title": "底部膝角",
        "joint": "膝角",
        "warn": 120.0,
        "alarm": 135.0,
    },
    "pushup": {
        "title": "底部肘角",
        "joint": "肘角",
        "warn": 110.0,
        "alarm": 125.0,
    },
}

TRACKING_CONFIG = {
    "squat": {
        "title": "膝部内扣轨迹",
        "warn": 22.0,
        "alarm": 35.0,
        "unit": "%",
    },
    "pushup": {
        "title": "手肘外张角度",
        "warn": 80.0,
        "alarm": 95.0,
        "unit": "°",
    },
}

TEMPO_CONFIG = {
    "warn_cv": 0.15,
    "alarm_cv": 0.35,
}


def _angle_deg(first: np.ndarray, second: np.ndarray) -> float | None:
    first_norm = float(np.linalg.norm(first))
    second_norm = float(np.linalg.norm(second))
    if first_norm < 1e-8 or second_norm < 1e-8:
        return None
    cosine = float(np.dot(first, second) / (first_norm * second_norm))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _joint_angle(points: np.ndarray, centre: int, first: int, second: int) -> float | None:
    if not np.isfinite(points[[centre, first, second]]).all():
        return None
    return _angle_deg(points[first] - points[centre], points[second] - points[centre])


def _planar_angle(points: np.ndarray, centre: int, first: int, second: int) -> float | None:
    if not np.isfinite(points[[centre, first, second]]).all():
        return None
    center_xy = points[centre, :2]
    first_xy = points[first, :2] - center_xy
    second_xy = points[second, :2] - center_xy
    return _angle_deg(
        np.array([first_xy[0], first_xy[1], 0.0]),
        np.array([second_xy[0], second_xy[1], 0.0]),
    )


def _bottom_values(
    values: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep: dict[str, Any],
) -> list[float]:
    rep_mask = np.asarray(
        [frame_rep == rep["rep_id"] for frame_rep in frame_rep_ids],
        dtype=bool,
    )
    valid_mask = rep_mask & np.isfinite(values)
    if not valid_mask.any():
        return []
    rep_times = times[valid_mask]
    rep_values = values[valid_mask]
    bottom_s = float(rep.get("bottom_s") or (rep["start_s"] + rep["end_s"]) / 2.0)
    window_s = min(0.35, max(0.12, (rep["end_s"] - rep["start_s"]) * 0.12))
    bottom_mask = np.abs(rep_times - bottom_s) <= window_s
    if not bottom_mask.any():
        nearest = int(np.argmin(np.abs(rep_times - bottom_s)))
        bottom_mask[nearest] = True
    return [float(value) for value in rep_values[bottom_mask]]


def _primary_angle_series(
    points: np.ndarray,
    exercise: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    left_values = np.full(len(points), np.nan, dtype=np.float64)
    right_values = np.full(len(points), np.nan, dtype=np.float64)
    mean_values = np.full(len(points), np.nan, dtype=np.float64)
    if exercise == "squat":
        left_ids, right_ids = (23, 25, 27), (24, 26, 28)
    else:
        left_ids, right_ids = (11, 13, 15), (12, 14, 16)
    for index in range(len(points)):
        left = _joint_angle(points[index], left_ids[1], left_ids[0], left_ids[2])
        right = _joint_angle(points[index], right_ids[1], right_ids[0], right_ids[2])
        left_values[index] = np.nan if left is None else left
        right_values[index] = np.nan if right is None else right
        finite = [value for value in (left, right) if value is not None]
        if finite:
            mean_values[index] = float(np.mean(finite))
    return mean_values, left_values, right_values


def _resample_rep_curve(
    values: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep: dict[str, Any],
    samples: int = 40,
) -> np.ndarray | None:
    mask = np.asarray(
        [frame_rep == rep["rep_id"] for frame_rep in frame_rep_ids],
        dtype=bool,
    )
    mask &= np.isfinite(values)
    if mask.sum() < 8:
        return None
    x = times[mask]
    y = values[mask]
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    start = float(rep["start_s"])
    end = float(rep["end_s"])
    if end - start < 0.15:
        return None
    grid = np.linspace(start, end, samples)
    curve = np.interp(grid, x, y)
    kernel = np.asarray([0.25, 0.5, 0.25], dtype=np.float64)
    return np.convolve(
        np.pad(curve, (1, 1), mode="edge"),
        kernel,
        mode="valid",
    )


def _point_confidence(visibility: np.ndarray, predicted: np.ndarray, joint_ids: tuple[int, ...]) -> float:
    values = []
    for joint_id in joint_ids:
        values.append(0.52 if predicted[joint_id] else float(visibility[joint_id]))
    return float(np.mean(values)) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return None
    return float(np.percentile(np.asarray(finite, dtype=np.float64), percentile))


def _variance(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    if len(finite) < 3:
        return None
    return float(np.var(np.asarray(finite, dtype=np.float64), ddof=1))


def _classify(value: float | None, warn: float, alarm: float) -> str:
    if value is None:
        return "unavailable"
    if value >= alarm:
        return "abnormal"
    if value >= warn:
        return "attention"
    return "normal"


def _classify_low(value: float | None, warn: float, alarm: float) -> str:
    if value is None:
        return "unavailable"
    if value <= alarm:
        return "abnormal"
    if value <= warn:
        return "attention"
    return "normal"


def _worst_status(*statuses: str) -> str:
    available = [status for status in statuses if status != "unavailable"]
    if not statuses or not available:
        return "unavailable"
    return max(available, key=lambda status: STATUS_RANK[status])


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def _rep_series(values: np.ndarray, frame_rep_ids: list[int | None], rep_id: int) -> list[float]:
    mask = np.asarray([rep == rep_id for rep in frame_rep_ids], dtype=bool)
    if not mask.any():
        return []
    selected = values[mask]
    return [float(value) for value in selected[np.isfinite(selected)]]


def _com_series(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    result = np.full((len(points), 3), np.nan, dtype=np.float64)
    confidence = np.zeros(len(points), dtype=np.float64)
    for frame_index in range(len(points)):
        acc = np.zeros(3, dtype=np.float64)
        available_weight = 0.0
        weighted_confidence = 0.0
        for joint_ids, weight in COM_SEGMENTS:
            coords = points[frame_index, list(joint_ids)]
            if not np.isfinite(coords).all():
                continue
            point = np.mean(coords, axis=0)
            segment_confidence = _point_confidence(
                visibility[frame_index], predicted[frame_index], joint_ids
            )
            acc += point * weight
            available_weight += weight
            weighted_confidence += segment_confidence * weight
        coverage = available_weight / COM_TOTAL_WEIGHT
        if coverage < 0.56 or available_weight <= 0:
            continue
        result[frame_index] = acc / available_weight
        confidence[frame_index] = (weighted_confidence / available_weight) * coverage
    return result, confidence


def _body_scale(points: np.ndarray, confidence: np.ndarray, exercise: str) -> tuple[float, str, str]:
    if exercise == "pushup":
        shoulder_mid = np.mean(points[:, [11, 12]], axis=1)
        ankle_mid = np.mean(points[:, [27, 28]], axis=1)
        distances = np.linalg.norm(shoulder_mid - ankle_mid, axis=1)
        valid = np.isfinite(distances) & (confidence >= 0.35)
        if valid.any():
            value = float(np.percentile(distances[valid], 90))
            return max(0.7, min(2.4, value)), "肩–踝长度", "shoulder_to_ankle_p90"
    height_samples = points[:, 0, 1]
    valid = np.isfinite(height_samples) & (confidence >= 0.35)
    if valid.any():
        value = float(np.percentile(height_samples[valid], 95)) / 0.93
        return max(1.0, min(2.4, value)), "站立高度", "nose_height_p95_divided_by_0.93"
    finite_y = points[:, :, 1]
    spans = np.nanmax(finite_y, axis=1) - np.nanmin(finite_y, axis=1)
    spans = spans[np.isfinite(spans)]
    fallback = float(np.percentile(spans, 90)) if len(spans) else 1.0
    return max(0.7, min(2.4, fallback)), "人体长度", "skeleton_span_p90"


def _front_sign(points: np.ndarray, visibility: np.ndarray) -> tuple[float, bool]:
    heel_mid = np.mean(points[:, [29, 30]], axis=1)
    toe_mid = np.mean(points[:, [31, 32]], axis=1)
    heel_conf = np.minimum(visibility[:, 29], visibility[:, 30])
    toe_conf = np.minimum(visibility[:, 31], visibility[:, 32])
    delta = toe_mid[:, 2] - heel_mid[:, 2]
    valid = np.isfinite(delta) & (heel_conf >= 0.35) & (toe_conf >= 0.35)
    if valid.sum() >= 3:
        median_delta = float(np.median(delta[valid]))
        if abs(median_delta) >= 0.015:
            return (1.0 if median_delta > 0 else -1.0), True
    return 1.0, False


def _sample_indices(frame_count: int, fps: float) -> list[int]:
    stride = max(1, int(round(max(fps, 1.0) / 12.0)))
    indices = list(range(0, frame_count, stride))
    if frame_count and indices[-1] != frame_count - 1:
        indices.append(frame_count - 1)
    return indices


def _empty_metric(metric_id: str, title: str, reason: str, chart_kind: str = "positive") -> dict[str, Any]:
    return {
        "id": metric_id,
        "title": title,
        "availability": "insufficient",
        "availability_label": STATUS_LABELS["unavailable"],
        "status": "unavailable",
        "status_label": STATUS_LABELS["unavailable"],
        "value_text": "—",
        "unit": "",
        "headline": reason,
        "detail": reason,
        "definition": "",
        "threshold_text": "—",
        "basis": "数据不足，未生成阈值判断。",
        "view_note": "",
        "chart": {
            "kind": chart_kind,
            "y_label": "",
            "warn": None,
            "alarm": None,
            "series": {"t": [], "rep": [], "v": [], "confidence": []},
            "reps": [],
        },
    }


def _apply_view_gate(
    metric: dict[str, Any],
    *,
    reliable_views: set[str],
    view: str,
    recommendation: str,
    reliable_availability: str = "reliable",
) -> dict[str, Any]:
    reliable = view in reliable_views
    metric["availability"] = (
        reliable_availability if reliable else "trend_only"
    )
    metric["availability_label"] = (
        "专项观察"
        if reliable and reliable_availability == "diagnostic"
        else "可评估"
        if reliable
        else TREND_ONLY_LABEL
    )
    if reliable or metric.get("status") == "unavailable":
        if (
            reliable
            and reliable_availability == "diagnostic"
            and metric.get("status") != "unavailable"
        ):
            metric["status_label"] = (
                f"专项 · {STATUS_LABELS.get(str(metric.get('status')), '观察')}"
            )
        return metric

    metric["observed_status"] = metric.get("status")
    metric["observed_status_label"] = metric.get("status_label")
    metric["status"] = "unavailable"
    metric["status_label"] = TREND_ONLY_LABEL
    metric["headline"] = (
        f"{TREND_ONLY_LABEL} · {metric.get('headline') or ''} "
        f"当前 {view} 机位不参与异常分级"
    ).strip()
    metric["detail"] = (
        f"{metric.get('detail') or ''} "
        "该曲线仍可用于观察变化趋势，不应据此判断动作异常。"
    ).strip()
    metric["threshold_text"] = "当前机位仅展示趋势，不应用关注/异常阈值。"
    metric["view_note"] = (
        f"{metric.get('view_note') or ''} "
        f"{recommendation}"
    ).strip()
    chart = metric.get("chart")
    if isinstance(chart, dict):
        chart["warn"] = None
        chart["alarm"] = None
    return metric

def _build_com_metric(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, Any]:
    com, com_confidence = _com_series(points, visibility, predicted)
    finite = np.isfinite(com).all(axis=1)
    if finite.sum() < 3:
        return _empty_metric("com_ap_drift", "重心前后漂移", "没有足够的 3D 重心帧。", "signed")

    scale_m, scale_label, scale_method = _body_scale(points, com_confidence, exercise)
    front_sign, direction_reliable = _front_sign(points, visibility)
    ap = com[:, 2] * front_sign
    rep_ids = [rep["rep_id"] for rep in rep_payload]

    first_rep_mask = (
        np.asarray([rep == rep_ids[0] for rep in frame_rep_ids], dtype=bool)
        if rep_ids
        else finite
    )
    baseline_pool = ap[first_rep_mask & finite]
    if not len(baseline_pool):
        baseline_pool = ap[finite]
    global_baseline = float(np.median(baseline_pool))

    per_rep: list[dict[str, Any]] = []
    rep_baselines: dict[int, float] = {}
    warn_m = scale_m * COM_WARN_RATIO
    alarm_m = scale_m * COM_ALARM_RATIO
    for rep in rep_payload:
        values = _rep_series(ap, frame_rep_ids, int(rep["rep_id"]))
        if not values:
            continue
        baseline_count = max(3, min(len(values), int(round(len(values) * 0.15))))
        baseline = float(np.median(values[:baseline_count]))
        rep_baselines[int(rep["rep_id"])] = baseline
        relative = np.asarray(values, dtype=np.float64) - baseline
        p05 = float(np.percentile(relative, 5))
        p95 = float(np.percentile(relative, 95))
        drift_m = max(0.0, p95 - p05)
        status = _classify(drift_m, warn_m, alarm_m)
        per_rep.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": rep["start_s"],
                "end_s": rep["end_s"],
                "drift_m": _round(drift_m, 4),
                "drift_cm": _round(drift_m * 100.0, 2),
                "ratio": _round(drift_m / scale_m, 4),
                "forward_cm": _round(max(0.0, float(p95)) * 100.0, 2),
                "backward_cm": _round(min(0.0, float(p05)) * 100.0, 2),
                "status": status,
                "status_label": STATUS_LABELS[status],
            }
        )

    sample_t: list[float] = []
    sample_rep: list[int | None] = []
    sample_value: list[float | None] = []
    sample_confidence: list[float | None] = []
    for index in _sample_indices(len(points), fps):
        if not finite[index]:
            continue
        rep_id = frame_rep_ids[index]
        baseline = (
            rep_baselines.get(int(rep_id), global_baseline)
            if rep_id is not None
            else global_baseline
        )
        sample_t.append(_round(float(times[index]), 4) or 0.0)
        sample_rep.append(rep_id)
        sample_value.append(_round((float(ap[index]) - baseline) * 100.0, 2))
        sample_confidence.append(_round(float(com_confidence[index]), 3))

    worst = max(per_rep, key=lambda item: float(item.get("drift_m") or 0.0), default=None)
    if worst is not None:
        status = str(worst["status"])
        value_text = f"{float(worst['drift_cm']):.1f}"
        headline = f"最差 Rep {worst['rep_id']}：前后摆动 {float(worst['drift_cm']):.1f} cm"
        detail = (
            f"向脚尖方向 {abs(float(worst['forward_cm'])):.1f} cm，"
            f"向脚跟方向 {abs(float(worst['backward_cm'])):.1f} cm；"
            f"约为{scale_label}的 {float(worst['ratio']) * 100:.1f}%。"
        )
    else:
        status = "unavailable"
        value_text = "—"
        headline = "本次没有有效 Rep，无法形成逐 Rep 漂移结论。"
        detail = "曲线仍显示全过程重心位移，但不做异常分级。"

    direction_note = "脚趾方向可辨识" if direction_reliable else "前后方向由身体坐标系推断"
    if view in {"front", "rear"}:
        view_note = "正面/背面机位的前后深度主要来自 3D 推断，建议结合斜侧机位复核。"
    else:
        view_note = "当前矢状面/斜侧机位对前后漂移更敏感。"

    metric = {
        "id": "com_ap_drift",
        "title": "重心前后漂移",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": value_text,
        "unit": "cm",
        "headline": headline,
        "detail": detail,
        "definition": (
            "以 14 个人体节段的近似质量权重合成全身质心，取身体前后轴位移；"
            "每个 Rep 以起始姿态为零点，曲线正负表示脚尖/脚跟方向。"
        ),
        "threshold_text": (
            f"关注 ≥ {warn_m * 100:.1f} cm ({COM_WARN_RATIO * 100:.0f}%)；"
            f"异常 ≥ {alarm_m * 100:.1f} cm ({COM_ALARM_RATIO * 100:.0f}% {scale_label})"
        ),
        "basis": (
            f"阈值按{scale_label}归一化，避免身高不同导致绝对厘米数不可比。"
            "4% 起进入关注、7% 判为异常，是 VLS 的工程暂定门槛；"
            "用于发现明显的前后失控，不声称是临床平衡标准。"
        ),
        "view_note": (
            f"{view_note} {direction_note}；高度基准 {scale_m:.2f} m（{scale_method}）。"
        ),
        "chart": {
            "kind": "signed",
            "y_label": "相对 Rep 起点 (cm)",
            "warn": _round(warn_m * 100.0, 2),
            "alarm": _round(alarm_m * 100.0, 2),
            "series": {
                "t": sample_t,
                "rep": sample_rep,
                "v": sample_value,
                "confidence": sample_confidence,
            },
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in rep_payload
            ],
        },
        "reps": per_rep,
        "body_scale_m": _round(scale_m, 3),
        "body_scale_label": scale_label,
    }
    return _apply_view_gate(
        metric,
        reliable_views=SIDE_VIEWS,
        view=view,
        recommendation="建议使用侧视或斜侧机位复核。",
    )


def _build_depth_metric(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, Any]:
    config = DEPTH_CONFIG[exercise]
    if exercise == "squat":
        left_ids, right_ids = (23, 25, 27), (24, 26, 28)
    else:
        left_ids, right_ids = (11, 13, 15), (12, 14, 16)

    values = np.full(len(points), np.nan, dtype=np.float64)
    confidence = np.zeros(len(points), dtype=np.float64)
    for index in range(len(points)):
        left = _joint_angle(points[index], left_ids[1], left_ids[0], left_ids[2])
        right = _joint_angle(points[index], right_ids[1], right_ids[0], right_ids[2])
        if left is None or right is None:
            continue
        values[index] = (left + right) / 2.0
        confidence[index] = min(
            _point_confidence(visibility[index], predicted[index], left_ids),
            _point_confidence(visibility[index], predicted[index], right_ids),
        )
    valid = np.isfinite(values) & (confidence >= 0.3)
    if valid.sum() < 3:
        return _empty_metric(
            "depth_angle",
            config["title"],
            f"没有足够的可靠{config['joint']}帧。",
        )

    per_rep = []
    for rep in rep_payload:
        bottom_values = _bottom_values(values, times, frame_rep_ids, rep)
        if not bottom_values:
            continue
        bottom_angle = float(np.median(bottom_values))
        status = _classify(bottom_angle, config["warn"], config["alarm"])
        per_rep.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": rep["start_s"],
                "end_s": rep["end_s"],
                "bottom_angle_deg": _round(bottom_angle, 2),
                "status": status,
                "status_label": STATUS_LABELS[status],
            }
        )

    series_t = []
    series_rep = []
    series_v = []
    series_conf = []
    for index in _sample_indices(len(points), fps):
        if not np.isfinite(values[index]):
            continue
        series_t.append(_round(float(times[index]), 4) or 0.0)
        series_rep.append(frame_rep_ids[index])
        series_v.append(_round(float(values[index]), 2))
        series_conf.append(_round(float(confidence[index]), 3))

    worst = max(
        per_rep,
        key=lambda item: float(item.get("bottom_angle_deg") or 0.0),
        default=None,
    )
    if worst is None:
        return _empty_metric(
            "depth_angle",
            config["title"],
            "本次没有可形成深度结论的有效 Rep。",
        )
    status = str(worst["status"])
    action = "膝角越大表示下蹲越浅" if exercise == "squat" else "肘角越大表示下放越浅"
    metric = {
        "id": "depth_angle",
        "title": config["title"],
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": f"{float(worst['bottom_angle_deg']):.1f}",
        "unit": "°",
        "headline": (
            f"最差 Rep {worst['rep_id']}："
            f"底部{config['joint']} {float(worst['bottom_angle_deg']):.1f}°"
        ),
        "detail": f"{action}；该值取底部前后约 0.12–0.35 秒窗口的中位数。",
        "definition": (
            "左右两侧髋–膝–踝夹角取均值，并在每个 Rep 底部窗口取中位数。"
            if exercise == "squat"
            else "左右两侧肩–肘–腕夹角取均值，并在每个 Rep 底部窗口取中位数。"
        ),
        "threshold_text": (
            f"关注 ≥ {config['warn']:.0f}°；"
            f"异常 ≥ {config['alarm']:.0f}°"
        ),
        "basis": (
            "阈值复用 VLS assessment_rules.json 的 depth 指标工程分带；"
            "该指标目前标记为 diagnostic_only，不替代问题卡结论。"
        ),
        "view_note": "深度依赖侧视/斜侧机位，正面机位只显示趋势。",
        "chart": {
            "kind": "positive",
            "y_label": f"平均{config['joint']} (°)",
            "warn": config["warn"],
            "alarm": config["alarm"],
            "series": {
                "t": series_t,
                "rep": series_rep,
                "v": series_v,
                "confidence": series_conf,
            },
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in rep_payload
            ],
        },
        "reps": per_rep,
    }
    return _apply_view_gate(
        metric,
        reliable_views=SIDE_VIEWS,
        view=view,
        recommendation="建议使用侧视或斜侧机位复核深度。",
        reliable_availability="diagnostic",
    )


def _build_tracking_metric(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, Any]:
    config = TRACKING_CONFIG[exercise]
    values = np.full(len(points), np.nan, dtype=np.float64)
    confidence = np.zeros(len(points), dtype=np.float64)
    if exercise == "squat":
        joints = (23, 24, 25, 26, 27, 28)
        for index in range(len(points)):
            hip_left = points[index, 23]
            hip_right = points[index, 24]
            if not np.isfinite(points[index, list(joints)]).all():
                continue
            center_x = float((hip_left[0] + hip_right[0]) / 2.0)
            hip_width = abs(float(hip_right[0] - hip_left[0]))
            if hip_width < 0.03:
                continue
            ratios = []
            for knee_id, ankle_id in ((25, 27), (26, 28)):
                knee = points[index, knee_id]
                ankle = points[index, ankle_id]
                ankle_outward = abs(float(ankle[0] - center_x))
                knee_outward = abs(float(knee[0] - center_x))
                ratios.append((ankle_outward - knee_outward) / hip_width * 100.0)
            values[index] = float(np.mean(ratios))
            confidence[index] = _point_confidence(
                visibility[index], predicted[index], joints
            )
        definition = (
            "以髋中点为中线，比较膝、踝相对中线的横向位置；"
            "正值表示膝盖相对脚踝向身体中线内扣。"
        )
        chart_kind = "signed"
        y_label = "膝部向内偏移 (%)"
        view_note = "内扣轨迹只在正面/背面机位参与专项观察；侧视仅显示趋势。"
    else:
        joints = (11, 12, 13, 14, 23, 24)
        for index in range(len(points)):
            left = _planar_angle(points[index], 11, 13, 23)
            right = _planar_angle(points[index], 12, 14, 24)
            if left is None or right is None:
                continue
            values[index] = (left + right) / 2.0
            confidence[index] = _point_confidence(
                visibility[index], predicted[index], joints
            )
        definition = (
            "正面投影中，上臂相对同侧躯干的夹角；"
            "角度越大表示手肘越向外张开。"
        )
        chart_kind = "positive"
        y_label = "手肘外张角 (°)"
        view_note = "手肘外张只在正面/背面机位参与专项观察；斜侧和侧视仅显示趋势。"
    valid = np.isfinite(values) & (confidence >= 0.3)
    if valid.sum() < 3:
        return _empty_metric(
            "tracking_angle",
            config["title"],
            "没有足够的可靠轨迹帧。",
            chart_kind,
        )

    per_rep = []
    for rep in rep_payload:
        bottom_values = _bottom_values(values, times, frame_rep_ids, rep)
        if not bottom_values:
            continue
        bottom_value = float(np.median(bottom_values))
        severity_value = (
            max(0.0, bottom_value)
            if exercise == "squat"
            else abs(bottom_value)
        )
        status = _classify(severity_value, config["warn"], config["alarm"])
        per_rep.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": rep["start_s"],
                "end_s": rep["end_s"],
                "bottom_value": _round(bottom_value, 2),
                "status": status,
                "status_label": STATUS_LABELS[status],
            }
        )
    worst = max(
        per_rep,
        key=lambda item: abs(float(item.get("bottom_value") or 0.0)),
        default=None,
    )
    if worst is None:
        return _empty_metric(
            "tracking_angle",
            config["title"],
            "本次没有可形成轨迹结论的有效 Rep。",
            chart_kind,
        )

    series_t = []
    series_rep = []
    series_v = []
    series_conf = []
    for index in _sample_indices(len(points), fps):
        if not np.isfinite(values[index]):
            continue
        series_t.append(_round(float(times[index]), 4) or 0.0)
        series_rep.append(frame_rep_ids[index])
        series_v.append(_round(float(values[index]), 2))
        series_conf.append(_round(float(confidence[index]), 3))
    status = str(worst["status"])
    value = float(worst.get("bottom_value") or 0.0)
    metric = {
        "id": "tracking_angle",
        "title": config["title"],
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": f"{abs(value):.1f}",
        "unit": config["unit"],
        "headline": (
            f"最差 Rep {worst['rep_id']}："
            f"{config['title']} {abs(value):.1f}{config['unit']}"
        ),
        "detail": (
            f"底部附近数值为 {value:+.1f}{config['unit']}。"
            if exercise == "squat"
            else f"底部附近平均外张角为 {value:.1f}°。"
        ),
        "definition": definition,
        "threshold_text": (
            f"关注 ≥ {config['warn']:.0f}{config['unit']}；"
            f"异常 ≥ {config['alarm']:.0f}{config['unit']}"
        ),
        "basis": (
            "阈值沿用实时动作规则的暂定工程门槛；"
            "轨迹专项指标目前只用于观察，不生成正式问题卡。"
        ),
        "view_note": view_note,
        "chart": {
            "kind": chart_kind,
            "y_label": y_label,
            "warn": config["warn"],
            "alarm": config["alarm"],
            "series": {
                "t": series_t,
                "rep": series_rep,
                "v": series_v,
                "confidence": series_conf,
            },
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in rep_payload
            ],
        },
        "reps": per_rep,
    }
    return _apply_view_gate(
        metric,
        reliable_views=FRONTAL_VIEWS,
        view=view,
        recommendation="建议使用正面或背面机位复核轨迹。",
        reliable_availability="diagnostic",
    )


def _build_tempo_metric(
    rep_payload: list[dict[str, Any]],
    exercise: str,
) -> dict[str, Any]:
    per_rep = []
    durations = []
    eccentric_values = []
    concentric_values = []
    for rep in rep_payload:
        start = float(rep["start_s"])
        bottom = float(rep.get("bottom_s") or start)
        end = float(rep["end_s"])
        duration = max(0.0, end - start)
        eccentric = max(0.0, bottom - start)
        concentric = max(0.0, end - bottom)
        if duration <= 0:
            continue
        durations.append(duration)
        eccentric_values.append(eccentric)
        concentric_values.append(concentric)
        per_rep.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": start,
                "end_s": end,
                "duration_s": _round(duration, 3),
                "descent_s": _round(eccentric, 3),
                "ascent_s": _round(concentric, 3),
            }
        )
    if not per_rep:
        return _empty_metric("tempo_structure", "分阶段节奏", "没有可用的 Rep 时长。")

    values = np.asarray(durations, dtype=np.float64)
    mean_duration = float(np.mean(values))
    cv = (
        float(np.std(values) / mean_duration)
        if len(values) >= 2 and mean_duration > 1e-8
        else None
    )
    mean_descent = float(np.mean(eccentric_values))
    mean_ascent = float(np.mean(concentric_values))
    status = (
        _classify(cv, TEMPO_CONFIG["warn_cv"], TEMPO_CONFIG["alarm_cv"])
        if len(values) >= 3
        else "unavailable"
    )
    descent_label = "下蹲" if exercise == "squat" else "下放"
    ascent_label = "起身" if exercise == "squat" else "推起"
    bars = [
        {
            "rep_id": rep["rep_id"],
            "start_s": rep["start_s"],
            "end_s": rep["end_s"],
            "value": rep["duration_s"],
        }
        for rep in per_rep
    ]
    series_t = [
        _round((float(rep["start_s"]) + float(rep["end_s"])) / 2.0, 4) or 0.0
        for rep in per_rep
    ]
    series_v = [rep["duration_s"] for rep in per_rep]
    return {
        "id": "tempo_structure",
        "title": "分阶段节奏",
        "availability": "reliable" if len(values) >= 3 else "insufficient",
        "availability_label": "可评估" if len(values) >= 3 else "数据不足",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": f"{cv * 100:.0f}" if cv is not None else "—",
        "unit": "% CV",
        "headline": (
            f"整组 Rep 时长变异系数 {cv * 100:.0f}%"
            if cv is not None
            else "有效 Rep 数不足，无法计算整组变异系数"
        ),
        "detail": (
            f"平均{descent_label} {mean_descent:.2f}s，"
            f"平均{ascent_label} {mean_ascent:.2f}s，"
            f"平均单次 {mean_duration:.2f}s。"
        ),
        "definition": (
            "Rep 总时长为起止边界之差；下蹲/下放阶段从起始到最低点，"
            "起身/推起阶段从最低点到结束。整组稳定性用 Rep 时长 CV 表示。"
        ),
        "threshold_text": (
            f"关注 CV ≥ {TEMPO_CONFIG['warn_cv'] * 100:.0f}%；"
            f"异常 CV ≥ {TEMPO_CONFIG['alarm_cv'] * 100:.0f}%"
        ),
        "basis": (
            "复用 VLS rep_duration_cv 指标与 issue 门槛；"
            "CV 依赖冻结后的 Rep 边界，边界误差会传导到节奏结果。"
        ),
        "view_note": "节奏指标与机位无关，但需要完整、稳定的 Rep 边界。",
        "wide": True,
        "chart": {
            "kind": "rep_bars",
            "y_label": "Rep 总时长 (s)",
            "warn": None,
            "alarm": None,
            "series": {
                "t": series_t,
                "rep": [rep["rep_id"] for rep in per_rep],
                "v": series_v,
                "confidence": [1.0 for _ in per_rep],
            },
            "bars": bars,
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in per_rep
            ],
        },
        "reps": per_rep,
    }


def _build_motion_profile_metric(
    points: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    exercise: str,
) -> dict[str, Any]:
    values, _, _ = _primary_angle_series(points, exercise)
    curves = []
    scores = []
    joint_label = "膝角" if exercise == "squat" else "肘角"
    for rep in rep_payload:
        curve = _resample_rep_curve(values, times, frame_rep_ids, rep)
        if curve is None:
            continue
        value_range = float(np.max(curve) - np.min(curve))
        if value_range < 5.0:
            continue
        normalized = (curve - float(np.min(curve))) / value_range
        dt = 1.0 / max(1, len(normalized) - 1)
        velocity = np.gradient(normalized, dt)
        acceleration = np.gradient(velocity, dt)
        jerk = np.gradient(acceleration, dt)
        jerk_score = float(np.sqrt(np.mean(jerk * jerk)))
        smoothness_index = 100.0 / (1.0 + jerk_score / 100.0)
        scores.append(smoothness_index)
        start = float(rep["start_s"])
        end = float(rep["end_s"])
        bottom = float(rep.get("bottom_s") or (start + end) / 2.0)
        curves.append(
            {
                "rep_id": rep["rep_id"],
                "curve": [_round(float(value), 2) for value in curve],
                "bottom_progress_pct": _round(
                    max(0.0, min(100.0, (bottom - start) / (end - start) * 100.0)),
                    2,
                ),
                "jerk_rms": _round(jerk_score, 3),
                "smoothness_index": _round(smoothness_index, 1),
            }
        )
    if not curves:
        return _empty_metric(
            "motion_profile",
            "Rep 动作轮廓",
            "可靠主关节曲线不足，无法生成阶段对齐轮廓。",
            "phase_curves",
        )
    worst_index = int(np.argmin(scores))
    worst = curves[worst_index]
    return {
        "id": "motion_profile",
        "title": "Rep 动作轮廓",
        "availability": "diagnostic",
        "availability_label": "专项观察",
        "status": "unavailable",
        "status_label": "专项观察",
        "value_text": f"{float(worst['smoothness_index']):.0f}",
        "unit": "/100",
        "headline": (
            f"Rep {worst['rep_id']} 平滑度最弱："
            f"平滑度指数 {float(worst['smoothness_index']):.0f}/100"
        ),
        "detail": (
            f"叠加显示每个 Rep 的{joint_label}曲线，并按动作起止时间拉伸到同一进度轴；"
            f"该 Rep 原始归一化 jerk 为 {float(worst['jerk_rms']):.2f}，"
            "指数越高表示速度和加速度变化越平滑。"
        ),
        "definition": (
            "每个 Rep 的关节角曲线先按起止时间重采样到 0–100% 动作进度，"
            "再按自身幅度归一化并计算三阶导数均方根。"
        ),
        "threshold_text": "暂不设置异常阈值，仅用于 Rep 间形态和平滑度比较。",
        "basis": "P2 探索指标；需更多多机位样本后再校准产品阈值。",
        "view_note": "轮廓仍受遮挡和机位影响，应结合曲线置信度阅读。",
        "wide": True,
        "reps": curves,
        "chart": {
            "kind": "phase_curves",
            "y_label": f"{joint_label} (°)",
            "warn": None,
            "alarm": None,
            "progress": list(range(101)),
            "curves": curves,
            "series": {"t": [], "rep": [], "v": [], "confidence": []},
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in rep_payload
            ],
            "clickable": False,
            "note": "曲线按动作进度对齐",
        },
    }


def _correlation_with_lag(first: np.ndarray, second: np.ndarray, max_lag: int = 8) -> tuple[float, int]:
    best_corr = -1.0
    best_lag = 0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            left = first[-lag:]
            right = second[: len(second) + lag]
        elif lag > 0:
            left = first[: len(first) - lag]
            right = second[lag:]
        else:
            left = first
            right = second
        if len(left) < 12 or np.std(left) < 1e-8 or np.std(right) < 1e-8:
            continue
        corr = float(np.corrcoef(left, right)[0, 1])
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    return best_corr, best_lag


def _build_sync_metric(
    points: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    exercise: str,
    view: str,
) -> dict[str, Any]:
    _, left_values, right_values = _primary_angle_series(points, exercise)
    bars = []
    per_rep = []
    for rep in rep_payload:
        left = _resample_rep_curve(left_values, times, frame_rep_ids, rep)
        right = _resample_rep_curve(right_values, times, frame_rep_ids, rep)
        if left is None or right is None:
            continue
        corr, lag = _correlation_with_lag(left, right)
        if corr < -0.99:
            continue
        sync = max(0.0, min(100.0, corr * 100.0))
        status = _classify_low(sync, 80.0, 60.0)
        lag_ms = round(lag * (float(rep["end_s"]) - float(rep["start_s"])) / 39.0 * 1000)
        item = {
            "rep_id": rep["rep_id"],
            "start_s": rep["start_s"],
            "end_s": rep["end_s"],
            "sync_index": _round(sync, 1),
            "lag_ms": lag_ms,
            "status": status,
            "status_label": STATUS_LABELS[status],
        }
        per_rep.append(item)
        bars.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": rep["start_s"],
                "end_s": rep["end_s"],
                "value": item["sync_index"],
            }
        )
    if not per_rep:
        return _empty_metric(
            "left_right_sync",
            "左右动作同步性",
            "双侧时序曲线不足。",
            "rep_bars",
        )
    worst = min(per_rep, key=lambda item: float(item["sync_index"]))
    status = str(worst["status"])
    metric = {
        "id": "left_right_sync",
        "title": "左右动作同步性",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": f"{float(worst['sync_index']):.0f}",
        "unit": "/100",
        "headline": (
            f"最差 Rep {worst['rep_id']}：左右同步性 "
            f"{float(worst['sync_index']):.0f}，时序偏移 {worst['lag_ms']} ms"
        ),
        "detail": "同步性衡量两侧关节角曲线是否同相位；数值低表示两侧动作时序差异更明显。",
        "definition": (
            "左右主关节角按 Rep 时间重采样后进行带时序偏移的相关分析，"
            "以最大相关系数映射为 0–100。"
        ),
        "threshold_text": "关注 < 80；异常 < 60。",
        "basis": "P2 探索指标；不同步不等同于力量差异，需结合反复出现的情况判断。",
        "view_note": "只在正面/背面机位参与专项观察；侧视远侧关节不可靠。",
        "chart": {
            "kind": "rep_bars",
            "y_label": "左右同步性 (/100)",
            "warn": None,
            "alarm": None,
            "bars": bars,
            "series": {
                "t": [
                    _round((float(item["start_s"]) + float(item["end_s"])) / 2.0, 4)
                    or 0.0
                    for item in per_rep
                ],
                "rep": [item["rep_id"] for item in per_rep],
                "v": [item["sync_index"] for item in per_rep],
                "confidence": [1.0 for _ in per_rep],
            },
            "reps": [
                {"rep_id": item["rep_id"], "start_s": item["start_s"], "end_s": item["end_s"]}
                for item in per_rep
            ],
        },
        "reps": per_rep,
    }
    return _apply_view_gate(
        metric,
        reliable_views=FRONTAL_VIEWS,
        view=view,
        recommendation="建议使用正面或背面机位复核左右同步性。",
        reliable_availability="diagnostic",
    )


def _torso_value(points: np.ndarray, exercise: str, front_sign: float) -> float | None:
    shoulder_mid = np.mean(points[[11, 12]], axis=0)
    hip_mid = np.mean(points[[23, 24]], axis=0)
    torso = shoulder_mid - hip_mid
    if exercise == "pushup":
        ankle_mid = np.mean(points[[27, 28]], axis=0)
        angle = _angle_deg(shoulder_mid - hip_mid, ankle_mid - hip_mid)
        return None if angle is None else abs(180.0 - angle)
    dy = float(torso[1])
    dz = float(torso[2]) * front_sign
    if abs(dy) + abs(dz) < 1e-8:
        return None
    return math.degrees(math.atan2(dz, dy))


def _build_torso_metric(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, Any]:
    config = TORSO_CONFIG[exercise]
    core_confidence = np.zeros(len(points), dtype=np.float64)
    for index in range(len(points)):
        core_confidence[index] = _point_confidence(
            visibility[index], predicted[index], CORE_BODY_JOINTS
        )
    front_sign, direction_reliable = _front_sign(points, visibility)

    values = np.full(len(points), np.nan, dtype=np.float64)
    for index in range(len(points)):
        if core_confidence[index] < 0.38:
            continue
        value = _torso_value(points[index], exercise, front_sign)
        if value is not None:
            values[index] = value
    if np.isfinite(values).sum() < 3:
        return _empty_metric(
            "torso_variability",
            "躯干角波动",
            "没有足够的可靠躯干角帧。",
            "signed" if exercise == "squat" else "positive",
        )

    per_rep: list[dict[str, Any]] = []
    for rep in rep_payload:
        rep_mask = np.asarray(
            [frame_rep == rep["rep_id"] for frame_rep in frame_rep_ids],
            dtype=bool,
        )
        valid_mask = rep_mask & np.isfinite(values)
        rep_values = values[valid_mask]
        if not len(rep_values):
            continue
        rep_times = times[valid_mask]
        bottom_s = float(rep.get("bottom_s") or (rep["start_s"] + rep["end_s"]) / 2.0)
        window_s = min(0.35, max(0.12, (rep["end_s"] - rep["start_s"]) * 0.12))
        bottom_mask = np.abs(rep_times - bottom_s) <= window_s
        if not bottom_mask.any():
            nearest = int(np.argmin(np.abs(rep_times - bottom_s)))
            bottom_mask[nearest] = True
        bottom_offset = float(np.median(rep_values[bottom_mask]))
        p90 = float(np.percentile(np.abs(rep_values), 90))
        variance = _variance(rep_values)
        angle_status = _classify(p90, config["warn_deg"], config["alarm_deg"])
        bottom_status = _classify(
            abs(bottom_offset),
            config["warn_deg"],
            config["alarm_deg"],
        )
        variance_status = _classify(
            variance,
            config["warn_variance_deg2"],
            config["alarm_variance_deg2"],
        )
        status = _worst_status(angle_status, variance_status)
        per_rep.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": rep["start_s"],
                "end_s": rep["end_s"],
                "p90_deg": _round(p90, 2),
                "bottom_offset_deg": _round(bottom_offset, 2),
                "bottom_status": bottom_status,
                "median_deg": _round(float(np.median(rep_values)), 2),
                "variance_deg2": _round(variance, 2),
                "std_deg": _round(math.sqrt(variance), 2) if variance is not None else None,
                "angle_status": angle_status,
                "variance_status": variance_status,
                "status": status,
                "status_label": STATUS_LABELS[status],
            }
        )

    worst = max(
        per_rep,
        key=lambda item: max(
            float(item.get("p90_deg") or 0.0) / config["warn_deg"],
            math.sqrt(float(item.get("variance_deg2") or 0.0))
            / math.sqrt(config["warn_variance_deg2"]),
        ),
        default=None,
    )
    if worst is not None:
        status = str(worst["status"])
        value_text = f"{float(worst['variance_deg2']):.1f}"
        headline = (
            f"最差 Rep {worst['rep_id']}：角方差 {float(worst['variance_deg2']):.1f}°²，"
            f"σ {float(worst['std_deg'] or 0):.1f}°"
        )
        detail = f"该 Rep 的 P90 角度偏差为 {float(worst['p90_deg']):.1f}°。"
    else:
        status = "unavailable"
        value_text = "—"
        headline = "本次没有有效 Rep，无法形成躯干波动结论。"
        detail = "曲线仍显示全过程角度，但不做异常分级。"

    series_t: list[float] = []
    series_rep: list[int | None] = []
    series_v: list[float | None] = []
    series_conf: list[float | None] = []
    for index in _sample_indices(len(points), fps):
        if not np.isfinite(values[index]):
            continue
        series_t.append(_round(float(times[index]), 4) or 0.0)
        series_rep.append(frame_rep_ids[index])
        series_v.append(_round(float(values[index]), 2))
        series_conf.append(_round(float(core_confidence[index]), 3))

    if exercise == "squat":
        definition = "肩中点–髋中点的躯干向量在矢状面相对竖直轴的前倾角；向脚尖为正，0° 表示竖直。"
        y_label = "躯干前倾角 (°)"
        threshold_text = (
            f"关注 |角| ≥ {config['warn_deg']:.0f}° 或方差 ≥ {config['warn_variance_deg2']:.0f}°² "
            f"(σ {math.sqrt(config['warn_variance_deg2']):.0f}°)；"
            f"异常 |角| ≥ {config['alarm_deg']:.0f}° 或方差 ≥ {config['alarm_variance_deg2']:.0f}°² "
            f"(σ {math.sqrt(config['alarm_variance_deg2']):.0f}°)"
        )
        basis = (
            "角度分带对齐 VLS assessment_rules.json 的暂定门槛："
            "torso_lean_bottom 低=35°、中=45°。方差分带（σ 4°/5°）用于识别持续晃动，"
            "同样是工程暂定值，expert_calibrated=false。"
        )
        view_note = "侧/斜侧机位更可靠；正面机位对躯干前后倾角较不敏感。"
    else:
        definition = "肩–髋–踝在髋点形成的直线角相对 180° 的偏差；0° 表示肩、髋、踝共线。"
        y_label = "体线偏差 (°)"
        threshold_text = (
            f"关注偏差 ≥ {config['warn_deg']:.0f}° 或方差 ≥ {config['warn_variance_deg2']:.0f}°² "
            f"(σ {math.sqrt(config['warn_variance_deg2']):.0f}°)；"
            f"异常偏差 ≥ {config['alarm_deg']:.0f}° 或方差 ≥ {config['alarm_variance_deg2']:.0f}°² "
            f"(σ {math.sqrt(config['alarm_variance_deg2']):.0f}°)"
        )
        basis = (
            "角度分带对齐 VLS assessment_rules.json：body_line_angle 低=165°、中=155°，"
            "即偏差 15°/25°。方差分带（σ 3°/4°）为识别持续塌腰/拱背的工程暂定值，非临床 cutoff。"
        )
        view_note = "侧/斜侧机位更适合肩–髋–踝体线；正面机位仅作趋势参考。"

    if not direction_reliable and exercise == "squat":
        view_note += " 前后正负号来自身体坐标系推断。"

    return {
        "id": "torso_variability",
        "title": "躯干角波动",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": value_text,
        "unit": "°²",
        "headline": headline,
        "detail": detail,
        "definition": definition,
        "threshold_text": threshold_text,
        "basis": basis,
        "view_note": view_note,
        "chart": {
            "kind": "signed" if exercise == "squat" else "positive",
            "y_label": y_label,
            "warn": config["warn_deg"],
            "alarm": config["alarm_deg"],
            "series": {
                "t": series_t,
                "rep": series_rep,
                "v": series_v,
                "confidence": series_conf,
            },
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in rep_payload
            ],
        },
        "reps": per_rep,
    }


def _build_torso_metrics(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, dict[str, Any]]:
    combined = _build_torso_metric(
        points,
        visibility,
        predicted,
        times,
        frame_rep_ids,
        rep_payload,
        fps,
        exercise,
        view,
    )
    config = TORSO_CONFIG[exercise]
    offset_title = "底部躯干前倾" if exercise == "squat" else "底部体线偏移"
    stability_title = "前倾稳定性" if exercise == "squat" else "体线稳定性"
    offset = copy.deepcopy(combined)
    stability = copy.deepcopy(combined)
    offset["id"] = "torso_offset"
    offset["title"] = offset_title
    stability["id"] = "torso_stability"
    stability["title"] = stability_title

    reps = combined.get("reps") or []
    if combined.get("status") == "unavailable" or not reps:
        for metric, title in (
            (offset, offset_title),
            (stability, stability_title),
        ):
            metric.update(
                {
                    "title": title,
                    "availability": "insufficient",
                    "availability_label": STATUS_LABELS["unavailable"],
                    "status": "unavailable",
                    "status_label": STATUS_LABELS["unavailable"],
                    "value_text": "—",
                    "unit": "°",
                    "threshold_text": "数据不足，不做阈值判断。",
                    "chart": {
                        **(metric.get("chart") or {}),
                        "warn": None,
                        "alarm": None,
                    },
                }
            )
        return {
            "torso_offset": offset,
            "torso_stability": stability,
        }

    offset_worst = max(
        reps,
        key=lambda item: abs(float(item.get("bottom_offset_deg") or 0.0)),
    )
    offset_signed = float(offset_worst.get("bottom_offset_deg") or 0.0)
    offset_status = str(offset_worst.get("bottom_status") or "unavailable")
    offset.update(
        {
            "status": offset_status,
            "status_label": STATUS_LABELS[offset_status],
            "value_text": f"{abs(offset_signed):.1f}",
            "unit": "°",
            "headline": (
                f"最差 Rep {offset_worst['rep_id']}："
                f"{offset_title} {abs(offset_signed):.1f}°"
            ),
            "detail": (
                f"底部附近角度为 {offset_signed:+.1f}°；"
                + (
                    "正值表示躯干向脚尖方向前倾。"
                    if exercise == "squat"
                    else "数值表示肩、髋、踝偏离身体直线的程度。"
                )
            ),
            "definition": (
                "取每个 Rep 底部前后约 0.12–0.35 秒窗口内的躯干前倾角中位数。"
                if exercise == "squat"
                else "取每个 Rep 底部前后约 0.12–0.35 秒窗口内的体线偏移中位数。"
            ),
            "threshold_text": (
                f"关注 ≥ {config['warn_deg']:.0f}°；"
                f"异常 ≥ {config['alarm_deg']:.0f}°"
            ),
            "chart": {
                **(offset.get("chart") or {}),
                "kind": "signed" if exercise == "squat" else "positive",
                "warn": config["warn_deg"],
                "alarm": config["alarm_deg"],
            },
        }
    )

    stability_worst = max(
        reps,
        key=lambda item: float(item.get("variance_deg2") or 0.0),
    )
    stability_status = str(
        stability_worst.get("variance_status") or "unavailable"
    )
    worst_std = float(stability_worst.get("std_deg") or 0.0)
    medians = {
        int(item["rep_id"]): float(item.get("median_deg") or 0.0)
        for item in reps
    }
    chart = stability.get("chart") or {}
    series = chart.get("series") or {}
    centered_values = []
    for value, rep_id in zip(series.get("v") or [], series.get("rep") or []):
        if value is None or rep_id is None:
            centered_values.append(None)
            continue
        centered_values.append(_round(float(value) - medians.get(int(rep_id), 0.0), 2))
    warn_std = math.sqrt(config["warn_variance_deg2"])
    alarm_std = math.sqrt(config["alarm_variance_deg2"])
    stability.update(
        {
            "status": stability_status,
            "status_label": STATUS_LABELS[stability_status],
            "value_text": f"{worst_std:.1f}",
            "unit": "° σ",
            "headline": (
                f"最差 Rep {stability_worst['rep_id']}："
                f"{stability_title} σ {worst_std:.1f}°"
            ),
            "detail": (
                f"该 Rep P90 姿态偏移为 "
                f"{float(stability_worst.get('p90_deg') or 0.0):.1f}°；"
                "稳定性数值已去除固定姿态偏移，仅反映过程波动。"
            ),
            "definition": (
                "每个 Rep 先以自身角度的中位数为零点，再计算去偏后的标准差；"
                "该指标不再把“本身前倾较大”误算成“动作晃动”。"
            ),
            "threshold_text": (
                f"关注 σ ≥ {warn_std:.0f}°；"
                f"异常 σ ≥ {alarm_std:.0f}°"
            ),
            "chart": {
                **chart,
                "kind": "signed",
                "y_label": "相对 Rep 中位角 (°)",
                "warn": _round(warn_std, 2),
                "alarm": _round(alarm_std, 2),
                "series": {
                    **series,
                    "v": centered_values,
                },
            },
        }
    )

    return {
        "torso_offset": _apply_view_gate(
            offset,
            reliable_views=SIDE_VIEWS,
            view=view,
            recommendation="建议使用侧视或斜侧机位复核。",
        ),
        "torso_stability": _apply_view_gate(
            stability,
            reliable_views=SIDE_VIEWS,
            view=view,
            recommendation="建议使用侧视或斜侧机位复核。",
        ),
    }


def _build_symmetry_metric(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, Any]:
    config = SYMMETRY_CONFIG[exercise]
    if exercise == "squat":
        left_ids, right_ids = (23, 25, 27), (24, 26, 28)
        definition = "左右膝角差经 100×(1−|L−R|/max(L,R)) 转为对称性指数；100=完全对称。"
    else:
        left_ids, right_ids = (11, 13, 15), (12, 14, 16)
        definition = "左右肘角差经 100×(1−|L−R|/max(L,R)) 转为对称性指数；100=完全对称。"

    left_values = np.full(len(points), np.nan, dtype=np.float64)
    right_values = np.full(len(points), np.nan, dtype=np.float64)
    confidence = np.zeros(len(points), dtype=np.float64)
    for index in range(len(points)):
        left = _joint_angle(points[index], *left_ids)
        right = _joint_angle(points[index], *right_ids)
        if left is None or right is None:
            continue
        left_values[index] = left
        right_values[index] = right
        confidence[index] = min(
            _point_confidence(visibility[index], predicted[index], left_ids),
            _point_confidence(visibility[index], predicted[index], right_ids),
        )
    finite = np.isfinite(left_values) & np.isfinite(right_values) & (confidence >= 0.3)
    if finite.sum() < 3:
        return _empty_metric("symmetry_index", "左右对称性指数", "没有足够的双侧关节角帧。", "positive")

    difference = np.abs(left_values - right_values)
    scale = np.maximum(np.abs(left_values), np.abs(right_values))
    symmetry_index = np.where(scale > 1e-6, 100.0 * (1.0 - difference / scale), np.nan)

    per_rep: list[dict[str, Any]] = []
    for rep in rep_payload:
        rep_mask = np.asarray(
            [frame_rep == rep["rep_id"] for frame_rep in frame_rep_ids], dtype=bool
        )
        valid = rep_mask & finite
        if valid.sum() < 3:
            continue
        diff_values = difference[valid]
        si_values = symmetry_index[valid]
        p90_diff = float(np.percentile(diff_values, 90))
        si_p10 = float(np.percentile(si_values, 10))
        status = _classify(p90_diff, config["warn_deg"], config["alarm_deg"])
        bad_fraction = float(np.mean(diff_values >= config["warn_deg"]))
        per_rep.append(
            {
                "rep_id": rep["rep_id"],
                "start_s": rep["start_s"],
                "end_s": rep["end_s"],
                "p90_asymmetry_deg": _round(p90_diff, 2),
                "si_p10": _round(si_p10, 1),
                "bad_fraction": _round(bad_fraction, 3),
                "status": status,
                "status_label": STATUS_LABELS[status],
            }
        )

    worst = max(
        per_rep,
        key=lambda item: float(item.get("p90_asymmetry_deg") or 0.0),
        default=None,
    )
    if worst is not None:
        status = str(worst["status"])
        value_text = f"{float(worst['si_p10']):.0f}"
        headline = (
            f"最差 Rep {worst['rep_id']}：对称性指数 {float(worst['si_p10']):.0f}，"
            f"P90 角差 {float(worst['p90_asymmetry_deg']):.1f}°"
        )
        detail = f"该 Rep 有 {float(worst['bad_fraction']) * 100:.0f}% 的时间超过关注角差。"
    else:
        status = "unavailable"
        value_text = "—"
        headline = "本次没有有效 Rep，无法形成对称性结论。"
        detail = "曲线仍显示全过程左右角差，但不做异常分级。"

    series_t: list[float] = []
    series_rep: list[int | None] = []
    series_diff: list[float | None] = []
    series_si: list[float | None] = []
    series_left: list[float | None] = []
    series_right: list[float | None] = []
    series_conf: list[float | None] = []
    for index in _sample_indices(len(points), fps):
        if not finite[index]:
            continue
        series_t.append(_round(float(times[index]), 4) or 0.0)
        series_rep.append(frame_rep_ids[index])
        series_diff.append(_round(float(difference[index]), 2))
        series_si.append(_round(float(symmetry_index[index]), 1))
        series_left.append(_round(float(left_values[index]), 2))
        series_right.append(_round(float(right_values[index]), 2))
        series_conf.append(_round(float(confidence[index]), 3))

    view_factor = 1.0 if view in {"front", "rear"} else (0.82 if view.startswith("oblique") else 0.55)
    if view_factor < 0.8:
        view_note = "侧机位的远侧关节可能被遮挡，3D 预测会降低该指标的可靠性；优先使用正面/背面机位复核。"
    else:
        view_note = "正面/背面机位对左右主关节角差最直接；斜侧机位需结合遮挡预测阅读。"

    metric = {
        "id": "symmetry_index",
        "title": "左右对称性指数",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "value_text": value_text,
        "unit": "/100",
        "headline": headline,
        "detail": detail,
        "definition": definition,
        "threshold_text": (
            f"关注 P90 角差 ≥ {config['warn_deg']:.0f}°；"
            f"异常 ≥ {config['alarm_deg']:.0f}°（{config['joint']}）"
        ),
        "basis": (
            "角差门槛对齐 VLS assessment_rules.json 的暂定阈值："
            + ("膝/髋不对称低=15°、中=22°。" if exercise == "squat" else "肘不对称低=12°、中=20°。")
            + "对称性指数用于直观显示趋势，分级仍以可解释的角度差为准；非临床诊断。"
        ),
        "view_note": f"机位适配系数 {view_factor:.2f}。{view_note}",
        "chart": {
            "kind": "positive",
            "y_label": "左右主关节角差 (°)",
            "warn": config["warn_deg"],
            "alarm": config["alarm_deg"],
            "series": {
                "t": series_t,
                "rep": series_rep,
                "v": series_diff,
                "si": series_si,
                "left": series_left,
                "right": series_right,
                "confidence": series_conf,
            },
            "reps": [
                {"rep_id": rep["rep_id"], "start_s": rep["start_s"], "end_s": rep["end_s"]}
                for rep in rep_payload
            ],
        },
        "reps": per_rep,
        "view_factor": view_factor,
    }
    return _apply_view_gate(
        metric,
        reliable_views=FRONTAL_VIEWS,
        view=view,
        recommendation="建议使用正面或背面机位复核。",
    )


def build_process_metrics(
    points: np.ndarray,
    visibility: np.ndarray,
    predicted: np.ndarray,
    times: np.ndarray,
    frame_rep_ids: list[int | None],
    rep_payload: list[dict[str, Any]],
    *,
    fps: float,
    exercise: str,
    view: str,
) -> dict[str, Any]:
    """Build the three report-facing continuous metrics and their chart data."""
    if exercise not in {"squat", "pushup"}:
        exercise = "squat"
    frame_count = len(points)
    duration_s = float(times[-1]) if frame_count and np.isfinite(times[-1]) else 0.0
    torso_metrics = _build_torso_metrics(
        points,
        visibility,
        predicted,
        times,
        frame_rep_ids,
        rep_payload,
        fps,
        exercise,
        view,
    )
    metrics = {
        "com_ap_drift": _build_com_metric(
            points,
            visibility,
            predicted,
            times,
            frame_rep_ids,
            rep_payload,
            fps,
            exercise,
            view,
        ),
        "depth_angle": _build_depth_metric(
            points,
            visibility,
            predicted,
            times,
            frame_rep_ids,
            rep_payload,
            fps,
            exercise,
            view,
        ),
        "tracking_angle": _build_tracking_metric(
            points,
            visibility,
            predicted,
            times,
            frame_rep_ids,
            rep_payload,
            fps,
            exercise,
            view,
        ),
        "torso_offset": torso_metrics["torso_offset"],
        "torso_stability": torso_metrics["torso_stability"],
        "symmetry_index": _build_symmetry_metric(
            points,
            visibility,
            predicted,
            times,
            frame_rep_ids,
            rep_payload,
            fps,
            exercise,
            view,
        ),
        "tempo_structure": _build_tempo_metric(rep_payload, exercise),
        "motion_profile": _build_motion_profile_metric(
            points,
            times,
            frame_rep_ids,
            rep_payload,
            exercise,
        ),
        "left_right_sync": _build_sync_metric(
            points,
            times,
            frame_rep_ids,
            rep_payload,
            exercise,
            view,
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": POLICY,
        "exercise": exercise,
        "view": view,
        "fps": round(float(fps), 3),
        "duration_s": round(duration_s, 4),
        "metric_order": [
            "com_ap_drift",
            "depth_angle",
            "tracking_angle",
            "torso_offset",
            "torso_stability",
            "symmetry_index",
            "tempo_structure",
            "motion_profile",
            "left_right_sync",
        ],
        "metrics": metrics,
    }
