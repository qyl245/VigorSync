from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_contracts as contracts  # noqa: E402


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = contracts.load_schemas()
        cls.registry = contracts.build_registry(cls.schemas)

    def test_all_contracts_validate(self) -> None:
        self.assertEqual([], contracts.check_contracts())

    def test_session_rejects_unknown_field(self) -> None:
        payload = contracts.load_json(
            contracts.EXAMPLES_DIR / "session-created.json"
        )
        payload["surprise"] = True
        errors = contracts.validate_payload(
            payload,
            self.schemas["session.schema.json"],
            self.registry,
        )
        self.assertTrue(errors)

    def test_live_event_rejects_confidence_above_one(self) -> None:
        payload = contracts.load_json(
            contracts.EXAMPLES_DIR / "live-metrics.json"
        )
        payload["payload"]["confidence"] = 1.01
        errors = contracts.validate_payload(
            payload,
            self.schemas["live-event.schema.json"],
            self.registry,
        )
        self.assertTrue(errors)

    def test_live_event_rejects_unknown_issue_code(self) -> None:
        payload = contracts.load_json(
            contracts.EXAMPLES_DIR / "form-alert.json"
        )
        payload["payload"]["code"] = "depth_insufficient"
        errors = contracts.validate_payload(
            payload,
            self.schemas["live-event.schema.json"],
            self.registry,
        )
        self.assertTrue(errors)

    def test_issue_catalog_matches_contract_and_runtime_capabilities(
        self,
    ) -> None:
        _, catalogs = contracts.load_issue_catalogs()
        issues = [
            issue
            for catalog in catalogs.values()
            for issue in catalog["issues"]
        ]
        self.assertEqual(
            {
                issue["code"]
                for issue in issues
                if issue["live_supported"]
            },
            {
                "squat_depth_insufficient",
                "squat_torso_lean_excess",
                "squat_knee_valgus",
                "pushup_depth_insufficient",
                "pushup_hip_sag",
                "pushup_hip_pike",
                "pushup_elbow_flare",
            },
        )
        self.assertEqual(
            14,
            len(contracts.issue_catalog_codes(catalogs)),
        )

    def test_state_machine_rejects_terminal_transition(self) -> None:
        machine = copy.deepcopy(contracts.load_json(contracts.STATE_MACHINE_PATH))
        machine["transitions"].append(
            {
                "from": "completed",
                "to": "active",
                "trigger": "command.resume",
            }
        )
        original = contracts.STATE_MACHINE_PATH
        temp_path = contracts.EXAMPLES_DIR / ".invalid-state-machine.json"
        try:
            temp_path.write_text(
                contracts.json.dumps(machine, ensure_ascii=False),
                encoding="utf-8",
            )
            contracts.STATE_MACHINE_PATH = temp_path
            errors = contracts.validate_state_machine(self.schemas, self.registry)
        finally:
            contracts.STATE_MACHINE_PATH = original
            temp_path.unlink(missing_ok=True)
        self.assertTrue(any("terminal state" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
