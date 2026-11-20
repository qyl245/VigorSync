from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_issue_configs as sync  # noqa: E402


class IssueConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = sync.shared_issues()
        cls.taxonomy = sync.load_json(sync.TAXONOMY_PATH)["issues"]
        cls.templates = sync.load_json(sync.TEMPLATES_PATH)["issues"]

    def test_vendored_configs_are_synchronized(self) -> None:
        taxonomy = sync.load_json(sync.TAXONOMY_PATH)
        templates = sync.load_json(sync.TEMPLATES_PATH)
        self.assertEqual(
            [],
            sync.synchronize(self.catalog, taxonomy, templates),
        )

    def test_offline_supported_codes_match_both_directions(self) -> None:
        expected = {
            code
            for code, issue in self.catalog.items()
            if issue["offline_supported"]
        }
        self.assertEqual(expected, set(self.taxonomy))
        self.assertEqual(expected, set(self.templates))

    def test_shared_short_cues_and_corrections_match_vls(self) -> None:
        for code, issue in self.catalog.items():
            if not issue["offline_supported"]:
                continue
            with self.subTest(code=code):
                template = self.templates[code]
                self.assertEqual(issue["title"], template["title"])
                self.assertEqual(issue["short_cue"], template["short_cue"])
                self.assertEqual(issue["correction"], template["cue"])
                self.assertEqual(issue["limitation"], template["limitation"])
                self.assertEqual(
                    set(issue["offline_joints"]),
                    set(template["highlight_joints"]),
                )

    def test_realtime_only_codes_do_not_enter_vls(self) -> None:
        for code, issue in self.catalog.items():
            if issue["live_supported"] and not issue["offline_supported"]:
                with self.subTest(code=code):
                    self.assertNotIn(code, self.taxonomy)
                    self.assertNotIn(code, self.templates)


if __name__ == "__main__":
    unittest.main()
