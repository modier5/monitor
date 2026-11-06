"""Platform-specific writable locations used by desktop bundles."""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path


APP_DIR_NAME = "WatchdogPerf"


def app_support_dir(
    platform_name: str | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return a per-user writable directory without requiring it to exist."""
    platform_name = platform_name or sys.platform
    home = home or Path.home()
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    if platform_name == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / APP_DIR_NAME
        return home / "AppData" / "Local" / APP_DIR_NAME
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    return Path(xdg_data_home) / APP_DIR_NAME if xdg_data_home else home / ".local" / "share" / APP_DIR_NAME


def ensure_desktop_config(root: Path | None = None) -> Path:
    """Create the first-run desktop config in a writable user directory."""
    from .config import DEFAULT_CONFIG, write_default_config

    root = root or app_support_dir()
    config_path = root / "config.json"
    if config_path.exists():
        return config_path
    data = deepcopy(DEFAULT_CONFIG)
    data["monitoring"]["data_dir"] = "data"
    return write_default_config(config_path, data)


def desktop_argv(arguments: list[str], *, frozen: bool | None = None) -> list[str]:
    """Translate a Finder launch into the normal CLI GUI command."""
    filtered = [arg for arg in arguments if not arg.startswith("-psn_")]
    if filtered:
        return filtered
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if sys.platform in {"darwin", "win32"} and frozen:
        return ["gui", "--config", str(ensure_desktop_config())]
    return ["gui"]
