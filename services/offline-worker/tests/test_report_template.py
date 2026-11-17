from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_offline import app as adapter  # noqa: F401 - initializes vendor path
from app.repchain.feedback_builder import build_report_html, build_report_timeline


class ReportTemplateTests(unittest.TestCase):
    def test_branded_report_contains_summary_and_issue(self) -> None:
        feedback = {
            "clip_id": "job_test",
            "exercise": "squat",
            "view": "front",
            "state_message": "研究候选反馈",
            "assessment_summary": {
                "rep_count": 10,
                "evaluable_metric_count": 6,
                "quality_status": "warn",
            },
            "cards": [
                {
                    "issue_id": "squat_knee_valgus_01",
                    "severity": "medium",
                    "confidence": 0.65,
                    "title": "膝盖内扣",
                    "detail": "rep 8 存在偏差",
                    "cue": "让膝盖朝向脚尖",
                    "limitation": "仅正面视角评估",
                    "representative_rep_id": 8,
                    "frequency": "4/10 reps",
                    "anchor_time_s": 3.2,
                    "evidence_interval_s": [3.0, 3.4],
                    "anchor_fallback": False,
                    "frame_available": False,
                    "frame_filename": None,
                }
            ],
            "limitations": [],
        }
        timeline = build_report_timeline(
            {
                "source_measurement": {
                    "input_contract": {
                        "fps": 30.0,
                        "frame_count": 762,
                    }
                },
                "reps": [
                    {
                        "rep_id": 8,
                        "start_frame": 470,
                        "end_frame": 534,
                        "bottom_frame": 487,
                        "boundary_confidence": 0.74439,
                        "status": "complete",
                    }
                ],
            }
        )
        html = build_report_html(
            feedback,
            media_url="/api/v1/report/media",
            frame_url_template="/api/v1/report/frames/{frame_name}",
            timeline=timeline,
        )
        self.assertIn("VIGORSYNC", html)
        self.assertIn("离线复盘", html)
        self.assertIn("膝盖内扣", html)
        self.assertIn("class=\"issue-card medium\"", html)
        self.assertIn("object-fit:contain", html)
        self.assertIn("REP TIMELINE", html)
        self.assertIn("\"rep_id\":8", html)
        self.assertIn("\"start_s\":15.666667", html)
        self.assertIn("player.addEventListener('timeupdate'", html)


if __name__ == "__main__":
    unittest.main()
