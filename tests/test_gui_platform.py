from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from watchdog_perf.gui import (
    app_macos_directory,
    bundle_executable_label,
    describe_target,
    enclosing_app_bundle,
    list_bundle_executables,
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

    def test_finds_bundle_and_macos_directory_from_inner_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            executable = bundle / "Contents" / "MacOS" / "Demo"
            self.assertEqual(enclosing_app_bundle(executable), bundle)
            self.assertEqual(
                app_macos_directory(executable),
                bundle / "Contents" / "MacOS",
            )

    def test_non_bundle_has_no_macos_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo"
            path.write_text("demo", encoding="utf-8")
            self.assertIsNone(enclosing_app_bundle(path))
            self.assertIsNone(app_macos_directory(path))

    def test_lists_bundle_programs_with_main_first(self) -> None:
        """The in-app picker must see Contents/MacOS without any file dialog."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            macos_dir = bundle / "Contents" / "MacOS"
            (macos_dir / "Helper").write_text("#!/bin/sh\n", encoding="utf-8")
            (macos_dir / "notes.txt").write_text("notes\n", encoding="utf-8")

            candidates = list_bundle_executables(bundle)
            names = [item.name for item in candidates]
            self.assertEqual(set(names), {"Demo", "Helper", "notes.txt"})
            self.assertEqual(names[0], "Demo")
            # Works from an inner executable path as well as the bundle root.
            self.assertEqual(
                [item.name for item in list_bundle_executables(macos_dir / "Demo")],
                names,
            )

    def test_lists_nothing_for_missing_or_plain_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "demo"
            plain.write_text("demo", encoding="utf-8")
            self.assertEqual(list_bundle_executables(plain), [])
            broken = root / "Broken.app"
            broken.mkdir()
            self.assertEqual(list_bundle_executables(broken), [])

    def test_bundle_executable_label_mentions_name_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            label = bundle_executable_label(bundle / "Contents" / "MacOS" / "Demo")
            self.assertTrue(label.startswith("Demo ·"))
            self.assertIn("KB", label)


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
