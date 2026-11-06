"""Desktop/PyInstaller entry point.

With no arguments a normal script run opens the GUI.  A macOS Finder launch
also provisions a writable config/data directory under Application Support.
"""

from __future__ import annotations

import sys

from watchdog_perf.cli import main
from watchdog_perf.platform_paths import desktop_argv

raise SystemExit(main(desktop_argv(sys.argv[1:])))
