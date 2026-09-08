"""JSON configuration layer.

All long-running parameters are read from config.json (or the path passed with
--config). CLI arguments still win when explicitly provided.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CONFIG: dict[str, Any] = {
    "monitoring": {
        "mode": "tree",
        "processes": [],
        "apps": [],
        "exe_paths": [],
        "pids": [],
        "include_children": True,
        "interval_sec": 1.0,
        "data_dir": "data",
        "disk_limit_mb": 100,
        "fsync_every_rows": 1,
        "self_status_interval_sec": 300.0,
    },
    "end_condition": {
        "enabled": False,
        "combine": "all",
        "sustained_sec": 300.0,
        "cooldown_sec": 3600.0,
        "action": "notify_and_stop",
        "rules": [
            {
                "metric": "cpu_machine_percent",
                "operator": ">=",
                "value": 85.0,
                "enabled": True,
            },
            {
                "metric": "memory_mb",
                "operator": ">=",
                "value": 4096.0,
                "enabled": False,
            },
        ],
    },
    "notify": {
        "dingtalk": {
            "enabled": False,
            "webhook": "",
            "secret": "",
            "timeout_sec": 10,
        }
    },
}


@dataclass
class ConditionRule:
    metric: str = "cpu_machine_percent"
    operator: str = ">="
    value: float = 85.0
    enabled: bool = True


@dataclass
class AppTargetSettings:
    """Human-readable application target that expands to root process patterns."""

    name: str = ""
    roots: tuple[str, ...] = ()
    enabled: bool = True


@dataclass
class EndConditionSettings:
    enabled: bool = False
    combine: str = "all"
    sustained_sec: float = 300.0
    cooldown_sec: float = 3600.0
    action: str = "notify_and_stop"
    rules: list[ConditionRule] = field(default_factory=list)
    targets_gone_enabled: bool = False
    targets_gone_grace_sec: float = 30.0


@dataclass
class DingTalkSettings:
    enabled: bool = False
    webhook: str = ""
    secret: str = ""
    timeout_sec: float = 10.0

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.webhook)


@dataclass
class AppSettings:
    mode: str = "tree"
    processes: tuple[str, ...] = ()
    apps: tuple[AppTargetSettings, ...] = ()
    exe_paths: tuple[str, ...] = ()
    pids: tuple[int, ...] = ()
    include_children: bool = True
    interval_sec: float = 1.0
    data_dir: Path = Path("data")
    disk_limit_mb: int = 100
    fsync_every_rows: int = 1
    self_status_interval_sec: float = 300.0
    end_condition: EndConditionSettings = field(default_factory=EndConditionSettings)
    dingtalk: DingTalkSettings = field(default_factory=DingTalkSettings)
    config_path: Path | None = None

    @property
    def has_process_targets(self) -> bool:
        return bool(self.processes or self.pids or self.exe_paths)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _as_path_list(value: Any) -> tuple[str, ...]:
    """Keep executable paths intact; a string is one path, not a comma list."""
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip().strip('"')
        return (text,) if text else ()
    if isinstance(value, list):
        paths = []
        for item in value:
            text = str(item).strip().strip('"')
            if text:
                paths.append(text)
        return tuple(paths)
    return ()


def _as_pid_list(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (int, str)):
        try:
            return (int(value),)
        except (TypeError, ValueError):
            return ()
    if isinstance(value, list):
        pids: list[int] = []
        for item in value:
            try:
                pids.append(int(item))
            except (TypeError, ValueError):
                continue
        return tuple(pids)
    return ()


def _nested(data: dict, key: str) -> dict:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _load_rules(raw: Any) -> list[ConditionRule]:
    if not isinstance(raw, list):
        return []
    rules: list[ConditionRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rules.append(
            ConditionRule(
                metric=str(item.get("metric", "cpu_machine_percent")),
                operator=str(item.get("operator", ">=")),
                value=_as_float(item.get("value"), 0.0),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return rules


def _load_apps(raw: Any) -> tuple[AppTargetSettings, ...]:
    """Parse monitoring.apps entries.

    Each app is only a naming layer: its roots expand to ordinary process name
    patterns, so all existing tree/dynamic-discovery logic keeps working.
    """
    if not isinstance(raw, list):
        return ()
    apps: list[AppTargetSettings] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "未命名应用").strip()
        roots_raw = item.get("roots")
        if roots_raw is None:
            roots_raw = item.get("root_processes") or item.get("processes")
        roots = _as_str_list(roots_raw)
        if not roots:
            continue
        if not name or name in seen_names:
            name = f"{name or '应用'}-{len(apps) + 1}"
        seen_names.add(name)
        apps.append(
            AppTargetSettings(
                name=name,
                roots=roots,
                enabled=bool(item.get("enabled", True)),
            )
        )
    return tuple(apps)


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """Load config.json by default. Missing optional config falls back to defaults."""
    explicit = config_path is not None
    path = Path(config_path) if config_path is not None else Path("config.json")
    if not path.exists():
        if explicit:
            raise FileNotFoundError(f"配置文件不存在: {path}")
        return AppSettings()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件 JSON 语法错误: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件顶层必须是 JSON 对象: {path}")

    monitoring = _nested(raw, "monitoring")
    condition_raw = _nested(raw, "end_condition")
    notify_raw = _nested(raw, "notify")
    ding_raw = _nested(notify_raw, "dingtalk")

    mode = str(monitoring.get("mode", "tree")).lower()
    if mode == "apps":
        mode = "tree"
    include_children = bool(monitoring.get("include_children", True))
    data_dir_raw = str(monitoring.get("data_dir", "data") or "data")
    data_dir = Path(data_dir_raw)
    if not data_dir.is_absolute():
        base = path.resolve().parent if path.exists() else Path.cwd()
        data_dir = base / data_dir

    targets_gone_raw = _nested(condition_raw, "targets_gone")
    targets_gone_enabled = bool(targets_gone_raw.get("enabled", False))
    targets_gone_grace_sec = _as_float(targets_gone_raw.get("grace_sec"), 30.0)
    end_condition = EndConditionSettings(
        enabled=bool(condition_raw.get("enabled", False)),
        combine=str(condition_raw.get("combine", "all")).lower(),
        sustained_sec=_as_float(condition_raw.get("sustained_sec"), 300.0),
        cooldown_sec=_as_float(condition_raw.get("cooldown_sec"), 3600.0),
        action=str(condition_raw.get("action", "notify_and_stop")),
        rules=_load_rules(condition_raw.get("rules")),
        targets_gone_enabled=targets_gone_enabled,
        targets_gone_grace_sec=max(0.0, targets_gone_grace_sec),
    )
    if (
        not end_condition.rules
        and condition_raw.get("enabled")
        and not targets_gone_enabled
    ):
        end_condition.rules = _load_rules(DEFAULT_CONFIG["end_condition"]["rules"])

    dingtalk = DingTalkSettings(
        enabled=bool(ding_raw.get("enabled", False)),
        webhook=str(ding_raw.get("webhook", "") or "").strip(),
        secret=str(ding_raw.get("secret", "") or "").strip(),
        timeout_sec=_as_float(ding_raw.get("timeout_sec"), 10.0),
    )

    base_processes = _as_str_list(monitoring.get("processes"))
    apps = _load_apps(monitoring.get("apps"))
    process_patterns = list(base_processes)
    if apps and mode not in ("pid", "system"):
        for app in apps:
            if app.enabled:
                process_patterns.extend(app.roots)
    # Keep the list stable and de-duplicated for logging and matching.
    process_patterns = list(dict.fromkeys(process_patterns))

    settings = AppSettings(
        mode=mode,
        processes=tuple(process_patterns),
        apps=apps,
        exe_paths=_as_path_list(monitoring.get("exe_paths")),
        pids=_as_pid_list(monitoring.get("pids")),
        include_children=include_children,
        interval_sec=_as_float(monitoring.get("interval_sec"), 1.0),
        data_dir=data_dir,
        disk_limit_mb=_as_int(monitoring.get("disk_limit_mb"), 100),
        fsync_every_rows=_as_int(monitoring.get("fsync_every_rows"), 1),
        self_status_interval_sec=_as_float(
            monitoring.get("self_status_interval_sec"), 300.0
        ),
        end_condition=end_condition,
        dingtalk=dingtalk,
        config_path=path,
    )
    if settings.interval_sec <= 0:
        raise ValueError("monitoring.interval_sec 必须大于 0")
    return settings


def write_default_config(path: str | Path, data: dict[str, Any] | None = None) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(data or DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
