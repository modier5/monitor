from __future__ import annotations

import unittest

from watchdog_perf.gui import target_labels, target_placeholder


class GuiPlatformLabelsTests(unittest.TestCase):
    def test_windows_uses_exe_language(self) -> None:
        labels = target_labels("win32")
        self.assertEqual(labels["exe"], "Windows 程序 (.exe)")
        self.assertIn(".exe", target_placeholder("exe", "win32"))

    def test_macos_uses_app_bundle_language(self) -> None:
        labels = target_labels("darwin")
        self.assertEqual(labels["exe"], "macOS 应用 (.app)")
        self.assertIn("/Applications", target_placeholder("exe", "darwin"))
        self.assertNotIn(".exe", target_placeholder("exe", "darwin"))

    def test_process_name_hint_is_platform_specific(self) -> None:
        self.assertIn("Google Chrome", target_placeholder("name", "darwin"))
        self.assertIn("chrome.exe", target_placeholder("name", "win32"))


if __name__ == "__main__":
    unittest.main()
