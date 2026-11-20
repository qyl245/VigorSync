"""Derive interpretable process metrics from stabilized 3D pose tracks."""

from __future__ import annotations

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

    return {
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
        rep_values = _rep_series(values, frame_rep_ids, int(rep["rep_id"]))
        if not rep_values:
            continue
        p90 = float(np.percentile(np.abs(np.asarray(rep_values, dtype=np.float64)), 90))
        variance = _variance(rep_values)
        angle_status = _classify(p90, config["warn_deg"], config["alarm_deg"])
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

    return {
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
    metrics = {
        "com_ap_drift": _build_com_metric(
            points, visibility, predicted, times, frame_rep_ids, rep_payload, fps, exercise, view
        ),
        "torso_variability": _build_torso_metric(
            points, visibility, predicted, times, frame_rep_ids, rep_payload, fps, exercise, view
        ),
        "symmetry_index": _build_symmetry_metric(
            points, visibility, predicted, times, frame_rep_ids, rep_payload, fps, exercise, view
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": POLICY,
        "exercise": exercise,
        "view": view,
        "fps": round(float(fps), 3),
        "duration_s": round(duration_s, 4),
        "metrics": metrics,
    }
