from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.state_machine import (
    FormAlert,
    RepCompleted,
    StateMachine,
    thresholds_for,
)


def feed(
    machine: StateMachine,
    angle: float,
    start_ms: int,
    end_ms: int,
    step_ms: int = 50,
    confidence: float = 0.95,
):
    events = []
    for t_ms in range(start_ms, end_ms + 1, step_ms):
        events.extend(machine.update(t_ms, angle, confidence))
    return events


class StateMachineTests(unittest.TestCase):
    def test_complete_squat_counts_once(self) -> None:
        machine = StateMachine("squat")
        feed(machine, 175, 0, 400)
        feed(machine, 80, 500, 1100)
        events = feed(machine, 175, 1200, 2100)
        self.assertEqual(1, machine.reps)
        self.assertEqual(1, sum(isinstance(item, RepCompleted) for item in events))
        feed(machine, 175, 2200, 3200)
        self.assertEqual(1, machine.reps, "staying at the top must not duplicate")

    def test_shallow_rep_emits_depth_alert(self) -> None:
        machine = StateMachine("squat")
        feed(machine, 175, 0, 300)
        feed(machine, 108, 400, 1000)
        events = feed(machine, 175, 1800, 2800)
        reps = [item for item in events if isinstance(item, RepCompleted)]
        alerts = [item for item in events if isinstance(item, FormAlert)]
        self.assertEqual(1, len(reps))
        self.assertEqual("squat_depth_insufficient", alerts[0].code)

    def test_low_confidence_does_not_count(self) -> None:
        machine = StateMachine("squat")
        feed(machine, 175, 0, 300)
        feed(machine, 70, 400, 1200, confidence=0.2)
        feed(machine, 175, 1300, 2200, confidence=0.2)
        self.assertEqual(0, machine.reps)

    def test_pause_resets_partial_cycle(self) -> None:
        machine = StateMachine("squat")
        feed(machine, 175, 0, 300)
        feed(machine, 80, 400, 800)
        machine.set_paused(True)
        machine.set_paused(False)
        feed(machine, 175, 1200, 2200)
        self.assertEqual(0, machine.reps)

    def test_front_squat_uses_camera_view_thresholds(self) -> None:
        machine = StateMachine("squat", view="front")
        feed(machine, 100, 0, 400)
        feed(machine, 25, 500, 1300)
        events = feed(machine, 100, 1400, 2500)
        self.assertEqual(1, machine.reps)
        self.assertEqual(1, sum(isinstance(item, RepCompleted) for item in events))

    def test_fast_top_bounce_is_not_counted_as_rep(self) -> None:
        machine = StateMachine(
            "squat",
            view="front",
            smoothing_alpha=1.0,
        )
        feed(machine, 100, 0, 300)
        feed(machine, 25, 500, 650)
        events = feed(machine, 100, 700, 800)
        self.assertEqual(0, machine.reps)
        self.assertEqual([], events)

    def test_pushup_uses_side_specific_thresholds(self) -> None:
        right = thresholds_for("pushup", "side_right")
        left = thresholds_for("pushup", "side_left")
        self.assertEqual(148.0, right.up_enter)
        self.assertEqual(0.80, right.min_rep_s)
        self.assertEqual(145.0, left.up_enter)
        self.assertEqual(0.30, left.min_rep_s)


if __name__ == "__main__":
    unittest.main()
