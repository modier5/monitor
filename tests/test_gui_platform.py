from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from watchdog_perf.gui import (
    describe_target,
    resolve_app_executable,
    target_hint,
    target_labels,
    target_placeholder,
)


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

    def test_macos_hint_explains_bundle_handling(self) -> None:
        hint = target_hint("exe", "darwin")
        self.assertIn(".app", hint)
        self.assertIn("Contents/MacOS", hint)
        self.assertNotIn(".exe", hint)

    def test_windows_hint_mentions_exe(self) -> None:
        self.assertIn(".exe", target_hint("exe", "win32"))


class AppBundleResolutionTests(unittest.TestCase):
    def _make_bundle(self, root: Path, name: str, executable: str, declare: bool = True) -> Path:
        bundle = root / f"{name}.app"
        macos_dir = bundle / "Contents" / "MacOS"
        macos_dir.mkdir(parents=True)
        (macos_dir / executable).write_text("#!/bin/sh\n", encoding="utf-8")
        if declare:
            with (bundle / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleExecutable": executable}, handle)
        return bundle

    def test_reads_declared_executable_from_info_plist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            resolved = resolve_app_executable(bundle)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.name, "Demo")

    def test_falls_back_to_first_file_in_macos_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "helper", declare=False)
            resolved = resolve_app_executable(bundle)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.name, "helper")

    def test_fallback_does_not_require_execute_bit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "helper", declare=False)
            (bundle / "Contents" / "MacOS" / "helper").chmod(0o644)
            resolved = resolve_app_executable(bundle)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.name, "helper")

    def test_rejects_non_bundle_and_broken_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(resolve_app_executable(root / "Demo.exe"))
            broken = root / "Broken.app"
            broken.mkdir()
            self.assertIsNone(resolve_app_executable(broken))


class TargetDescriptionTests(unittest.TestCase):
    def test_macos_bundle_is_described_as_application(self) -> None:
        title, subtitle = describe_target("exe", "/Applications/Google Chrome.app", "darwin")
        self.assertEqual(title, "Google Chrome.app")
        self.assertTrue(subtitle.startswith("应用 · "))

    def test_executable_inside_bundle_is_still_listed(self) -> None:
        inner = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        title, subtitle = describe_target("exe", inner, "darwin")
        self.assertEqual(title, "Google Chrome")
        self.assertTrue(subtitle.startswith("可执行文件 · "))

    def test_windows_path_uses_program_wording(self) -> None:
        title, subtitle = describe_target("exe", r"C:\Apps\demo.exe", "win32")
        self.assertEqual(title, "demo.exe")
        self.assertTrue(subtitle.startswith("程序 · "))

    def test_pid_and_process_name(self) -> None:
        self.assertEqual(describe_target("pid", "1234", "win32")[0], "1234")
        self.assertEqual(describe_target("name", "ffmpeg", "win32")[0], "ffmpeg")


if __name__ == "__main__":
    unittest.main()
