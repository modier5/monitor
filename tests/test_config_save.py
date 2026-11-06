from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from watchdog_perf.config import (
    AppSettings,
    ConditionRule,
    DingTalkSettings,
    EndConditionSettings,
    load_settings,
    save_settings,
)


class ConfigSaveTests(unittest.TestCase):
    def test_complete_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            data_dir = Path(tmp) / "custom-data"
            settings = AppSettings(
                mode="tree",
                processes=("ffmpeg.exe", "Google Chrome"),
                exe_paths=("/Applications/Demo.app",),
                pids=(1234, 5678),
                include_children=False,
                interval_sec=2.5,
                data_dir=data_dir,
                disk_limit_mb=256,
                fsync_every_rows=5,
                backup_every_rows=500,
                backup_keep=7,
                self_status_interval_sec=90,
                end_condition=EndConditionSettings(
                    enabled=True,
                    combine="any",
                    sustained_sec=60,
                    cooldown_sec=120,
                    action="notify_and_stop",
                    targets_gone_enabled=True,
                    targets_gone_grace_sec=15,
                    rules=[
                        ConditionRule(
                            metric="memory_mb",
                            operator=">=",
                            value=2048,
                            enabled=True,
                        )
                    ],
                ),
                dingtalk=DingTalkSettings(
                    enabled=True,
                    webhook="https://example.invalid/hook",
                    secret="secret-value",
                    timeout_sec=8,
                ),
                config_path=config_path,
            )

            saved = save_settings(settings)
            loaded = load_settings(saved)

            self.assertEqual(loaded.processes, settings.processes)
            self.assertEqual(loaded.exe_paths, settings.exe_paths)
            self.assertEqual(loaded.pids, settings.pids)
            self.assertEqual(loaded.data_dir, data_dir)
            self.assertEqual(loaded.backup_keep, 7)
            self.assertTrue(loaded.end_condition.targets_gone_enabled)
            self.assertEqual(loaded.end_condition.rules[0].metric, "memory_mb")
            self.assertEqual(loaded.dingtalk.secret, "secret-value")

            raw = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("monitoring", raw)
            self.assertIn("end_condition", raw)
            self.assertIn("notify", raw)
            self.assertEqual(raw["monitoring"]["data_dir"], str(data_dir))

    def test_save_settings_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "config.json"
            saved = save_settings(AppSettings(), path)
            self.assertEqual(saved, path)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
