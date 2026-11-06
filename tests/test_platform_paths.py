from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from watchdog_perf.platform_paths import (
    app_support_dir,
    desktop_argv,
    ensure_desktop_config,
)


class PlatformPathTests(unittest.TestCase):
    def test_macos_application_support_location(self) -> None:
        home = Path("/Users/demo")
        self.assertEqual(
            app_support_dir("darwin", home=home),
            home / "Library" / "Application Support" / "WatchdogPerf",
        )

    def test_first_run_config_uses_relative_writable_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = ensure_desktop_config(Path(tmp) / "WatchdogPerf")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["monitoring"]["data_dir"], "data")
            self.assertEqual(ensure_desktop_config(path.parent), path)

    def test_finder_launch_opens_gui_with_user_config(self) -> None:
        expected = Path("/Users/demo/Library/Application Support/WatchdogPerf/config.json")
        with mock.patch("watchdog_perf.platform_paths.sys.platform", "darwin"), mock.patch(
            "watchdog_perf.platform_paths.ensure_desktop_config",
            return_value=expected,
        ):
            self.assertEqual(
                desktop_argv(["-psn_0_12345"], frozen=True),
                ["gui", "--config", str(expected)],
            )


if __name__ == "__main__":
    unittest.main()
