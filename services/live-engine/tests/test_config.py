from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vigorsync_live.config import DEFAULT_CAMERA_INDEX, Settings


class SettingsTests(unittest.TestCase):
    def test_default_camera_index_is_configured_usb_camera(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(1, DEFAULT_CAMERA_INDEX)
        self.assertEqual(DEFAULT_CAMERA_INDEX, settings.camera_index)

    def test_camera_index_can_be_overridden_for_reordered_devices(self) -> None:
        with patch.dict(
            os.environ,
            {"VIGORSYNC_CAMERA_INDEX": "0"},
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(0, settings.camera_index)


if __name__ == "__main__":
    unittest.main()
