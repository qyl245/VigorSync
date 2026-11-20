from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_offline import app as adapter  # noqa: F401
from vigorsync_offline.realtime_bridge import enrich_job_realtime_review


class RealtimeBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.job_id = "job_0123456789abcdef"
        self.job_dir = Path(self.temp.name) / "jobs" / self.job_id
        self.artifacts = self.job_dir / "artifacts" / self.job_id
        self.artifacts.mkdir(parents=True)
        (self.job_dir / "input").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_inputs(self) -> None:
        events = [
            {
                "type": "form.alert",
                "t_ms": 1200,
                "payload": {
                    "code": "pushup_hip_sag",
                    "severity": "high",
                    "message": "收紧核心",
                },
            },
            {
                "type": "form.alert",
                "t_ms": 3400,
                "payload": {
                    "code": "pushup_hip_pike",
                    "severity": "medium",
                    "message": "髋部放低",
                },
            },
            {
                "type": "form.alert",
                "t_ms": 4600,
                "payload": {
                    "code": "pushup_hip_sag",
                    "severity": "high",
                    "message": "收紧核心",
                },
            },
        ]
        (self.job_dir / "input" / "realtime-events.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in events)
            + "\nnot-json\n",
            encoding="utf-8",
        )
        feedback = {
            "clip_id": self.job_id,
            "exercise": "pushup",
            "view": "side_left",
            "state_message": "研究候选反馈",
            "assessment_summary": {
                "rep_count": 5,
                "evaluable_metric_count": 4,
                "quality_status": "pass",
            },
            "cards": [
                {
                    "issue_id": "pushup_hip_sag_01",
                    "issue_code": "pushup_hip_sag",
                    "severity": "high",
                    "confidence": 0.8,
                    "title": "塌腰",
                    "detail": "髋部低于身体线",
                    "cue": "收紧核心",
                    "limitation": "离线阈值与实时阈值不同",
                    "representative_rep_id": 3,
                    "frequency": "2/5 reps",
                    "anchor_time_s": 4.1,
                    "evidence_interval_s": [3.8, 4.4],
                    "anchor_fallback": False,
                    "frame_available": False,
                    "frame_filename": None,
                }
            ],
            "limitations": [],
        }
        assessment = {
            "source_measurement": {
                "input_contract": {
                    "fps": 30.0,
                    "frame_count": 300,
                }
            },
            "reps": [
                {
                    "rep_id": 3,
                    "start_frame": 90,
                    "end_frame": 150,
                    "bottom_frame": 120,
                    "boundary_confidence": 0.8,
                    "status": "complete",
                }
            ],
        }
        (self.artifacts / "feedback_v2.json").write_text(
            json.dumps(feedback, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.artifacts / "assessment_v2.json").write_text(
            json.dumps(assessment, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.job_dir / "complete.json").write_text(
            json.dumps({"job_id": self.job_id, "artifacts": []}),
            encoding="utf-8",
        )

    def test_realtime_codes_are_matched_without_semantic_merging(
        self,
    ) -> None:
        self.write_inputs()
        review = enrich_job_realtime_review(
            self.job_dir,
            adapter.PROJECT_ROOT,
        )
        self.assertIsNotNone(review)
        self.assertEqual(3, review["event_count"])
        self.assertEqual(["pushup_hip_sag"], review["matched_codes"])
        self.assertEqual(["pushup_hip_pike"], review["live_only_codes"])
        self.assertEqual(1, review["parse_errors"])

        feedback = json.loads(
            (self.artifacts / "feedback_v2.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            review,
            feedback["realtime_review"],
        )
        report = (self.artifacts / "report_v2.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("实时提示关联", report)
        self.assertIn("实时也检测到 2 次", report)
        self.assertIn("离线未形成问题卡", report)

        complete = json.loads(
            (self.job_dir / "complete.json").read_text(encoding="utf-8")
        )
        names = {item["name"] for item in complete["artifacts"]}
        self.assertIn("realtime_review.json", names)
        self.assertIn("report_v2.html", names)

    def test_missing_events_do_not_fail_offline_result(self) -> None:
        self.write_inputs()
        (self.job_dir / "input" / "realtime-events.jsonl").unlink()
        review = enrich_job_realtime_review(
            self.job_dir,
            adapter.PROJECT_ROOT,
        )
        self.assertEqual("missing", review["state"])
        self.assertEqual([], review["matched_codes"])


if __name__ == "__main__":
    unittest.main()
