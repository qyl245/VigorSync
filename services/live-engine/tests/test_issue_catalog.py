from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.issue_catalog import (
    catalog_for_exercise,
    issue_for,
    live_speech_texts,
    load_issue_catalog,
)


class IssueCatalogTests(unittest.TestCase):
    def test_catalog_contains_all_live_and_offline_codes(self) -> None:
        catalog = load_issue_catalog()
        self.assertEqual(14, len(catalog))
        self.assertEqual(
            {"squat", "pushup"},
            {issue.exercise for issue in catalog.values()},
        )

    def test_live_only_codes_are_explicit(self) -> None:
        self.assertTrue(issue_for("pushup_hip_pike").live_supported)
        self.assertFalse(issue_for("pushup_hip_pike").offline_supported)
        self.assertTrue(issue_for("pushup_elbow_flare").live_supported)
        self.assertFalse(issue_for("pushup_elbow_flare").offline_supported)

    def test_realtime_metadata_comes_from_catalog(self) -> None:
        knee_valgus = issue_for("squat_knee_valgus")
        self.assertEqual("膝盖向外", knee_valgus.short_cue)
        self.assertEqual("high", knee_valgus.default_severity)
        self.assertEqual(
            ("left_knee", "right_knee"),
            knee_valgus.live_joints,
        )
        self.assertIn(
            "squat_knee_valgus",
            catalog_for_exercise("squat"),
        )

    def test_speech_warmup_uses_catalog_short_cues(self) -> None:
        texts = set(live_speech_texts())
        live_cues = {
            issue.short_cue
            for issue in load_issue_catalog().values()
            if issue.live_supported
        }
        self.assertTrue(live_cues <= texts)
        self.assertTrue(
            {"完成五次", "完成十次", "目标完成"} <= texts
        )


if __name__ == "__main__":
    unittest.main()
