from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import json

from watchdog_perf.alerting import (
    DingTalkNotifier,
    EndConditionMonitor,
)
from watchdog_perf.config import (
    ConditionRule,
    DingTalkSettings,
    EndConditionSettings,
    load_settings,
    write_default_config,
)
from watchdog_perf.storage import STATUS_NOT_FOUND, STATUS_OK


class AlertingTests(unittest.TestCase):
    def test_condition_triggers_only_after_sustained_seconds(self) -> None:
        settings = EndConditionSettings(
            enabled=True,
            combine="all",
            sustained_sec=1.0,
            cooldown_sec=3600.0,
            action="notify_and_stop",
            rules=[
                ConditionRule(
                    metric="cpu_machine_percent",
                    operator=">=",
                    value=50.0,
                )
            ],
        )
        monitor = EndConditionMonitor(settings, cpu_count=1)
        row = ("demo.exe", 100, 80.0, 128.0, 0, "")
        start = datetime(2026, 9, 8, 10, 0, 0)

        first = monitor.record(start, [row])
        self.assertIsNone(first)
        second = monitor.record(start + timedelta(seconds=0.6), [row])
        self.assertIsNone(second)
        third = monitor.record(start + timedelta(seconds=1.2), [row])
        self.assertIsNotNone(third)
        self.assertTrue(third.stop_requested)
        self.assertGreaterEqual(third.summary.rows_written, 3)
        self.assertGreaterEqual(third.summary.memory_max, 128.0)

    def test_dingtalk_signature_contains_timestamp_and_sign(self) -> None:
        settings = DingTalkSettings(
            enabled=True,
            webhook="https://oapi.example/robot/send?access_token=abc",
            secret="secret-value",
        )
        url = DingTalkNotifier(settings).signed_url()
        self.assertIn("timestamp=", url)
        self.assertIn("sign=", url)
        self.assertTrue(url.startswith("https://oapi.example/robot/send?access_token=abc"))

    def test_config_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            write_default_config(path)
            settings = load_settings(path)
            self.assertEqual(settings.end_condition.combine, "all")
            self.assertEqual(settings.fsync_every_rows, 1)
            self.assertFalse(settings.dingtalk.enabled)

    def test_targets_gone_waits_for_first_appearance_then_grace(self) -> None:
        settings = EndConditionSettings(
            enabled=True,
            action="notify_and_stop",
            sustained_sec=60.0,
            cooldown_sec=3600.0,
            targets_gone_enabled=True,
            targets_gone_grace_sec=1.0,
        )
        monitor = EndConditionMonitor(settings, cpu_count=1)
        start = datetime(2026, 9, 8, 11, 0, 0)

        # The target may appear minutes after startup; absence is not a reason
        # to finish before we have ever seen it.
        self.assertIsNone(monitor.record(start, []))
        self.assertIsNone(monitor.record(start + timedelta(seconds=30), []))

        live_row = ("demo.exe", 100, 20.0, 128.0, STATUS_OK, "")
        self.assertIsNone(monitor.record(start + timedelta(seconds=31), [live_row]))

        exit_row = (
            "demo.exe",
            100,
            "",
            "",
            STATUS_NOT_FOUND,
            "process_exit",
        )
        self.assertIsNone(monitor.record(start + timedelta(seconds=32), [exit_row]))
        self.assertIsNone(monitor.record(start + timedelta(seconds=32.5), []))

        alert = monitor.record(start + timedelta(seconds=34), [])
        self.assertIsNotNone(alert)
        self.assertTrue(alert.stop_requested)
        self.assertIn("全部退出", alert.condition_text)
        self.assertGreaterEqual(alert.summary.exit_events, 1)

    def test_rules_do_not_trigger_while_target_has_never_appeared(self) -> None:
        settings = EndConditionSettings(
            enabled=True,
            combine="all",
            sustained_sec=0.0,
            action="notify_and_stop",
            rules=[
                ConditionRule(
                    metric="process_count",
                    operator="<=",
                    value=0.0,
                )
            ],
        )
        monitor = EndConditionMonitor(settings, cpu_count=1)
        start = datetime(2026, 9, 8, 11, 30, 0)
        self.assertIsNone(monitor.record(start, []))
        self.assertIsNone(monitor.record(start + timedelta(seconds=1), []))
        self.assertIsNone(monitor.record(start + timedelta(seconds=2), []))

    def test_apps_are_flattened_into_root_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "monitoring": {
                            "exe_paths": [
                                "D:\\Program Files\\FanxiangCloud\\ai-manager.exe"
                            ],
                            "apps": [
                                {
                                    "name": "FanxiangCloud",
                                    "roots": [
                                        "FanxiangCloud*.exe",
                                        "ai-manager.exe",
                                    ],
                                }
                            ]
                        },
                        "end_condition": {
                            "enabled": True,
                            "rules": [],
                            "targets_gone": {
                                "enabled": True,
                                "grace_sec": 20.0,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            settings = load_settings(path)
            self.assertEqual(len(settings.apps), 1)
            self.assertEqual(
                settings.exe_paths,
                ("D:\\Program Files\\FanxiangCloud\\ai-manager.exe",),
            )
            self.assertIn("FanxiangCloud*.exe", settings.processes)
            self.assertIn("ai-manager.exe", settings.processes)
            self.assertTrue(settings.end_condition.targets_gone_enabled)
            self.assertEqual(
                settings.end_condition.targets_gone_grace_sec,
                20.0,
            )


if __name__ == "__main__":
    unittest.main()
