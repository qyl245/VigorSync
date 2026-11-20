from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.cue_policy import CuePolicy, SpeechCue
from vigorsync_live.speech import SpeechQueue
from vigorsync_live.state_machine import FormAlert


class BlockingSpeechBackend:
    name = "blocking-test"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.warmed: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.stop_count = 0
        self.closed = False

    def speak(self, text: str) -> None:
        self.calls.append(text)
        self.started.set()
        self.release.wait(2.0)

    def warm(self, texts: tuple[str, ...]) -> None:
        self.warmed.extend(texts)

    def stop(self) -> None:
        self.stop_count += 1
        self.release.set()

    def close(self) -> None:
        self.closed = True


class FailingSpeechBackend:
    name = "failing-test"

    def speak(self, text: str) -> None:
        del text
        raise RuntimeError("SAPI unavailable")

    def warm(self, texts: tuple[str, ...]) -> None:
        del texts

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


class CuePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = CuePolicy(
            min_confidence=0.75,
            code_cooldown_ms=5000,
            global_cooldown_ms=3000,
        )

    def alert(
        self,
        *,
        code: str = "squat_knee_valgus",
        confidence: float = 0.9,
        severity: str = "high",
    ) -> FormAlert:
        return FormAlert(
            code=code,
            severity=severity,
            message="膝盖向外",
            confidence=confidence,
            joints=("left_knee",),
        )

    def test_form_alerts_filter_confidence_and_cooldowns(self) -> None:
        self.assertIsNone(self.policy.form_alert(self.alert(confidence=0.74), 0))
        first = self.policy.form_alert(self.alert(), 100)
        self.assertIsNotNone(first)
        self.assertEqual(0, first.priority)
        self.assertIsNone(self.policy.form_alert(self.alert(), 5099))
        self.assertIsNone(
            self.policy.form_alert(
                self.alert(code="squat_torso_lean_excess"),
                3099,
            )
        )
        second = self.policy.form_alert(
            self.alert(code="squat_torso_lean_excess"),
            3100,
        )
        self.assertIsNotNone(second)
        repeated = self.policy.form_alert(self.alert(), 6100)
        self.assertIsNotNone(repeated)

    def test_only_five_ten_or_target_milestones_are_emitted(self) -> None:
        emitted = {}
        for reps in range(1, 13):
            cue = self.policy.rep_milestone(reps, 12, reps * 1000)
            if cue is not None:
                emitted[reps] = cue.text
        self.assertEqual(
            {5: "完成五次", 10: "完成十次", 12: "目标完成"},
            emitted,
        )
        self.assertIsNone(self.policy.rep_milestone(5, 12, 13000))

        target_policy = CuePolicy()
        self.assertEqual(
            "目标完成",
            target_policy.rep_milestone(8, 8, 1000).text,
        )
        self.assertEqual(
            -1,
            target_policy.rep_milestone(9, 9, 2000).priority,
        )


class SpeechQueueTests(unittest.TestCase):
    def test_prewarm_runs_before_queued_speech(self) -> None:
        backend = BlockingSpeechBackend()
        speech = SpeechQueue(backend, max_size=2)
        speech.warm(("完成五次", "目标完成"))
        wait_for(lambda: backend.warmed == ["完成五次", "目标完成"])
        self.assertTrue(speech.submit(SpeechCue("目标完成", -1, "milestone")))
        self.assertTrue(backend.started.wait(1.0))
        backend.release.set()
        speech.close()

    def test_submit_is_async_and_mute_clears_pending_cues(self) -> None:
        backend = BlockingSpeechBackend()
        speech = SpeechQueue(backend, max_size=2)
        first = SpeechCue("收紧核心", 0, "form", "pushup_hip_sag")
        self.assertTrue(speech.submit(first))
        self.assertTrue(backend.started.wait(1.0))
        self.assertTrue(speech.submit(SpeechCue("再低一点", 1, "form")))

        started = time.monotonic()
        muted = speech.set_enabled(False)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(muted["enabled"])
        self.assertEqual("muted", muted["state"])
        self.assertEqual(0, muted["queued"])
        self.assertFalse(speech.submit(SpeechCue("膝盖向外", 0, "form")))

        wait_for(lambda: speech.status()["current"] is None)
        self.assertEqual(["收紧核心"], backend.calls)
        speech.close()
        self.assertTrue(backend.closed)

    def test_full_queue_keeps_higher_priority_cue(self) -> None:
        backend = BlockingSpeechBackend()
        speech = SpeechQueue(backend, max_size=2)
        self.assertTrue(speech.submit(SpeechCue("正在播报", 0, "form")))
        self.assertTrue(backend.started.wait(1.0))
        self.assertTrue(speech.submit(SpeechCue("低优先级", 4, "milestone")))
        self.assertTrue(speech.submit(SpeechCue("中优先级", 2, "form")))
        self.assertTrue(speech.submit(SpeechCue("高优先级", 1, "form")))
        self.assertEqual(2, speech.status()["queued"])

        backend.release.set()
        wait_for(lambda: len(backend.calls) >= 3)
        self.assertEqual(
            ["正在播报", "高优先级", "中优先级"],
            backend.calls,
        )
        speech.close()

    def test_backend_failure_is_contained(self) -> None:
        speech = SpeechQueue(FailingSpeechBackend())
        self.assertTrue(speech.submit(SpeechCue("收紧核心", 0, "form")))
        wait_for(lambda: speech.status()["state"] == "unavailable")
        status = speech.status()
        self.assertFalse(status["available"])
        self.assertIn("SAPI unavailable", status["last_error"])
        self.assertFalse(speech.submit(SpeechCue("再低一点", 1, "form")))
        speech.close()


if __name__ == "__main__":
    unittest.main()
