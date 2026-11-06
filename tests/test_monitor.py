from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import datetime
from unittest import mock

import psutil

from watchdog_perf.monitor import (
    MonitorConfig,
    PerfMonitor,
    _Tracked,
    _matches_exe_path,
)


class ExePathMatchingTests(unittest.TestCase):
    def test_exact_exe_path_matching_uses_platform_case_rules(self) -> None:
        self.assertTrue(_matches_exe_path(sys.executable, (sys.executable,)))
        if sys.platform == "win32":
            self.assertTrue(_matches_exe_path(sys.executable.upper(), (sys.executable,)))

    def test_macos_app_bundle_matches_its_inner_executable(self) -> None:
        with mock.patch("watchdog_perf.monitor.sys.platform", "darwin"):
            self.assertTrue(
                _matches_exe_path(
                    "/Applications/Demo.app/Contents/MacOS/Demo",
                    ("/Applications/Demo.app",),
                )
            )

    def test_exe_paths_count_as_process_targets(self) -> None:
        config = MonitorConfig(exe_paths=(sys.executable,))
        self.assertTrue(config.has_process_targets)

    def test_exe_scan_discovers_running_process(self) -> None:
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            # macOS Framework Python may launch the child from a different
            # real executable path than sys.executable.  Ask the OS for the
            # child's actual path so this remains an exact-path discovery test.
            helper_exe = psutil.Process(helper.pid).exe()
            monitor = PerfMonitor(
                MonitorConfig(exe_paths=(helper_exe,), interval_sec=0.2),
                store=None,
            )
            monitor._scan_exe_pids()
            self.assertIn(helper.pid, monitor._cached_exe_pids)
        finally:
            helper.kill()
            helper.wait(timeout=3)


class PerfMonitorTests(unittest.TestCase):
    def test_tracked_extra_includes_exe_path(self) -> None:
        state = _Tracked(
            name="ffmpeg.exe",
            create_time=1.0,
            handle=None,
            exe_path=r"D:\media\ffmpeg.exe",
            root_pid=100,
            root_name="ffmpeg.exe",
            parent_pid=50,
            depth=0,
        )
        extra = PerfMonitor._extra(state)
        self.assertIn(r"exe_path=D:\media\ffmpeg.exe", extra)
        self.assertIn("root_pid=100", extra)

    def test_process_target_sample_also_includes_system_row(self) -> None:
        monitor = PerfMonitor(
            MonitorConfig(pids=(12345,)),
            store=None,
        )
        monitor._collect_process_rows = lambda now: [
            ("demo.exe", 12345, 200.0, 100.0, 0, "root_pid=12345")
        ]
        monitor._collect_system_row = lambda: [
            ("SYSTEM", -1, 30.0, 8000.0, 0, "cpu_cores=8")
        ]
        rows = monitor._collect_once(datetime(2026, 9, 9, 1, 0, 0))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "demo.exe")
        self.assertEqual(rows[1][0], "SYSTEM")
        self.assertEqual(rows[1][1], -1)


if __name__ == "__main__":
    unittest.main()
