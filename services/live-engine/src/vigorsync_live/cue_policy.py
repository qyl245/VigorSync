"""Policy for turning live events into concise spoken cues."""

from __future__ import annotations

from dataclasses import dataclass

from .state_machine import FormAlert


KNOWN_SPEECH_TEXTS = (
    "膝盖向外",
    "胸口抬起",
    "收紧核心",
    "髋部放低",
    "手肘内收",
    "下蹲深度不够",
    "俯卧撑深度不够",
    "完成五次",
    "完成十次",
    "目标完成",
)


@dataclass(frozen=True, slots=True)
class SpeechCue:
    text: str
    priority: int
    kind: str
    code: str | None = None


class CuePolicy:
    """Apply confidence, cooldown and milestone rules before TTS submission."""

    _SEVERITY_PRIORITY = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }

    def __init__(
        self,
        *,
        min_confidence: float = 0.75,
        code_cooldown_ms: int = 5000,
        global_cooldown_ms: int = 3000,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        if code_cooldown_ms < 0 or global_cooldown_ms < 0:
            raise ValueError("cooldowns must not be negative")
        self.min_confidence = min_confidence
        self.code_cooldown_ms = code_cooldown_ms
        self.global_cooldown_ms = global_cooldown_ms
        self._last_code_ms: dict[str, int] = {}
        self._last_cue_ms = -1_000_000
        self._milestones: set[int] = set()

    def form_alert(self, alert: FormAlert, t_ms: int) -> SpeechCue | None:
        if alert.confidence < self.min_confidence:
            return None
        last_code = self._last_code_ms.get(alert.code, -1_000_000)
        if t_ms - last_code < self.code_cooldown_ms:
            return None
        if t_ms - self._last_cue_ms < self.global_cooldown_ms:
            return None
        self._last_code_ms[alert.code] = t_ms
        self._last_cue_ms = t_ms
        return SpeechCue(
            text=alert.message,
            priority=self._SEVERITY_PRIORITY.get(alert.severity, 2),
            kind="form",
            code=alert.code,
        )

    def rep_milestone(
        self,
        reps: int,
        target_reps: int,
        t_ms: int,
    ) -> SpeechCue | None:
        del t_ms
        if reps <= 0 or reps in self._milestones:
            return None
        text = None
        priority = 3
        if reps == target_reps:
            text = "目标完成"
            priority = -1
        elif reps == 10:
            text = "完成十次"
        elif reps == 5:
            text = "完成五次"
        if text is None:
            return None
        self._milestones.add(reps)
        return SpeechCue(
            text=text,
            priority=priority,
            kind="milestone",
            code=f"reps_{reps}",
        )

    def reset(self) -> None:
        self._last_code_ms.clear()
        self._last_cue_ms = -1_000_000
        self._milestones.clear()
