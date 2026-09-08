from __future__ import annotations

import subprocess
import sys
import unittest

from watchdog_perf.monitor import MonitorConfig, PerfMonitor, _matches_exe_path


class ExePathMatchingTests(unittest.TestCase):
    def test_exact_exe_path_matching_is_case_insensitive(self) -> None:
        self.assertTrue(_matches_exe_path(sys.executable, (sys.executable,)))
        self.assertTrue(_matches_exe_path(sys.executable.upper(), (sys.executable,)))

    def test_exe_paths_count_as_process_targets(self) -> None:
        config = MonitorConfig(exe_paths=(sys.executable,))
        self.assertTrue(config.has_process_targets)

    def test_exe_scan_discovers_running_process(self) -> None:
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(8)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            monitor = PerfMonitor(
                MonitorConfig(exe_paths=(sys.executable,), interval_sec=0.2),
                store=None,
            )
            monitor._scan_exe_pids()
            self.assertIn(helper.pid, monitor._cached_exe_pids)
        finally:
            helper.kill()
            helper.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
