from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from watchdog_perf.config import (
    AppSettings,
    load_settings,
    save_appearance,
    save_settings,
)


class AppearanceSettingTests(unittest.TestCase):
    def test_default_is_follow_system(self) -> None:
        settings = AppSettings()
        self.assertEqual(settings.appearance, "system")
        self.assertEqual(settings_to_appearance(settings), "system")

    def test_load_reads_appearance_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps({"appearance": "dark", "monitoring": {"interval_sec": 2}}),
                encoding="utf-8",
            )
            self.assertEqual(load_settings(config).appearance, "dark")

    def test_load_rejects_unknown_appearance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"appearance": "purple"}), encoding="utf-8")
            self.assertEqual(load_settings(config).appearance, "system")

    def test_missing_appearance_falls_back_to_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"monitoring": {}}), encoding="utf-8")
            self.assertEqual(load_settings(config).appearance, "system")

    def test_save_settings_keeps_appearance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            settings = AppSettings(appearance="light", config_path=config)
            save_settings(settings)
            data = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(data["appearance"], "light")

    def test_save_appearance_preserves_other_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "appearance": "system",
                        "monitoring": {"processes": ["ffmpeg"], "data_dir": "data"},
                        "notify": {"dingtalk": {"webhook": "https://example.test"}},
                    }
                ),
                encoding="utf-8",
            )
            save_appearance("dark", config)
            data = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(data["appearance"], "dark")
            self.assertEqual(data["monitoring"]["processes"], ["ffmpeg"])
            self.assertEqual(data["notify"]["dingtalk"]["webhook"], "https://example.test")

    def test_save_appearance_creates_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "nested" / "config.json"
            save_appearance("light", config)
            self.assertTrue(config.exists())
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["appearance"], "light")


def settings_to_appearance(settings: AppSettings) -> str:
    return settings.appearance


if __name__ == "__main__":
    unittest.main()
