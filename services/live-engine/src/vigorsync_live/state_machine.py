"""Hysteretic squat and push-up rep state machines."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    UP = "up"
    DOWN = "down"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Thresholds:
    up_enter: float
    up_exit: float
    down_enter: float
    down_exit: float
    shallow_bottom: float
    min_down_s: float = 0.12
    min_rep_s: float = 0.80
    rep_cooldown_s: float = 0.35


SIDE_THRESHOLDS = {
    "squat": Thresholds(
        up_enter=148.0,
        up_exit=140.0,
        down_enter=112.0,
        down_exit=130.0,
        shallow_bottom=105.0,
    ),
    "pushup": Thresholds(
        up_enter=145.0,
        up_exit=139.0,
        down_enter=138.0,
        down_exit=148.0,
        shallow_bottom=110.0,
        min_rep_s=0.30,
        rep_cooldown_s=1.1,
    ),
}

FRONT_SQUAT_THRESHOLDS = Thresholds(
    up_enter=88.0,
    up_exit=82.0,
    down_enter=55.0,
    down_exit=75.0,
    shallow_bottom=60.0,
)

RIGHT_PUSHUP_THRESHOLDS = Thresholds(
    up_enter=148.0,
    up_exit=141.0,
    down_enter=128.0,
    down_exit=140.0,
    shallow_bottom=110.0,
    min_rep_s=0.80,
)


def thresholds_for(exercise: str, view: str) -> Thresholds:
    if exercise == "squat" and view in {"front", "rear"}:
        return FRONT_SQUAT_THRESHOLDS
    if exercise == "pushup" and view in {"side_right", "oblique_right"}:
        return RIGHT_PUSHUP_THRESHOLDS
    try:
        return SIDE_THRESHOLDS[exercise]
    except KeyError as exc:
        raise ValueError(f"unsupported exercise: {exercise}") from exc


@dataclass(frozen=True, slots=True)
class RepCompleted:
    rep_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    confidence: float


@dataclass(frozen=True, slots=True)
class FormAlert:
    code: str
    severity: str
    message: str
    confidence: float
    joints: tuple[str, ...] = ()


@dataclass(slots=True)
class StateMachine:
    exercise: str
    view: str = "side_left"
    min_confidence: float = 0.5
    smoothing_alpha: float | None = None
    paused: bool = False
    reps: int = 0
    last_rep_id: str | None = None
    _phase: Phase = field(default=Phase.UNKNOWN, init=False)
    _smoothed: float | None = field(default=None, init=False)
    _reached_down: bool = field(default=False, init=False)
    _down_started_ms: int | None = field(default=None, init=False)
    _rep_started_ms: int | None = field(default=None, init=False)
    _last_top_ms: int | None = field(default=None, init=False)
    _last_rep_end_ms: int | None = field(default=None, init=False)
    _bottom_angle: float | None = field(default=None, init=False)
    _cycle_posture_valid: bool = field(default=True, init=False)
    _confidence_sum: float = field(default=0.0, init=False)
    _confidence_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.exercise not in SIDE_THRESHOLDS:
            raise ValueError(f"unsupported exercise: {self.exercise}")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        if self.smoothing_alpha is None:
            self.smoothing_alpha = (
                0.3
                if self.exercise == "pushup"
                and self.view in {"side_left", "oblique_left", "front", "rear"}
                else 0.5
            )
        if not 0.0 < self.smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0, 1]")

    @property
    def thresholds(self) -> Thresholds:
        return thresholds_for(self.exercise, self.view)

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def smoothed_angle(self) -> float | None:
        return self._smoothed

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            self.reset_cycle()
            self._smoothed = None
            self._phase = Phase.UNKNOWN

    def reset_cycle(self) -> None:
        self._reached_down = False
        self._down_started_ms = None
        self._rep_started_ms = None
        self._bottom_angle = None
        self._cycle_posture_valid = True
        self._confidence_sum = 0.0
        self._confidence_count = 0

    def update(
        self,
        t_ms: int,
        angle: float | None,
        confidence: float,
        posture_valid: bool = True,
    ) -> list[RepCompleted | FormAlert]:
        if self.paused or angle is None or confidence < self.min_confidence:
            return []

        if self._smoothed is None:
            self._smoothed = angle
        else:
            self._smoothed = (
                self.smoothing_alpha * angle
                + (1.0 - self.smoothing_alpha) * self._smoothed
            )
        current = float(self._smoothed)
        thresholds = self.thresholds

        if self._phase is Phase.UNKNOWN:
            if current >= thresholds.up_enter:
                self._phase = Phase.UP
                self._last_top_ms = t_ms
            elif current <= thresholds.down_enter and posture_valid:
                self._phase = Phase.DOWN
                self._reached_down = True
                self._down_started_ms = t_ms
                self._rep_started_ms = t_ms
                self._bottom_angle = current
                self._cycle_posture_valid = posture_valid
                self._confidence_sum = confidence
                self._confidence_count = 1
            return []

        if self._phase is Phase.UP:
            if current <= thresholds.down_enter and posture_valid:
                self._phase = Phase.DOWN
                self._reached_down = True
                self._down_started_ms = t_ms
                self._rep_started_ms = (
                    self._last_top_ms
                    if self._last_top_ms is not None
                    else t_ms
                )
                self._bottom_angle = current
                self._cycle_posture_valid = posture_valid
                self._confidence_sum = confidence
                self._confidence_count = 1
            return []

        if self._bottom_angle is None or current < self._bottom_angle:
            self._bottom_angle = current
        if not posture_valid:
            self._cycle_posture_valid = False
        self._confidence_sum += confidence
        self._confidence_count += 1

        if current < thresholds.up_enter:
            return []

        self._phase = Phase.UP
        down_start_ms = (
            self._down_started_ms
            if self._down_started_ms is not None
            else t_ms
        )
        rep_start_ms = (
            self._rep_started_ms
            if self._rep_started_ms is not None
            else down_start_ms
        )
        down_duration_ms = max(0, t_ms - down_start_ms)
        rep_duration_ms = max(1, t_ms - rep_start_ms)
        mean_confidence = (
            self._confidence_sum / self._confidence_count
            if self._confidence_count
            else confidence
        )
        shallow = (
            self._bottom_angle is None
            or self._bottom_angle > thresholds.shallow_bottom
        )
        cooldown_ok = (
            self._last_rep_end_ms is None
            or (t_ms - self._last_rep_end_ms) / 1000.0
            >= thresholds.rep_cooldown_s
        )
        cycle_ok = (
            self._reached_down
            and down_duration_ms / 1000.0 >= thresholds.min_down_s
            and rep_duration_ms / 1000.0 >= thresholds.min_rep_s
            and cooldown_ok
        )

        events: list[RepCompleted | FormAlert] = []
        if cycle_ok and self._cycle_posture_valid:
            rep_id = f"r_{secrets.token_hex(5)}"
            record = RepCompleted(
                rep_id=rep_id,
                start_ms=max(0, rep_start_ms),
                end_ms=max(0, t_ms),
                duration_ms=rep_duration_ms,
                confidence=round(min(1.0, mean_confidence), 6),
            )
            events.append(record)
            self.reps += 1
            self.last_rep_id = rep_id
            self._last_rep_end_ms = t_ms
            if shallow:
                code = f"{self.exercise}_depth_insufficient"
                message = "下蹲深度不够" if self.exercise == "squat" else "俯卧撑深度不够"
                severity = "medium" if self._bottom_angle is not None else "low"
                joints = (
                    ("left_knee", "right_knee")
                    if self.exercise == "squat"
                    else ("left_elbow", "right_elbow")
                )
                events.append(
                    FormAlert(
                        code=code,
                        severity=severity,
                        message=message,
                        confidence=round(
                            min(
                                1.0,
                                max(
                                    0.5,
                                    (self._bottom_angle or 180.0)
                                    / thresholds.shallow_bottom,
                                ),
                            ),
                            6,
                        ),
                        joints=joints,
                    )
                )

        self._last_top_ms = t_ms
        self.reset_cycle()
        return events

    def display_phase(self) -> Phase:
        if self._smoothed is None or self._phase is Phase.UNKNOWN:
            return Phase.UNKNOWN
        thresholds = self.thresholds
        if self._phase is Phase.UP:
            return Phase.UP if self._smoothed >= thresholds.up_exit else Phase.TRANSITION
        return (
            Phase.DOWN
            if self._smoothed <= thresholds.down_exit
            else Phase.TRANSITION
        )
