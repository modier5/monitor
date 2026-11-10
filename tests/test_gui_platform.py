from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from watchdog_perf.gui import (
    app_macos_directory,
    bundle_program_detail,
    describe_target,
    enclosing_app_bundle,
    list_bundle_executables,
    looks_like_program,
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
        self.assertIn("包内程序", hint)
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
        """The in-app picker must find programs without any file dialog."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            macos_dir = bundle / "Contents" / "MacOS"
            (macos_dir / "Helper").write_text("#!/bin/sh\n", encoding="utf-8")
            (macos_dir / "notes.txt").write_text("notes\n", encoding="utf-8")

            candidates = list_bundle_executables(bundle)
            names = [item.name for item in candidates]
            self.assertEqual(names, ["Demo", "Helper"])
            # Works from an inner executable path as well as the bundle root.
            self.assertEqual(
                [item.name for item in list_bundle_executables(macos_dir / "Demo")],
                names,
            )

    def test_finds_programs_outside_contents_macos(self) -> None:
        """Helper binaries may live in Resources, XPCServices or nested apps."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            resources = bundle / "Contents" / "Resources"
            resources.mkdir()
            (resources / "ffmpeg").write_text("#!/bin/sh\n", encoding="utf-8")
            xpc = bundle / "Contents" / "XPCServices" / "Worker.xpc" / "Contents" / "MacOS"
            xpc.mkdir(parents=True)
            (xpc / "Worker").write_text("#!/bin/sh\n", encoding="utf-8")

            names = [item.name for item in list_bundle_executables(bundle)]
            self.assertIn("ffmpeg", names)
            self.assertIn("Worker", names)
            # Main executable still sorts first.
            self.assertEqual(names[0], "Demo")

    def test_skips_code_signature_and_non_programs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            signature = bundle / "Contents" / "_CodeSignature"
            signature.mkdir()
            (signature / "script").write_text("#!/bin/sh\n", encoding="utf-8")
            resources = bundle / "Contents" / "Resources"
            resources.mkdir()
            (resources / "logo.png").write_bytes(b"\x89PNG\r\n")

            names = [item.name for item in list_bundle_executables(bundle)]
            self.assertNotIn("script", names)
            self.assertNotIn("logo.png", names)

    def test_detects_macho_executables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "bin"
            header = (
                (0xFEEDFACF).to_bytes(4, "big")
                + (0x0100000C).to_bytes(4, "little")
                + (0x00000000).to_bytes(4, "little")
                + (0x00000002).to_bytes(4, "little")
            )
            binary.write_bytes(header + b"\x00" * 32)
            self.assertTrue(looks_like_program(binary))

            dylib = root / "lib.dylib"
            dylib.write_bytes(
                (0xFEEDFACF).to_bytes(4, "big")
                + (0x0100000C).to_bytes(4, "little")
                + (0x00000000).to_bytes(4, "little")
                + (0x00000006).to_bytes(4, "little")
                + b"\x00" * 32
            )
            self.assertFalse(looks_like_program(dylib))

            text = root / "readme.txt"
            text.write_text("plain text\n", encoding="utf-8")
            self.assertFalse(looks_like_program(text))

    def test_lists_nothing_for_missing_or_plain_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "demo"
            plain.write_text("demo", encoding="utf-8")
            self.assertEqual(list_bundle_executables(plain), [])
            broken = root / "Broken.app"
            broken.mkdir()
            self.assertEqual(list_bundle_executables(broken), [])

    def test_bundle_program_detail_shows_relative_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._make_bundle(Path(tmp), "Demo", "Demo")
            detail = bundle_program_detail(bundle / "Contents" / "MacOS" / "Demo", bundle)
            self.assertTrue(detail.startswith("Contents/MacOS/Demo"))
            self.assertIn("KB", detail)


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
