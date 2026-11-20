#!/usr/bin/env python3
"""Check or synchronize shared issue metadata into vendored VLS configs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ISSUES_ROOT = ROOT / "configs" / "issues"
VLS_CONFIGS = (
    ROOT
    / "services"
    / "offline-worker"
    / "vendor"
    / "vls"
    / "configs"
)
TAXONOMY_PATH = VLS_CONFIGS / "issue_taxonomy.json"
TEMPLATES_PATH = VLS_CONFIGS / "feedback_templates_v2.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def shared_issues() -> dict[str, dict[str, Any]]:
    issues: dict[str, dict[str, Any]] = {}
    for exercise in ("squat", "pushup"):
        payload = load_json(ISSUES_ROOT / f"{exercise}.json")
        for issue in payload["issues"]:
            item = dict(issue)
            item["exercise"] = exercise
            issues[item["code"]] = item
    return issues


def synchronize(
    catalog: dict[str, dict[str, Any]],
    taxonomy: dict[str, Any],
    templates: dict[str, Any],
) -> list[str]:
    changes: list[str] = []
    unordered_fields = {"views", "metrics", "highlight_joints"}

    def differs(key: str, actual: Any, expected: Any) -> bool:
        if key in unordered_fields:
            return set(actual or []) != set(expected or [])
        return actual != expected

    taxonomy_issues = taxonomy["issues"]
    template_issues = templates["issues"]
    offline_codes = {
        code
        for code, issue in catalog.items()
        if issue["offline_supported"]
    }
    for code in sorted(set(taxonomy_issues) - offline_codes):
        del taxonomy_issues[code]
        changes.append(f"taxonomy removed {code}")
    for code in sorted(set(template_issues) - offline_codes):
        del template_issues[code]
        changes.append(f"template removed {code}")

    for code in sorted(offline_codes):
        issue = catalog[code]
        taxonomy_issue = taxonomy_issues.setdefault(
            code,
            {
                "annotation_scope": ["rep", "clip"],
            },
        )
        expected_taxonomy = {
            "action": issue["exercise"],
            "family": issue["family"],
            "views": issue["views"],
            "metrics": issue["offline_metrics"],
            "definition": issue["detail"],
        }
        for key, value in expected_taxonomy.items():
            if differs(key, taxonomy_issue.get(key), value):
                taxonomy_issue[key] = value
                changes.append(f"taxonomy {code}.{key}")

        template = template_issues.setdefault(
            code,
            {
                "detail_template": issue["detail"],
            },
        )
        expected_template = {
            "title": issue["title"],
            "short_cue": issue["short_cue"],
            "cue": issue["correction"],
            "limitation": issue["limitation"],
            "highlight_joints": issue["offline_joints"],
        }
        for key, value in expected_template.items():
            if differs(key, template.get(key), value):
                template[key] = value
                changes.append(f"template {code}.{key}")

    for code, issue in sorted(catalog.items()):
        if issue["live_supported"] and not issue["offline_supported"]:
            if code in taxonomy_issues or code in template_issues:
                raise ValueError(
                    f"live-only issue leaked into offline config: {code}"
                )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check",
        action="store_true",
        help="Fail when vendored VLS issue metadata has drifted.",
    )
    action.add_argument(
        "--write",
        action="store_true",
        help="Write synchronized metadata into vendored VLS configs.",
    )
    args = parser.parse_args()

    catalog = shared_issues()
    taxonomy = load_json(TAXONOMY_PATH)
    templates = load_json(TEMPLATES_PATH)
    changes = synchronize(catalog, taxonomy, templates)
    if args.write:
        if changes:
            write_json(TAXONOMY_PATH, taxonomy)
            write_json(TEMPLATES_PATH, templates)
        print(f"issue configs synchronized: {len(changes)} change(s)")
        for change in changes:
            print(f"- {change}")
        return 0
    if changes:
        print(
            f"issue config drift detected: {len(changes)} change(s)",
            file=sys.stderr,
        )
        for change in changes:
            print(f"- {change}", file=sys.stderr)
        print(
            "run scripts/sync_issue_configs.py --write",
            file=sys.stderr,
        )
        return 1
    print(
        f"issue configs aligned: {len(catalog)} shared codes, "
        f"{sum(issue['offline_supported'] for issue in catalog.values())} offline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
