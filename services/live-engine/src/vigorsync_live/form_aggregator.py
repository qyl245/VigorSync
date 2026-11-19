"""Temporal stability and cooldown for real-time form faults."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .form_rules import FormFault


@dataclass(slots=True)
class _FaultWindow:
    present: deque[bool] = field(default_factory=deque)
    last_seen_ms: int = -1_000_000
    cooldown_until_ms: int = -1_000_000
    latest: FormFault | None = None


class FaultAggregator:
    """Emit a fault only when it persists in a recent M-of-N window."""

    def __init__(
        self,
        *,
        required_hits: int = 4,
        window_size: int = 6,
        cooldown_ms: int = 4000,
        active_hold_ms: int = 1500,
    ) -> None:
        if not 1 <= required_hits <= window_size:
            raise ValueError("required_hits must be within the window")
        self.required_hits = required_hits
        self.window_size = window_size
        self.cooldown_ms = cooldown_ms
        self.active_hold_ms = active_hold_ms
        self._windows: dict[str, _FaultWindow] = {}

    def update(
        self,
        candidates: Iterable[FormFault],
        evaluated_codes: Iterable[str],
        t_ms: int,
    ) -> tuple[FormFault, ...]:
        by_code = {fault.code: fault for fault in candidates}
        evaluated = set(evaluated_codes)
        emitted = []

        for code in evaluated:
            state = self._windows.setdefault(
                code,
                _FaultWindow(present=deque(maxlen=self.window_size)),
            )
            current = by_code.get(code)
            state.present.append(current is not None)
            if current is not None:
                state.latest = current
                state.last_seen_ms = t_ms
            if (
                current is not None
                and sum(state.present) >= self.required_hits
                and t_ms >= state.cooldown_until_ms
            ):
                emitted.append(current)
                state.cooldown_until_ms = t_ms + self.cooldown_ms

        return tuple(emitted)

    def active_joints(self, t_ms: int) -> frozenset[str]:
        joints = set()
        for state in self._windows.values():
            if (
                state.latest is not None
                and t_ms - state.last_seen_ms <= self.active_hold_ms
            ):
                joints.update(state.latest.joints)
        return frozenset(joints)

    def reset(self) -> None:
        self._windows.clear()
