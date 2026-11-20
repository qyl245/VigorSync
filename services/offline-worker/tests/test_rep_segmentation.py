from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_offline import app as adapter  # noqa: F401 - initializes vendor path
from analysis_v1 import find_phase_cycle_boundaries


class RepSegmentationTests(unittest.TestCase):
    def test_pose_gap_does_not_hide_adjacent_troughs(self) -> None:
        frames = np.arange(360, dtype=np.float64)
        phase = np.full_like(frames, 160.0)
        for center, amplitude in ((60, 120.0), (125, 50.0), (190, 115.0), (255, 115.0)):
            phase -= amplitude * np.exp(-0.5 * ((frames - center) / 6.0) ** 2)
        phase[184:189] = np.nan

        boundaries, evidence = find_phase_cycle_boundaries(
            phase,
            fps=30.0,
            min_period_s=0.4,
            max_period_s=5.0,
        )

        self.assertEqual("applied", evidence["status"])
        self.assertEqual(
            [60, 125, 190, 255],
            evidence["trough_frames"],
        )
        self.assertEqual(5, len(boundaries))
        self.assertEqual(5, evidence["interpolated_missing_frames"])


if __name__ == "__main__":
    unittest.main()
