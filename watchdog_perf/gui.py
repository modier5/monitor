"""CustomTkinter desktop control panel for WatchdogPerf.

The visual language follows the ``watchdogperf-ui-concept`` design: a deep
navigation rail, soft neutral canvas, rounded surface panels, a single jade
accent and a tight, platform-aware type scale.
"""

from __future__ import annotations

import ntpath
import os
import plistlib
import posixpath
import queue
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox

from .alerting import DingTalkNotifier, EndConditionMonitor
from .config import AppSettings, ConditionRule, save_settings
from .metrics import machine_cpu_percent
from .monitor import MonitorConfig, PerfMonitor
from .storage import CsvStore, STATUS_OK
from .ui_icons import brand_mark, icon

# ---------------------------------------------------------------------------
# Palette: every colour is a (light, dark) pair so the whole UI can flip theme.
# ---------------------------------------------------------------------------
BG = ("#F4F6FB", "#090D15")
SURFACE = ("#FFFFFF", "#121A27")
SURFACE_ALT = ("#EFF3F9", "#0D1520")
SURFACE_HOVER = ("#E5EBF4", "#182233")
BORDER = ("#E2E8F2", "#1E2938")
BORDER_SOFT = ("#EDF1F8", "#17212E")
TEXT = ("#0F1829", "#F2F6FC")
TEXT_SECONDARY = ("#55647C", "#95A4B9")
MUTED = ("#8090A6", "#7C8CA1")

SIDEBAR_BG = ("#101B2C", "#0D1522")
SIDEBAR_CARD = ("#18273C", "#131E2E")
SIDEBAR_LINE = ("#263650", "#1D2939")
SIDEBAR_TEXT = "#A5B3C6"
SIDEBAR_MUTED = "#7E90A6"
SIDEBAR_HOVER = ("#182840", "#15202F")
NAV_ACTIVE_BG = ("#17383E", "#16323A")

ACCENT = "#0FA396"
ACCENT_HOVER = "#0C887E"
ACCENT_SOFT = ("#E2F6F2", "#10302F")
ACCENT_INK = ("#0A6B63", "#6FE3D2")
ACCENT_TEXT = "#6FE3D2"

BLUE = "#4C8DE6"
BLUE_SOFT = ("#E8F0FD", "#152238")
DANGER = "#CE5561"
DANGER_HOVER = "#B3454F"
DANGER_SOFT = ("#FDECEE", "#331D23")
WARN_BG = ("#FEF7E9", "#2C2416")
WARN_TEXT = ("#8A6216", "#E5C27A")
WHITE = "#FFFFFF"

METRIC_LABELS = {
    "cpu_machine_percent": "CPU 占用率 (%)",
    "memory_mb": "内存占用 (MB)",
}
METRIC_KEYS = {label: key for key, label in METRIC_LABELS.items()}

TARGET_ICONS = {"exe": "app-window", "pid": "hash", "name": "terminal"}

COMBINE_LABELS = {"all": "全部满足", "any": "任一满足"}
ACTION_LABELS = {
    "notify_and_stop": "通知并停止",
    "notify_only": "仅通知",
    "stop": "直接停止",
}
COMBINE_VALUES = {label: value for value, label in COMBINE_LABELS.items()}
ACTION_VALUES = {label: value for value, label in ACTION_LABELS.items()}


def target_labels(platform_name: str | None = None) -> dict[str, str]:
    """Return platform-native labels for the three target selectors."""
    platform_name = platform_name or sys.platform
    program_label = (
        "macOS 应用 (.app)"
        if platform_name == "darwin"
        else "Windows 程序 (.exe)"
        if platform_name == "win32"
        else "应用 / 可执行文件"
    )
    return {"exe": program_label, "pid": "PID", "name": "进程名"}


def target_placeholder(kind: str, platform_name: str | None = None) -> str:
    platform_name = platform_name or sys.platform
    if kind == "pid":
        return "输入 PID，例如 1234"
    if kind == "name":
        return (
            "输入进程名，例如 Google Chrome"
            if platform_name == "darwin"
            else "输入进程名，例如 ffmpeg.exe 或 chrome.exe"
        )
    if platform_name == "darwin":
        return "选择 /Applications 中的 .app 应用程序"
    if platform_name == "win32":
        return "选择 Windows .exe 可执行程序"
    return "选择应用或可执行文件路径"


def target_hint(kind: str, platform_name: str | None = None) -> str:
    """Explain what the selector expects, including the macOS bundle question."""
    platform_name = platform_name or sys.platform
    if kind == "pid":
        return "PID 必须是大于 0 的整数；进程退出后需要重新添加。"
    if kind == "name":
        return "填写活动监视器 / 任务管理器中的进程名，支持 * 通配符，例如 chrome*。"
    if platform_name == "darwin":
        return "直接选择 .app 应用包即可，工具会自动匹配包内 Contents/MacOS 主程序；也可以粘贴包内可执行文件路径。"
    if platform_name == "win32":
        return "选择程序实际的 .exe 文件；程序稍后启动也会被自动发现并开始采集。"
    return "选择应用或可执行文件路径。"


def resolve_app_executable(app_path: str | Path) -> Path | None:
    """Return the main executable inside a macOS ``.app`` bundle, if any."""
    path = Path(app_path)
    if path.suffix.lower() != ".app":
        return None
    macos_dir = path / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        return None
    executable_name = ""
    info_plist = path / "Contents" / "Info.plist"
    if info_plist.is_file():
        try:
            with info_plist.open("rb") as handle:
                executable_name = str(plistlib.load(handle).get("CFBundleExecutable", "") or "")
        except (OSError, plistlib.InvalidFileException, ValueError):
            executable_name = ""
    if executable_name:
        candidate = macos_dir / executable_name
        if candidate.is_file():
            return candidate
    candidates = [item for item in sorted(macos_dir.iterdir()) if item.is_file()]
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            return candidate
    return candidates[0] if candidates else None


def describe_target(kind: str, value: str, platform_name: str | None = None) -> tuple[str, str]:
    """Return the (title, subtitle) pair rendered in the target list."""
    platform_name = platform_name or sys.platform
    if kind == "pid":
        return value, "PID · 结束前持续跟踪指定进程"
    if kind == "name":
        return value, "进程名 · 包含子进程"
    if platform_name == "win32":
        trimmed = value.rstrip("\\/")
        title = ntpath.basename(trimmed) or value
        suffix = ntpath.splitext(trimmed)[1].lower()
    else:
        trimmed = value.rstrip("/")
        title = posixpath.basename(trimmed) or value
        suffix = posixpath.splitext(trimmed)[1].lower()
    if suffix == ".app":
        return title, f"应用 · {value}"
    if platform_name == "darwin":
        return title, f"可执行文件 · {value}"
    return title, f"程序 · {value}"


def choose_macos_target() -> str:
    """Open the native macOS picker, which can select ``.app`` bundles."""
    script = (
        'set picked to choose file with prompt "选择要监控的 .app 应用或可执行文件" '
        'default location (path to applications folder) '
        'of type {"com.apple.application-bundle", "public.unix-executable"}\n'
        "POSIX path of picked"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _truncate(text: str, limit: int = 68) -> str:
    if len(text) <= limit:
        return text
    tail = max(12, limit // 2)
    head = limit - tail - 1
    return f"{text[:head]}…{text[-tail:]}"


class _VarEntry(ctk.CTkEntry):
    """Entry bound to a tk variable that still renders its placeholder.

    CustomTkinter disables placeholder rendering as soon as a ``textvariable``
    is passed, so the binding is done by hand instead.
    """

    def __init__(self, master, variable: tk.Variable, **kwargs) -> None:
        self.variable = variable
        super().__init__(master, **kwargs)
        value = variable.get()
        if value:
            self.insert(0, str(value))
        variable.trace_add("write", self._from_variable)
        self.bind("<KeyRelease>", self._to_variable, add="+")
        self.bind("<FocusOut>", self._to_variable, add="+")

    def _to_variable(self, _event=None) -> None:
        value = self.get()
        if value != self.variable.get():
            self.variable.set(value)

    def _from_variable(self, *_args) -> None:
        value = str(self.variable.get())
        if value == self.get():
            return
        self.delete(0, "end")
        if value:
            self.insert(0, value)


class WatchdogGui:
    def __init__(
        self,
        root: ctk.CTk,
        default_processes: Sequence[str] = (),
        default_interval_sec: float = 1.0,
        data_dir: str | Path = "data",
        disk_limit_mb: int = 100,
        fsync_every_rows: int = 1,
        backup_every_rows: int = 10000,
        backup_keep: int = 3,
        app_settings: AppSettings | None = None,
    ) -> None:
        self.root = root
        self.app_settings = app_settings or AppSettings(
            processes=tuple(default_processes),
            interval_sec=default_interval_sec,
            data_dir=Path(data_dir),
            disk_limit_mb=disk_limit_mb,
            fsync_every_rows=fsync_every_rows,
            backup_every_rows=backup_every_rows,
            backup_keep=backup_keep,
        )
        self.data_dir = Path(data_dir)
        self.disk_limit_mb = disk_limit_mb
        self.fsync_every_rows = max(1, fsync_every_rows)
        self.backup_every_rows = max(0, backup_every_rows)
        self.backup_keep = max(1, backup_keep)
        self._end_condition = self.app_settings.end_condition
        self._dingtalk = self.app_settings.dingtalk

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._history_thread: threading.Thread | None = None
        self._event_queue: queue.Queue = queue.Queue()
        self._sample_count = 0
        self._records_count = 0
        self._last_records: list[dict] = []
        self._last_html_path: Path | None = None
        self._closed = False
        self._poll_job: str | None = None
        self._scroll_sync_job: str | None = None
        self._icon_image: tk.PhotoImage | None = None
        self._active_page = "targets"
        self._nav_items: dict[str, dict] = {}
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._font_cache: dict[tuple[int, str], ctk.CTkFont] = {}
        self._icon_cache: dict[tuple, ctk.CTkImage] = {}
        self.target_records: list[tuple[str, str]] = []
        self.rule_records: list[ConditionRule] = []
        self._seg_value = ""
        self._seg_buttons: dict[str, ctk.CTkButton] = {}
        self.target_labels = target_labels()
        self._font_family = self._pick_font_family()

        self.live_var = tk.StringVar(value="服务就绪")
        self.status_var = tk.StringVar(value=f"数据目录：{self.data_dir}")
        self.count_var = tk.StringVar(value="0 个样本")
        self.target_value_var = tk.StringVar()
        self.interval_var = tk.StringVar(value=f"{self.app_settings.interval_sec:g}")
        self.children_var = tk.BooleanVar(value=self.app_settings.include_children)
        self.data_dir_var = tk.StringVar(value=str(self.app_settings.data_dir))
        self.disk_limit_var = tk.StringVar(value=str(self.app_settings.disk_limit_mb))
        self.fsync_var = tk.StringVar(value=str(self.app_settings.fsync_every_rows))
        self.backup_rows_var = tk.StringVar(value=str(self.app_settings.backup_every_rows))
        self.backup_keep_var = tk.StringVar(value=str(self.app_settings.backup_keep))
        self.self_status_var = tk.StringVar(value=f"{self.app_settings.self_status_interval_sec:g}")

        condition = self.app_settings.end_condition
        self.condition_enabled_var = tk.BooleanVar(value=condition.enabled)
        self.combine_var = tk.StringVar(value=COMBINE_LABELS.get(condition.combine, condition.combine))
        self.sustained_var = tk.StringVar(value=f"{condition.sustained_sec:g}")
        self.cooldown_var = tk.StringVar(value=f"{condition.cooldown_sec:g}")
        self.action_var = tk.StringVar(value=ACTION_LABELS.get(condition.action, condition.action))
        self.targets_gone_var = tk.BooleanVar(value=condition.targets_gone_enabled)
        self.targets_grace_var = tk.StringVar(value=f"{condition.targets_gone_grace_sec:g}")
        self.rule_enabled_var = tk.BooleanVar(value=True)
        self.rule_metric_var = tk.StringVar(value=METRIC_LABELS["cpu_machine_percent"])
        self.rule_operator_var = tk.StringVar(value=">=")
        self.rule_value_var = tk.StringVar(value="85")

        ding = self.app_settings.dingtalk
        self.ding_enabled_var = tk.BooleanVar(value=ding.enabled)
        self.webhook_var = tk.StringVar(value=ding.webhook)
        self.secret_var = tk.StringVar(value=ding.secret)
        self.timeout_var = tk.StringVar(value=f"{ding.timeout_sec:g}")

        self.root.title("WatchdogPerf · 资源监控")
        self.root.configure(fg_color=BG)
        self.root.minsize(1120, 740)
        self._center_window(1280, 840)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_icon_assets()
        self._load_targets()
        self._load_rules()
        self._build_ui()
        self._show_page("targets")

    # ------------------------------------------------------------------ setup
    def _center_window(self, width: int, height: int) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _pick_font_family(self) -> str:
        available = {name for name in tkfont.families(self.root) if not name.startswith("@")}
        if sys.platform == "darwin":
            candidates = ("SF Pro Text", "PingFang SC", "Helvetica Neue", "Helvetica")
        elif sys.platform == "win32":
            candidates = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
        else:
            candidates = ("Noto Sans CJK SC", "Source Han Sans SC", "Noto Sans", "DejaVu Sans")
        for family in candidates:
            if family in available:
                return family
        return "TkDefaultFont"

    def _font(self, size: int = 13, weight: str = "normal") -> ctk.CTkFont:
        key = (size, weight)
        if key not in self._font_cache:
            self._font_cache[key] = ctk.CTkFont(family=self._font_family, size=size, weight=weight)
        return self._font_cache[key]

    def _icon(self, name: str, color, size: int = 18) -> ctk.CTkImage:
        if isinstance(color, tuple):
            light, dark = color
        else:
            light = dark = color
        key = (name, light, dark, size)
        if key not in self._icon_cache:
            self._icon_cache[key] = ctk.CTkImage(
                light_image=icon(name, light, size),
                dark_image=icon(name, dark, size),
                size=(size, size),
            )
        return self._icon_cache[key]

    def _load_icon_assets(self) -> None:
        icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.png"
        if not icon_path.exists():
            return
        try:
            self._icon_image = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._icon_image)
        except (tk.TclError, OSError):
            self._icon_image = None

    # ------------------------------------------------------------- primitives
    def _label(
        self,
        parent,
        text: str = "",
        *,
        size: int = 13,
        weight: str = "normal",
        color=TEXT,
        anchor: str = "w",
        justify: str = "left",
        wraplength: int = 0,
        image=None,
    ) -> ctk.CTkLabel:
        kwargs = {
            "text": text,
            "text_color": color,
            "font": self._font(size, weight),
            "anchor": anchor,
            "justify": justify,
        }
        if wraplength:
            kwargs["wraplength"] = wraplength
        if image is not None:
            kwargs["image"] = image
        return ctk.CTkLabel(parent, **kwargs)

    def _panel(self, parent, **kwargs) -> ctk.CTkFrame:
        options = {
            "fg_color": SURFACE,
            "border_width": 1,
            "border_color": BORDER,
            "corner_radius": 18,
        }
        options.update(kwargs)
        return ctk.CTkFrame(parent, **options)

    def _page(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.page_host, fg_color=BG, corner_radius=0)
        frame.grid(row=0, column=0, sticky="nsew")
        return frame

    def _schedule_scrollbar_sync(self, frame) -> None:
        try:
            self.root.after(140, lambda: self._sync_scrollbar(frame))
        except tk.TclError:
            pass

    def _sync_scrollbar(self, frame) -> None:
        """Hide the scrollbar of a scrollable frame when the content fits."""
        canvas = getattr(frame, "_parent_canvas", None)
        scrollbar = getattr(frame, "_scrollbar", None)
        if canvas is None or scrollbar is None:
            return
        try:
            box = canvas.bbox("all")
            if box is None:
                return
            needed = (box[3] - box[1]) > canvas.winfo_height() + 2
            if needed:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
        except tk.TclError:
            return

    def _on_page_host_resize(self, _event=None) -> None:
        if self._scroll_sync_job is not None:
            try:
                self.root.after_cancel(self._scroll_sync_job)
            except tk.TclError:
                pass
        try:
            self._scroll_sync_job = self.root.after(140, self._sync_all_scrollbars)
        except tk.TclError:
            self._scroll_sync_job = None

    def _sync_all_scrollbars(self) -> None:
        self._scroll_sync_job = None
        for name in ("targets_scroll", "rules_scroll", "storage_scroll"):
            frame = getattr(self, name, None)
            if frame is not None:
                self._sync_scrollbar(frame)

    def _section(self, parent, title: str, subtitle: str = ""):
        box = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0)
        box.grid_columnconfigure(0, weight=1)
        self._label(box, title, size=15, weight="bold").grid(row=0, column=0, sticky="w")
        if subtitle:
            self._label(box, subtitle, size=12, color=TEXT_SECONDARY, wraplength=560).grid(
                row=1, column=0, sticky="w", pady=(4, 0)
            )
        return box

    def _button(
        self,
        parent,
        text: str,
        command,
        *,
        kind: str = "ghost",
        icon_name: str | None = None,
        icon_color=None,
        width: int | None = None,
        height: int = 40,
        size: int = 12,
        bold: bool = False,
    ) -> ctk.CTkButton:
        styles = {
            "ghost": dict(
                fg_color=BG,
                hover_color=SURFACE_ALT,
                text_color=TEXT_SECONDARY,
                border_width=0,
            ),
            "secondary": dict(
                fg_color=SURFACE,
                hover_color=SURFACE_ALT,
                text_color=TEXT,
                border_width=1,
                border_color=BORDER,
            ),
            "primary": dict(
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                text_color=WHITE,
                border_width=0,
            ),
            "danger": dict(
                fg_color=DANGER,
                hover_color=DANGER_HOVER,
                text_color=WHITE,
                border_width=0,
            ),
        }[kind]
        options = dict(
            text=text,
            command=command,
            height=height,
            corner_radius=11,
            font=self._font(size, "bold" if bold else "normal"),
            **styles,
        )
        if icon_name is not None:
            color = icon_color or (WHITE if kind in {"primary", "danger"} else TEXT_SECONDARY)
            options["image"] = self._icon(icon_name, color, size + 4)
            options["compound"] = "left"
        if width is not None:
            options["width"] = width
        else:
            options["width"] = 10
        return ctk.CTkButton(parent, **options)

    def _entry(self, parent, variable, *, placeholder: str = "", height: int = 42, show: str = "", width: int | None = None):
        options = dict(
            height=height,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
            fg_color=SURFACE,
            text_color=TEXT,
            placeholder_text=placeholder,
            placeholder_text_color=MUTED,
            font=self._font(13),
        )
        if show:
            options["show"] = show
        if width is not None:
            options["width"] = width
        return _VarEntry(parent, variable, **options)

    def _field_card(
        self,
        parent,
        label: str,
        hint: str,
        variable,
        *,
        suffix: str = "",
        entry_height: int = 40,
    ) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=SURFACE_ALT, corner_radius=14)
        card.grid_columnconfigure(0, weight=1)
        self._label(card, label, size=13, weight="bold").grid(row=0, column=0, sticky="w", padx=16, pady=(13, 2))
        self._label(card, hint, size=11, color=TEXT_SECONDARY, wraplength=320, justify="left").grid(
            row=1, column=0, sticky="w", padx=16
        )
        row = ctk.CTkFrame(card, fg_color=SURFACE_ALT, corner_radius=0)
        row.grid(row=2, column=0, sticky="ew", padx=16, pady=(9, 13))
        row.grid_columnconfigure(0, weight=1)
        self._entry(row, variable, height=entry_height).grid(row=0, column=0, sticky="ew")
        if suffix:
            self._label(row, suffix, size=11, color=MUTED).grid(row=0, column=1, padx=(8, 2))
        return card

    def _switch(self, parent, text: str, variable) -> ctk.CTkSwitch:
        return ctk.CTkSwitch(
            parent,
            text=text,
            variable=variable,
            progress_color=ACCENT,
            text_color=TEXT_SECONDARY,
            font=self._font(12),
        )

    def _option(self, parent, variable, values: list[str], *, width: int | None = None, height: int = 38) -> ctk.CTkOptionMenu:
        options = dict(
            variable=variable,
            values=values,
            height=height,
            corner_radius=10,
            fg_color=SURFACE_ALT,
            button_color=SURFACE_ALT,
            button_hover_color=SURFACE_HOVER,
            text_color=TEXT,
            font=self._font(12),
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=SURFACE_ALT,
            dropdown_text_color=TEXT,
            dropdown_font=self._font(12),
        )
        if width is not None:
            options["width"] = width
        return ctk.CTkOptionMenu(parent, **options)

    # ------------------------------------------------------------------- shell
    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(0, minsize=244)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        main = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self._build_topbar(main)

        self.page_host = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=30, pady=(20, 0))
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.bind("<Configure>", self._on_page_host_resize)

        self._pages["overview"] = self._build_overview_page()
        self._pages["targets"] = self._build_targets_page()
        self._pages["storage"] = self._build_storage_page()
        self._pages["conditions"] = self._build_conditions_page()
        self._pages["notifications"] = self._build_notifications_page()

        self._build_footer(main)

    def _build_topbar(self, parent) -> None:
        topbar = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        topbar.grid(row=0, column=0, sticky="ew", padx=30, pady=(28, 0))
        topbar.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(topbar, fg_color=BG, corner_radius=0)
        titles.grid(row=0, column=0, sticky="w")
        self.page_title = self._label(titles, "监控目标", size=23, weight="bold")
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = self._label(
            titles,
            "选择需要持续跟踪的应用、进程或 PID。",
            size=12,
            color=TEXT_SECONDARY,
        )
        self.page_subtitle.grid(row=1, column=0, sticky="w", pady=(5, 0))

        badge = ctk.CTkFrame(topbar, fg_color=ACCENT_SOFT, corner_radius=18)
        badge.grid(row=0, column=1, sticky="e", pady=(4, 0))
        badge.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(badge, text="", image=self._icon("dot", ACCENT_INK, 10), width=10).grid(
            row=0, column=0, padx=(14, 8), pady=9
        )
        self.live_label = ctk.CTkLabel(
            badge,
            textvariable=self.live_var,
            text_color=ACCENT_INK,
            font=self._font(12, "bold"),
            anchor="w",
        )
        self.live_label.grid(row=0, column=1, sticky="w", padx=(0, 14))

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self.root, fg_color=SIDEBAR_BG, corner_radius=0, width=244)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color=SIDEBAR_BG, corner_radius=0)
        brand.grid(row=0, column=0, sticky="ew", padx=22, pady=(28, 26))
        brand.grid_columnconfigure(1, weight=1)
        mark = ctk.CTkImage(light_image=brand_mark(38), dark_image=brand_mark(38), size=(38, 38))
        ctk.CTkLabel(brand, text="", image=mark, width=38).grid(row=0, column=0, padx=(0, 12))
        self._label(brand, "WatchdogPerf", size=16, weight="bold", color="#F4FAFF").grid(
            row=0, column=1, sticky="w"
        )

        nav = ctk.CTkFrame(sidebar, fg_color=SIDEBAR_BG, corner_radius=0)
        nav.grid(row=1, column=0, sticky="ew", padx=14)
        nav.grid_columnconfigure(0, weight=1)
        items = (
            ("overview", "gauge", "运行概览"),
            ("targets", "target", "监控目标"),
            ("storage", "database", "采集与存储"),
            ("conditions", "shield", "结束条件"),
            ("notifications", "bell", "通知设置"),
        )
        for row, (key, icon_name, text) in enumerate(items):
            self._nav_item(nav, row, key, icon_name, text)

        footer = ctk.CTkFrame(
            sidebar,
            fg_color=SIDEBAR_CARD,
            border_width=1,
            border_color=SIDEBAR_LINE,
            corner_radius=14,
        )
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 18))
        footer.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(footer, fg_color=SIDEBAR_CARD, corner_radius=0)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(13, 0))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="", image=self._icon("check", ACCENT_TEXT, 14), width=14).grid(row=0, column=0, padx=(0, 7))
        self._label(head, "配置已同步", size=12, weight="bold", color="#E7FBF6").grid(row=0, column=1, sticky="w")
        self.config_sync_label = self._label(
            footer,
            "config.json · 已加载",
            size=11,
            color=SIDEBAR_MUTED,
        )
        self.config_sync_label.grid(row=1, column=0, sticky="w", padx=14, pady=(4, 0))
        self.sidebar_count = self._label(footer, "", size=11, color=SIDEBAR_MUTED)
        self.sidebar_count.grid(row=2, column=0, sticky="w", padx=14, pady=(2, 14))

    def _nav_item(self, parent, row: int, key: str, icon_name: str, text: str) -> None:
        frame = ctk.CTkFrame(parent, fg_color=SIDEBAR_BG, corner_radius=11, height=46)
        frame.grid(row=row, column=0, sticky="ew", pady=2)
        frame.grid_propagate(False)
        frame.grid_columnconfigure(1, weight=1)
        idle = self._icon(icon_name, "#93A5BA", 19)
        active = self._icon(icon_name, ACCENT_TEXT, 19)
        icon_label = ctk.CTkLabel(frame, text="", image=idle, width=19)
        icon_label.grid(row=0, column=0, padx=(15, 12), pady=13)
        text_label = self._label(frame, text, size=13, color=SIDEBAR_TEXT)
        text_label.grid(row=0, column=1, sticky="ew", padx=(0, 14))
        for widget in (frame, icon_label, text_label):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _event, page=key: self._show_page(page))
            widget.bind("<Enter>", lambda _event, page=key: self._nav_hover(page, True))
            widget.bind("<Leave>", lambda _event, page=key: self._nav_hover(page, False))
        self._nav_items[key] = {
            "frame": frame,
            "icon": icon_label,
            "text": text_label,
            "idle": idle,
            "active": active,
        }

    def _nav_hover(self, key: str, entering: bool) -> None:
        if key == self._active_page:
            return
        item = self._nav_items.get(key)
        if item is None:
            return
        item["frame"].configure(fg_color=SIDEBAR_HOVER if entering else SIDEBAR_BG)

    def _set_stop_state(self, enabled: bool) -> None:
        if enabled:
            self.stop_button.configure(
                state="normal",
                fg_color=DANGER,
                hover_color=DANGER_HOVER,
                text_color=WHITE,
            )
        else:
            self.stop_button.configure(
                state="disabled",
                fg_color=SURFACE_ALT,
                hover_color=SURFACE_ALT,
                text_color=MUTED,
            )

    def _build_footer(self, parent) -> None:
        footer = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew", padx=30, pady=(18, 22))
        footer.grid_columnconfigure(0, weight=1)
        status = ctk.CTkLabel(
            footer,
            textvariable=self.status_var,
            text_color=MUTED,
            font=self._font(11),
            anchor="w",
        )
        status.grid(row=0, column=0, sticky="ew", padx=(2, 16))

        self.load_history_button = self._button(footer, "加载 CSV", self._load_history, kind="secondary", width=96)
        self.load_history_button.grid(row=0, column=1, padx=3)
        self.export_button = self._button(footer, "导出报告", self._export_figure, kind="secondary", width=96)
        self.export_button.configure(state="disabled")
        self.export_button.grid(row=0, column=2, padx=3)
        self.save_button = self._button(footer, "保存配置", self._save_config, kind="secondary", width=100)
        self.save_button.grid(row=0, column=3, padx=(14, 3))
        self.stop_button = self._button(
            footer, "停止监控", self._stop_monitor, kind="danger", width=100, bold=True
        )
        self._set_stop_state(False)
        self.stop_button.grid(row=0, column=4, padx=3)
        self.start_button = self._button(
            footer,
            "保存并开始",
            self._start_monitor,
            kind="primary",
            icon_name="play",
            width=132,
            bold=True,
        )
        self.start_button.grid(row=0, column=5, padx=(3, 0))

    # ------------------------------------------------------------------ pages
    def _build_overview_page(self) -> ctk.CTkFrame:
        page = self._page()
        page.grid_columnconfigure((0, 1, 2), weight=1, uniform="stat")
        page.grid_rowconfigure(1, weight=1)
        self.overview_cpu = self._stat_card(page, 0, "整机 CPU", "0.0%", "实时采样", "activity")
        self.overview_memory = self._stat_card(page, 1, "目标内存", "0 MB", "当前进程合计", "database")
        self.overview_samples = self._stat_card(page, 2, "已采集样本", "0", "本次运行", "file")

        panel = self._panel(page)
        panel.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(18, 0))
        panel.grid_columnconfigure(0, weight=3, minsize=460)
        panel.grid_columnconfigure(1, weight=0)
        panel.grid_columnconfigure(2, weight=2, minsize=320)
        panel.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(28, 24), pady=26)
        left.grid_columnconfigure(0, weight=1)
        self._section(left, "运行状态", "启动监控后，这里会显示最新采样和输出文件。").grid(
            row=0, column=0, sticky="ew"
        )
        self.overview_status = self._label(
            left,
            "尚未开始监控",
            size=22,
            weight="bold",
            justify="left",
            wraplength=440,
        )
        self.overview_status.grid(row=1, column=0, sticky="w", pady=(26, 4))

        meta = ctk.CTkFrame(left, fg_color=SURFACE, corner_radius=0)
        meta.grid(row=2, column=0, sticky="ew", pady=(22, 0))
        meta.grid_columnconfigure((0, 1), weight=1, uniform="meta")
        self.overview_file = self._meta_cell(meta, 0, 0, "输出文件", "等待开始")
        self._meta_cell(meta, 0, 1, "采样间隔", f"{self.app_settings.interval_sec:g} 秒")
        self._meta_cell(meta, 1, 0, "数据目录", _truncate(str(self.app_settings.data_dir), 30))
        self._meta_cell(meta, 1, 1, "报告状态", "尚未生成")

        self._button(left, "前往监控目标", lambda: self._show_page("targets"), kind="primary", width=140).grid(
            row=3, column=0, sticky="w", pady=(24, 0)
        )

        tips = ctk.CTkFrame(left, fg_color=SURFACE_ALT, corner_radius=11)
        tips.grid(row=4, column=0, sticky="ew", pady=(22, 0))
        tips.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tips, text="", image=self._icon("dot", ACCENT, 7), width=7).grid(
            row=0, column=0, padx=(14, 10), pady=10
        )
        self._label(
            tips,
            "添加目标后点击「保存并开始」，结束后用「加载 CSV」生成报告。",
            size=11,
            color=TEXT_SECONDARY,
            wraplength=420,
        ).grid(row=0, column=1, sticky="w", padx=(0, 14), pady=10)
        left.grid_rowconfigure(5, weight=1)

        divider = ctk.CTkFrame(panel, fg_color=BORDER, width=1, corner_radius=0)
        divider.grid(row=0, column=1, sticky="ns", pady=26)

        right = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        right.grid(row=0, column=2, sticky="nsew", padx=(24, 28), pady=26)
        right.grid_columnconfigure(0, weight=1)
        self._section(right, "快捷操作", "常用动作不必离开当前页面。").grid(row=0, column=0, sticky="ew")
        self._button(right, "打开数据目录", self._open_data_dir, kind="secondary", icon_name="folder").grid(
            row=1, column=0, sticky="ew", pady=(18, 8)
        )
        self._button(right, "加载历史 CSV", self._load_history, kind="secondary", icon_name="file").grid(
            row=2, column=0, sticky="ew", pady=8
        )
        self.overview_export_button = self._button(
            right, "导出报告", self._export_figure, kind="secondary", icon_name="play"
        )
        self.overview_export_button.configure(state="disabled")
        self.overview_export_button.grid(row=3, column=0, sticky="ew", pady=8)
        self._label(
            right,
            "提示：加载 CSV 会在后台解析并自动生成交互 HTML 报告。",
            size=11,
            color=TEXT_SECONDARY,
            wraplength=300,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(16, 0))
        return page

    def _meta_cell(self, parent, row: int, column: int, label: str, value: str) -> ctk.CTkLabel:
        box = ctk.CTkFrame(parent, fg_color=SURFACE_ALT, corner_radius=11)
        box.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 8 if column == 0 else 0), pady=(0, 8))
        box.grid_columnconfigure(1, weight=1)
        self._label(box, label, size=11, color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w", padx=(14, 8), pady=11)
        value_label = self._label(box, value, size=12, weight="bold", anchor="e")
        value_label.grid(row=0, column=1, sticky="e", padx=(0, 14), pady=11)
        return value_label

    def _stat_card(self, parent, column: int, label: str, value: str, note: str, icon_name: str) -> ctk.CTkLabel:
        card = self._panel(parent)
        card.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=(0 if column == 0 else 8, 0 if column == 2 else 8),
        )
        inner = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=20, pady=18)
        top = ctk.CTkFrame(inner, fg_color=SURFACE, corner_radius=0)
        top.pack(fill="x")
        self._label(top, label, size=11, color=TEXT_SECONDARY).pack(side="left")
        chip = ctk.CTkFrame(top, fg_color=ACCENT_SOFT, corner_radius=9, width=30, height=30)
        chip.pack(side="right")
        chip.pack_propagate(False)
        ctk.CTkLabel(chip, text="", image=self._icon(icon_name, ACCENT_INK, 16)).place(relx=0.5, rely=0.5, anchor="center")
        value_label = self._label(inner, value, size=30, weight="bold")
        value_label.pack(fill="x", pady=(12, 2))
        self._label(inner, note, size=11, color=MUTED).pack(fill="x")
        return value_label

    def _build_targets_page(self) -> ctk.CTkFrame:
        page = self._page()
        page.grid_columnconfigure(0, weight=7, minsize=520)
        page.grid_columnconfigure(1, weight=4, minsize=300)
        page.grid_rowconfigure(0, weight=1)

        left = self._panel(page)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(5, weight=1)

        self._section(left, "添加监控目标", "支持同时添加不同类型的目标，留空则监控整机。").grid(
            row=0, column=0, sticky="ew", padx=24, pady=(22, 0)
        )

        self._segmented = ctk.CTkFrame(left, fg_color=SURFACE_ALT, corner_radius=12)
        self._segmented.grid(row=1, column=0, sticky="ew", padx=24, pady=(18, 0))
        for column in range(3):
            self._segmented.grid_columnconfigure(column, weight=1, uniform="seg")
        for column, (kind, text) in enumerate(self.target_labels.items()):
            button = ctk.CTkButton(
                self._segmented,
                text=text,
                height=36,
                corner_radius=9,
                fg_color=SURFACE_ALT,
                hover_color=SURFACE_HOVER,
                text_color=TEXT_SECONDARY,
                font=self._font(12),
                command=lambda value=kind: self._select_target_kind(value, focus=False),
            )
            button.grid(row=0, column=column, sticky="ew", padx=4, pady=4)
            self._seg_buttons[kind] = button

        input_row = ctk.CTkFrame(left, fg_color=SURFACE, corner_radius=0)
        input_row.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 0))
        input_row.grid_columnconfigure(0, weight=1)
        self.target_entry = self._entry(input_row, self.target_value_var, placeholder=target_placeholder("exe"))
        self.target_entry.grid(row=0, column=0, sticky="ew")
        self.browse_target_button = self._button(
            input_row, "浏览", self._browse_target, kind="secondary", icon_name="folder", width=92
        )
        self.browse_target_button.grid(row=0, column=1, padx=(8, 0))
        self._button(
            input_row,
            "添加",
            self._add_target,
            kind="primary",
            icon_name="plus",
            width=96,
            bold=True,
        ).grid(row=0, column=2, padx=(8, 0))

        self.target_hint_label = self._label(
            left,
            target_hint("exe"),
            size=11,
            color=TEXT_SECONDARY,
            wraplength=560,
        )
        self.target_hint_label.grid(row=3, column=0, sticky="w", padx=24, pady=(9, 0))

        list_head = ctk.CTkFrame(left, fg_color=SURFACE, corner_radius=0)
        list_head.grid(row=4, column=0, sticky="ew", padx=24, pady=(20, 8))
        list_head.grid_columnconfigure(0, weight=1)
        self._label(list_head, "已添加目标", size=13, weight="bold").grid(row=0, column=0, sticky="w")
        self.targets_count = self._label(list_head, "", size=11, color=MUTED)
        self.targets_count.grid(row=0, column=1, sticky="e")

        self.targets_scroll = ctk.CTkScrollableFrame(
            left,
            fg_color=SURFACE,
            corner_radius=0,
            scrollbar_fg_color=SURFACE,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=SURFACE_HOVER,
        )
        self.targets_scroll.grid(row=5, column=0, sticky="nsew", padx=(18, 10), pady=(0, 20))
        self.targets_scroll.grid_columnconfigure(0, weight=1)
        self._render_targets()

        right = ctk.CTkFrame(page, fg_color=BG, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        live = self._panel(right)
        live.grid(row=0, column=0, sticky="ew")
        live_inner = ctk.CTkFrame(live, fg_color=SURFACE, corner_radius=0)
        live_inner.pack(fill="both", padx=22, pady=20)
        self._section(live_inner, "实时概览", "监控运行后实时更新").pack(fill="x")
        self.cpu_value_label, self.cpu_progress = self._meter(live_inner, "整机 CPU", "0.0%", ACCENT)
        self.memory_value_label, self.memory_progress = self._meter(live_inner, "目标内存", "0 MB", BLUE)

        current = self._panel(right)
        current.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        current_inner = ctk.CTkFrame(current, fg_color=SURFACE, corner_radius=0)
        current_inner.pack(fill="both", expand=True, padx=22, pady=20)
        self._section(current_inner, "当前配置", "").pack(fill="x")
        self.summary_interval = self._summary_row(current_inner, "采样间隔", f"{self.app_settings.interval_sec:g} 秒")
        self.summary_children = self._summary_row(current_inner, "包含子进程", "开启" if self.children_var.get() else "关闭")
        self.summary_data = self._summary_row(current_inner, "CSV 路径", _truncate(str(self.app_settings.data_dir), 30))
        self.summary_backup = self._summary_row(current_inner, "自动备份", self._backup_summary())
        self.summary_targets = self._summary_row(
            current_inner, "监控目标", self._targets_summary()
        )
        self._select_target_kind("exe", focus=False)
        return page

    def _meter(self, parent, label: str, value: str, color: str):
        row = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0)
        row.pack(fill="x", pady=(18, 0))
        row.grid_columnconfigure(0, weight=1)
        self._label(row, label, size=11, color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w")
        value_label = self._label(row, value, size=12, weight="bold")
        value_label.grid(row=0, column=1, sticky="e")
        progress = ctk.CTkProgressBar(
            row,
            height=8,
            corner_radius=4,
            progress_color=color,
            fg_color=SURFACE_ALT,
            border_width=0,
        )
        progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        progress.set(0)
        return value_label, progress

    def _summary_row(self, parent, label: str, value: str) -> ctk.CTkLabel:
        row = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0)
        row.pack(fill="x", pady=(14, 0))
        self._label(row, label, size=11, color=TEXT_SECONDARY).pack(side="left")
        value_label = self._label(row, value, size=12, weight="bold", anchor="e")
        value_label.pack(side="right")
        return value_label

    def _build_storage_page(self) -> ctk.CTkFrame:
        page = self._page()
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        panel = self._panel(page)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            panel,
            fg_color=SURFACE,
            corner_radius=0,
            scrollbar_fg_color=SURFACE,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=SURFACE_HOVER,
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=(10, 2), pady=(6, 6))
        scroll.grid_columnconfigure(0, weight=1)
        self.storage_scroll = scroll

        inner = ctk.CTkFrame(scroll, fg_color=SURFACE, corner_radius=0)
        inner.grid(row=0, column=0, sticky="ew", padx=16, pady=18)
        inner.grid_columnconfigure((0, 1, 2), weight=1, uniform="field")
        self._section(inner, "采样设置", "控制采集频率、CSV 输出位置与数据安全策略。").grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 16)
        )
        self._field_card(inner, "采样间隔（秒）", "建议 1 秒；更低间隔会增加磁盘写入。", self.interval_var, entry_height=36).grid(
            row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12)
        )
        children = ctk.CTkFrame(inner, fg_color=SURFACE_ALT, corner_radius=14)
        children.grid(row=1, column=1, sticky="nsew", padx=8, pady=(0, 12))
        children.grid_columnconfigure(0, weight=1)
        self._label(children, "包含子进程", size=13, weight="bold").grid(row=0, column=0, sticky="w", padx=16, pady=(14, 3))
        self._label(
            children,
            "跟踪目标启动的全部子进程。",
            size=11,
            color=TEXT_SECONDARY,
            wraplength=300,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=16)
        self._switch(children, "开启后记录进程树", self.children_var).grid(
            row=2, column=0, sticky="w", padx=16, pady=(12, 15)
        )
        self._field_card(inner, "磁盘保留阈值（MB）", "低于此空间时停止写入。", self.disk_limit_var, entry_height=36).grid(
            row=1, column=2, sticky="nsew", padx=(8, 0), pady=(0, 12)
        )
        self._field_card(inner, "每 N 行强制落盘", "数值越小，异常断电丢的数据越少。", self.fsync_var, entry_height=36).grid(
            row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12)
        )
        self._field_card(inner, "每 N 行生成备份", "输入 0 关闭自动备份。", self.backup_rows_var, entry_height=36).grid(
            row=2, column=1, sticky="nsew", padx=8, pady=(0, 12)
        )
        self._field_card(inner, "保留备份数量", "自动删除更早的备份文件。", self.backup_keep_var, entry_height=36).grid(
            row=2, column=2, sticky="nsew", padx=(8, 0), pady=(0, 12)
        )

        path_box = ctk.CTkFrame(inner, fg_color=SURFACE_ALT, corner_radius=14)
        path_box.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        path_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(path_box, text="", image=self._icon("folder", ACCENT_INK, 18), width=18).grid(
            row=0, column=0, padx=(16, 10), pady=(12, 0)
        )
        self._label(path_box, "CSV 保存位置", size=13, weight="bold").grid(row=0, column=1, sticky="w", pady=(11, 0))
        self._label(
            path_box,
            "每次启动生成新的会话 CSV，备份写入 data/backups。",
            size=11,
            color=TEXT_SECONDARY,
        ).grid(row=0, column=2, columnspan=2, sticky="e", padx=(8, 16), pady=(11, 0))
        self._entry(path_box, self.data_dir_var).grid(row=1, column=0, columnspan=2, sticky="ew", padx=(16, 8), pady=(8, 13))
        self._button(path_box, "选择", self._browse_data_dir, kind="secondary", width=84).grid(
            row=1, column=2, padx=(0, 8), pady=(8, 13)
        )
        self._button(path_box, "打开目录", self._open_data_dir, kind="secondary", width=100).grid(
            row=1, column=3, padx=(0, 16), pady=(8, 13)
        )

        self._field_card(
            inner,
            "状态输出间隔（秒）",
            "控制 monitor.log 中自身状态的记录频率。",
            self.self_status_var,
            entry_height=36,
        ).grid(row=4, column=0, sticky="nsew", padx=(0, 8))
        tip = ctk.CTkFrame(inner, fg_color=ACCENT_SOFT, corner_radius=14)
        tip.grid(row=4, column=1, columnspan=2, sticky="nsew", padx=(8, 0))
        tip.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tip, text="", image=self._icon("check", ACCENT_INK, 18), width=18).grid(
            row=0, column=0, sticky="n", padx=(18, 10), pady=(17, 0)
        )
        self._label(tip, "可靠性优先", size=13, weight="bold", color=ACCENT_INK).grid(
            row=0, column=1, sticky="w", padx=(0, 18), pady=(15, 2)
        )
        self._label(
            tip,
            "每个样本写入后立即 flush，默认逐行 fsync；异常退出也会尽量保留已采数据。",
            size=11,
            color=TEXT_SECONDARY,
            wraplength=620,
            justify="left",
        ).grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(0, 15))
        self._schedule_scrollbar_sync(scroll)
        return page

    def _build_conditions_page(self) -> ctk.CTkFrame:
        page = self._page()
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        panel = self._panel(page)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(7, weight=1)

        head = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 0))
        head.grid_columnconfigure(0, weight=1)
        self._section(head, "自动结束条件", "规则持续满足指定时间后，可发送通知并停止采集。").grid(
            row=0, column=0, sticky="ew"
        )
        self._switch(head, "启用结束条件", self.condition_enabled_var).grid(row=0, column=1, sticky="e")

        divider = ctk.CTkFrame(panel, fg_color=BORDER, height=1, corner_radius=0)
        divider.grid(row=1, column=0, sticky="ew", padx=24, pady=(18, 0))

        sub = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        sub.grid(row=2, column=0, sticky="ew", padx=24, pady=(18, 8))
        self._label(sub, "触发规则", size=13, weight="bold").grid(row=0, column=0, sticky="w")

        fields = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        fields.grid(row=3, column=0, sticky="ew", padx=24)
        for column in range(3):
            fields.grid_columnconfigure(column, weight=1, uniform="cond")
        self._compact_field(fields, 0, 0, "规则关系", lambda box: self._option(box, self.combine_var, list(COMBINE_LABELS.values()), height=36))
        self._compact_field(
            fields,
            0,
            1,
            "触发动作",
            lambda box: self._option(box, self.action_var, list(ACTION_LABELS.values()), height=36),
        )
        self._compact_field(fields, 0, 2, "持续时间（秒）", lambda box: self._entry(box, self.sustained_var, height=36))
        self._compact_field(fields, 1, 0, "冷却时间（秒）", lambda box: self._entry(box, self.cooldown_var, height=36))
        self._compact_field(fields, 1, 1, "退出宽限（秒）", lambda box: self._entry(box, self.targets_grace_var, height=36))
        gone = ctk.CTkFrame(fields, fg_color=SURFACE_ALT, corner_radius=12)
        gone.grid(row=1, column=2, sticky="nsew", padx=8, pady=(0, 8))
        self._label(gone, "目标退出后结束", size=11, color=TEXT_SECONDARY).grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 0)
        )
        self._switch(gone, "启用", self.targets_gone_var).grid(
            row=1, column=0, sticky="w", padx=14, pady=(6, 10)
        )

        divider2 = ctk.CTkFrame(panel, fg_color=BORDER, height=1, corner_radius=0)
        divider2.grid(row=4, column=0, sticky="ew", padx=24, pady=(14, 0))

        rule_head = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        rule_head.grid(row=5, column=0, sticky="ew", padx=24, pady=(18, 0))
        rule_head.grid_columnconfigure(0, weight=1)
        self._label(rule_head, "阈值规则", size=13, weight="bold").grid(row=0, column=0, sticky="w")
        self._label(rule_head, "多个规则按上面的“规则关系”组合判断。", size=11, color=TEXT_SECONDARY).grid(
            row=1, column=0, sticky="w", pady=(3, 0)
        )

        builder = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        builder.grid(row=6, column=0, sticky="ew", padx=24, pady=(12, 10))
        builder.grid_columnconfigure(1, weight=1)
        self._switch(builder, "启用", self.rule_enabled_var).grid(row=0, column=0, padx=(0, 12))
        self._option(builder, self.rule_metric_var, list(METRIC_LABELS.values()), height=38).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        self._option(builder, self.rule_operator_var, [">=", ">", "<=", "<"], width=72, height=38).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self._entry(builder, self.rule_value_var, height=38, width=96).grid(row=0, column=3, padx=(0, 8))
        self._button(builder, "添加规则", self._add_rule, kind="primary", icon_name="plus", width=112).grid(
            row=0, column=4
        )

        self.rules_scroll = ctk.CTkScrollableFrame(
            panel,
            fg_color=SURFACE,
            corner_radius=0,
            scrollbar_fg_color=SURFACE,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=SURFACE_HOVER,
        )
        self.rules_scroll.grid(row=7, column=0, sticky="nsew", padx=(18, 10), pady=(0, 20))
        self.rules_scroll.grid_columnconfigure(0, weight=1)
        self._render_rules()
        return page

    def _compact_field(self, parent, row: int, column: int, label: str, factory) -> None:
        box = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0)
        box.grid(row=row, column=column, sticky="ew", padx=8, pady=(0, 8))
        box.grid_columnconfigure(0, weight=1)
        self._label(box, label, size=11, color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w", pady=(0, 5))
        widget = factory(box)
        widget.grid(row=1, column=0, sticky="ew")

    def _build_notifications_page(self) -> ctk.CTkFrame:
        page = self._page()
        page.grid_columnconfigure(0, weight=1)
        panel = self._panel(page)
        panel.grid(row=0, column=0, sticky="new")
        panel.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=0)
        inner.grid(row=0, column=0, sticky="ew", padx=26, pady=24)
        inner.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(inner, fg_color=SURFACE, corner_radius=0)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self._section(head, "钉钉机器人", "触发结束条件时发送运行汇总与进程明细。").grid(
            row=0, column=0, sticky="ew"
        )
        self._switch(head, "启用通知", self.ding_enabled_var).grid(row=0, column=1, sticky="e")

        form = ctk.CTkFrame(inner, fg_color=SURFACE, corner_radius=0)
        form.grid(row=1, column=0, sticky="ew", pady=(20, 0))
        form.grid_columnconfigure(0, weight=1)
        self._form_field(form, 0, "Webhook 地址", self.webhook_var, placeholder="https://oapi.dingtalk.com/robot/send?access_token=...")
        self._form_field(form, 1, "加签 Secret", self.secret_var, show="•", placeholder="SEC...")
        self._form_field(form, 2, "请求超时（秒）", self.timeout_var, width=180)

        warning = ctk.CTkFrame(inner, fg_color=WARN_BG, corner_radius=14)
        warning.grid(row=2, column=0, sticky="ew", pady=(20, 0))
        warning.grid_columnconfigure(1, weight=1)
        self._label(warning, "密钥仅保存在本机 config.json，请勿把真实密钥提交到公开仓库。", size=11, color=WARN_TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=13
        )
        return page

    def _form_field(self, parent, row: int, label: str, variable, *, show: str = "", placeholder: str = "", width: int | None = None) -> None:
        box = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        box.grid_columnconfigure(0, weight=1)
        self._label(box, label, size=12, color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w", pady=(0, 6))
        entry = self._entry(box, variable, show=show, placeholder=placeholder, width=width)
        if width is not None:
            entry.grid(row=1, column=0, sticky="w")
        else:
            entry.grid(row=1, column=0, sticky="ew")

    # -------------------------------------------------------------- navigation
    def _show_page(self, key: str) -> None:
        titles = {
            "overview": ("运行概览", "查看当前监控状态与采集统计。"),
            "targets": ("监控目标", "选择需要持续跟踪的应用、进程或 PID。"),
            "storage": ("采集与存储", "设置采样频率、CSV 目录和备份策略。"),
            "conditions": ("结束条件", "设置持续阈值、目标退出和触发动作。"),
            "notifications": ("通知设置", "配置钉钉机器人通知。"),
        }
        self._active_page = key
        for page_key, frame in self._pages.items():
            if page_key == key:
                frame.grid()
                frame.tkraise()
            else:
                frame.grid_remove()
        for page_key, item in self._nav_items.items():
            active = page_key == key
            item["frame"].configure(fg_color=NAV_ACTIVE_BG if active else SIDEBAR_BG)
            item["icon"].configure(image=item["active"] if active else item["idle"])
            item["text"].configure(
                text_color=WHITE if active else SIDEBAR_TEXT,
                font=self._font(13, "bold" if active else "normal"),
            )
        title, subtitle = titles[key]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        self._on_page_host_resize()

    # ---------------------------------------------------------------- targets
    def _select_target_kind(self, kind: str, *, focus: bool = True) -> None:
        self._seg_value = kind
        for key, button in self._seg_buttons.items():
            selected = key == kind
            button.configure(
                fg_color=SURFACE if selected else SURFACE_ALT,
                hover_color=SURFACE if selected else SURFACE_HOVER,
                text_color=TEXT if selected else TEXT_SECONDARY,
                font=self._font(12, "bold" if selected else "normal"),
            )
        if not hasattr(self, "target_entry"):
            return
        self.target_entry.configure(placeholder_text=target_placeholder(kind))
        self.target_hint_label.configure(text=target_hint(kind))
        self.browse_target_button.configure(state="normal" if kind == "exe" else "disabled")
        self.target_value_var.set("")
        if focus:
            self.target_entry.focus_set()

    def _selected_target_kind(self) -> str:
        return self._seg_value or "exe"

    def _browse_target(self) -> None:
        if sys.platform == "darwin":
            selected = choose_macos_target()
            if not selected:
                selected = filedialog.askopenfilename(
                    title="选择要监控的 macOS 应用程序 (.app)",
                    initialdir="/Applications",
                    filetypes=[("macOS 应用程序", "*.app"), ("所有文件", "*")],
                )
        else:
            selected = filedialog.askopenfilename(
                title="选择要监控的 Windows 程序 (.exe)",
                filetypes=[("Windows 可执行文件", "*.exe"), ("所有文件", "*.*")],
            )
        if selected:
            self.target_value_var.set(self._clean_target_path(selected))

    @staticmethod
    def _clean_target_path(value: str) -> str:
        cleaned = value.strip().strip('"').strip("'")
        if len(cleaned) > 1 and cleaned.endswith(("/", "\\")):
            cleaned = cleaned.rstrip("/\\")
        return cleaned

    def _target_kind_changed(self, label: str) -> None:
        kind = next((key for key, text in self.target_labels.items() if text == label), "exe")
        self._select_target_kind(kind)

    def _add_target(self) -> None:
        kind = self._selected_target_kind()
        value = self._clean_target_path(self.target_value_var.get())
        if not value:
            messagebox.showwarning("缺少目标", "请先输入或选择监控目标。")
            return
        if kind == "pid":
            try:
                pid = int(value)
                if pid <= 0:
                    raise ValueError
                value = str(pid)
            except ValueError:
                messagebox.showerror("PID 无效", "PID 必须是大于 0 的整数。")
                return
        elif kind == "exe":
            value = str(Path(value).expanduser())
        if kind == "exe" and sys.platform == "darwin":
            path = Path(value).expanduser()
            if path.suffix.lower() == ".app" and path.is_dir() and resolve_app_executable(path) is None:
                messagebox.showwarning(
                    "无法识别应用包",
                    "这个 .app 中没有找到 Contents/MacOS 可执行文件。\n"
                    "请确认选择的是完整的应用程序，或改用“进程名”方式监控。",
                )
                return
        if (kind, value) in self.target_records:
            messagebox.showinfo("目标已存在", "该监控目标已经在列表中。")
            return
        self.target_records.append((kind, value))
        self.target_value_var.set("")
        self._render_targets()

    def _remove_target(self, index: int) -> None:
        if 0 <= index < len(self.target_records):
            self.target_records.pop(index)
            self._render_targets()

    def _render_targets(self) -> None:
        if not hasattr(self, "targets_scroll"):
            return
        for child in self.targets_scroll.winfo_children():
            child.destroy()
        count = len(self.target_records)
        self.targets_count.configure(text=f"{count} 个" if count else "空")
        if hasattr(self, "sidebar_count"):
            self.sidebar_count.configure(text=f"已监控 {count} 个目标" if count else "尚未添加监控目标")
        if hasattr(self, "summary_targets"):
            self.summary_targets.configure(text=self._targets_summary())
        if not self.target_records:
            empty = ctk.CTkFrame(self.targets_scroll, fg_color=SURFACE_ALT, corner_radius=14)
            empty.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))
            empty.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(empty, text="", image=self._icon("target", MUTED, 28)).grid(
                row=0, column=0, pady=(26, 8)
            )
            self._label(empty, "尚未添加目标", size=13, weight="bold").grid(row=1, column=0, pady=(0, 4))
            self._label(
                empty,
                "留空时监控整机 CPU 与已用内存。",
                size=11,
                color=TEXT_SECONDARY,
            ).grid(row=2, column=0, pady=(0, 26))
            self._schedule_scrollbar_sync(self.targets_scroll)
            return
        for index, (kind, value) in enumerate(self.target_records):
            row = ctk.CTkFrame(self.targets_scroll, fg_color=SURFACE_ALT, corner_radius=13)
            row.grid(row=index, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))
            row.grid_columnconfigure(1, weight=1)
            tile = ctk.CTkFrame(row, fg_color=SURFACE, corner_radius=10, width=38, height=38)
            tile.grid(row=0, column=0, padx=(12, 12), pady=11)
            tile.grid_propagate(False)
            ctk.CTkLabel(tile, text="", image=self._icon(TARGET_ICONS[kind], ACCENT_INK, 18)).place(
                relx=0.5, rely=0.5, anchor="center"
            )
            copy = ctk.CTkFrame(row, fg_color=SURFACE_ALT, corner_radius=0)
            copy.grid(row=0, column=1, sticky="ew")
            title, subtitle = describe_target(kind, value)
            self._label(copy, title, size=12, weight="bold").pack(fill="x")
            self._label(copy, _truncate(subtitle, 64), size=11, color=TEXT_SECONDARY).pack(fill="x", pady=(2, 0))
            trash = ctk.CTkButton(
                row,
                text="",
                image=self._icon("trash", MUTED, 17),
                width=34,
                height=34,
                corner_radius=9,
                fg_color=SURFACE_ALT,
                hover_color=DANGER_SOFT,
                command=lambda item=index: self._remove_target(item),
            )
            trash.grid(row=0, column=2, padx=(8, 10))
        self._schedule_scrollbar_sync(self.targets_scroll)

    def _load_targets(self) -> None:
        self.target_records = []
        self.target_records.extend(("exe", path) for path in self.app_settings.exe_paths)
        self.target_records.extend(("pid", str(pid)) for pid in self.app_settings.pids)
        self.target_records.extend(("name", name) for name in self.app_settings.processes)

    def _browse_data_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 CSV 保存目录",
            initialdir=self.data_dir_var.get() or str(Path.home()),
        )
        if selected:
            self.data_dir_var.set(selected)

    def _open_data_dir(self) -> None:
        path = Path(self.data_dir_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc))

    # ------------------------------------------------------------------ rules
    def _load_rules(self) -> None:
        self.rule_records = [
            ConditionRule(rule.metric, rule.operator, rule.value, rule.enabled)
            for rule in self.app_settings.end_condition.rules
        ]

    def _add_rule(self) -> None:
        try:
            value = float(self.rule_value_var.get())
        except ValueError:
            messagebox.showerror("阈值无效", "规则阈值必须是数字。")
            return
        self.rule_records.append(
            ConditionRule(
                metric=METRIC_KEYS.get(self.rule_metric_var.get(), self.rule_metric_var.get()),
                operator=self.rule_operator_var.get(),
                value=value,
                enabled=bool(self.rule_enabled_var.get()),
            )
        )
        self._render_rules()

    def _remove_rule(self, index: int) -> None:
        if 0 <= index < len(self.rule_records):
            self.rule_records.pop(index)
            self._render_rules()

    def _render_rules(self) -> None:
        if not hasattr(self, "rules_scroll"):
            return
        for child in self.rules_scroll.winfo_children():
            child.destroy()
        if not self.rule_records:
            empty = ctk.CTkFrame(self.rules_scroll, fg_color=SURFACE_ALT, corner_radius=14)
            empty.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))
            self._label(empty, "尚未添加指标规则", size=12, color=TEXT_SECONDARY, anchor="center").pack(pady=24)
            self._schedule_scrollbar_sync(self.rules_scroll)
            return
        for index, rule in enumerate(self.rule_records):
            row = ctk.CTkFrame(self.rules_scroll, fg_color=SURFACE_ALT, corner_radius=13)
            row.grid(row=index, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))
            row.grid_columnconfigure(1, weight=1)
            icon_name = "check" if rule.enabled else "dot"
            color = ACCENT_INK if rule.enabled else MUTED
            ctk.CTkLabel(row, text="", image=self._icon(icon_name, color, 16), width=16).grid(
                row=0, column=0, padx=(14, 10), pady=16
            )
            metric = METRIC_LABELS.get(rule.metric, rule.metric)
            self._label(row, metric, size=12, weight="bold").grid(row=0, column=1, sticky="w")
            self._label(row, f"{rule.operator} {rule.value:g}", size=12, color=TEXT).grid(
                row=0, column=2, padx=12
            )
            ctk.CTkButton(
                row,
                text="",
                image=self._icon("trash", MUTED, 16),
                width=32,
                height=32,
                corner_radius=9,
                fg_color=SURFACE_ALT,
                hover_color=DANGER_SOFT,
                command=lambda item=index: self._remove_rule(item),
            ).grid(row=0, column=3, padx=(4, 12))
        self._schedule_scrollbar_sync(self.rules_scroll)

    # ----------------------------------------------------------------- config
    @staticmethod
    def _number(value: str, label: str, *, integer: bool = False, minimum: float = 0) -> float | int:
        try:
            number = int(value) if integer else float(value)
        except ValueError as exc:
            raise ValueError(f"{label}必须是{'整数' if integer else '数字'}。") from exc
        if number < minimum:
            raise ValueError(f"{label}不能小于 {minimum:g}。")
        return number

    def _save_config(self, show_confirmation: bool = True) -> bool:
        try:
            processes = [value for kind, value in self.target_records if kind == "name"]
            exe_paths = [value for kind, value in self.target_records if kind == "exe"]
            pids = [int(value) for kind, value in self.target_records if kind == "pid"]
            settings = self.app_settings
            settings.mode = "system" if not self.target_records else "tree"
            settings.processes = tuple(dict.fromkeys(processes))
            settings.apps = ()
            settings.exe_paths = tuple(dict.fromkeys(exe_paths))
            settings.pids = tuple(dict.fromkeys(pids))
            settings.include_children = bool(self.children_var.get())
            settings.interval_sec = float(self._number(self.interval_var.get(), "采样间隔", minimum=0.01))
            data_dir_text = self.data_dir_var.get().strip()
            if not data_dir_text:
                raise ValueError("CSV 保存目录不能为空。")
            settings.data_dir = Path(data_dir_text).expanduser()
            settings.disk_limit_mb = int(self._number(self.disk_limit_var.get(), "磁盘保留阈值", integer=True, minimum=0))
            settings.fsync_every_rows = int(self._number(self.fsync_var.get(), "强制落盘行数", integer=True, minimum=1))
            settings.backup_every_rows = int(self._number(self.backup_rows_var.get(), "备份行数", integer=True, minimum=0))
            settings.backup_keep = int(self._number(self.backup_keep_var.get(), "备份保留数量", integer=True, minimum=1))
            settings.self_status_interval_sec = float(self._number(self.self_status_var.get(), "状态输出间隔", minimum=1))

            condition = settings.end_condition
            condition.enabled = bool(self.condition_enabled_var.get())
            condition.combine = COMBINE_VALUES.get(self.combine_var.get(), self.combine_var.get())
            condition.sustained_sec = float(self._number(self.sustained_var.get(), "持续时间", minimum=0))
            condition.cooldown_sec = float(self._number(self.cooldown_var.get(), "冷却时间", minimum=0))
            condition.action = ACTION_VALUES.get(self.action_var.get(), self.action_var.get())
            condition.targets_gone_enabled = bool(self.targets_gone_var.get())
            condition.targets_gone_grace_sec = float(self._number(self.targets_grace_var.get(), "目标退出宽限", minimum=0))
            condition.rules = list(self.rule_records)

            ding = settings.dingtalk
            ding.enabled = bool(self.ding_enabled_var.get())
            ding.webhook = self.webhook_var.get().strip()
            ding.secret = self.secret_var.get().strip()
            ding.timeout_sec = float(self._number(self.timeout_var.get(), "通知超时", minimum=0.1))

            saved = save_settings(settings)
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.data_dir = settings.data_dir
            self.disk_limit_mb = settings.disk_limit_mb
            self.fsync_every_rows = settings.fsync_every_rows
            self.backup_every_rows = settings.backup_every_rows
            self.backup_keep = settings.backup_keep
            self._end_condition = condition
            self._dingtalk = ding
            self._set_status(f"配置已保存：{saved}  ·  数据目录：{self.data_dir}")
            self.config_sync_label.configure(text="config.json · 刚刚保存")
            self._refresh_summary()
            if show_confirmation:
                messagebox.showinfo("保存成功", f"配置已写入：\n{saved}")
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置无法保存", str(exc))
            return False

    def _refresh_summary(self) -> None:
        if not hasattr(self, "summary_interval"):
            return
        self.summary_interval.configure(text=f"{self.app_settings.interval_sec:g} 秒")
        self.summary_children.configure(text="开启" if self.children_var.get() else "关闭")
        self.summary_data.configure(text=_truncate(str(self.app_settings.data_dir), 30))
        self.summary_backup.configure(text=self._backup_summary())
        if hasattr(self, "summary_targets"):
            self.summary_targets.configure(text=self._targets_summary())

    def _backup_summary(self) -> str:
        rows = self.app_settings.backup_every_rows
        return "关闭" if rows <= 0 else f"每 {rows:,} 行"

    def _targets_summary(self) -> str:
        count = len(self.target_records)
        return "整机监控" if count == 0 else f"{count} 个目标"

    def _set_status(self, text: str) -> None:
        self.status_var.set(_truncate(text, 110))

    def _targets(self) -> tuple[list[str], list[str], list[int]]:
        processes = [value for kind, value in self.target_records if kind == "name"]
        exe_paths = [value for kind, value in self.target_records if kind == "exe"]
        pids = [int(value) for kind, value in self.target_records if kind == "pid"]
        return processes, exe_paths, pids

    # ------------------------------------------------------------- monitoring
    def _start_monitor(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        if not self._save_config(show_confirmation=False):
            return
        processes, exe_paths, pids = self._targets()
        target_text = ", ".join(processes + [f"APP:{path}" for path in exe_paths] + [f"PID:{pid}" for pid in pids]) or "整机资源"
        self._set_status(f"监控目标：{target_text}  ·  数据目录：{self.data_dir}")
        self._stop_event = threading.Event()
        self._sample_count = 0
        self.count_var.set("0 个样本")
        self.overview_samples.configure(text="0")
        self.overview_status.configure(text="正在启动采集…")
        self._worker_thread = threading.Thread(
            target=self._monitor_worker,
            args=(processes, exe_paths, pids, self.app_settings.interval_sec, bool(self.children_var.get())),
            name="watchdog-collector",
            daemon=True,
        )
        self.start_button.configure(state="disabled")
        self._set_stop_state(True)
        self.live_var.set("正在启动")
        self._worker_thread.start()

    def _monitor_worker(self, processes: list[str], exe_paths: list[str], pids: list[int], interval: float, include_children: bool) -> None:
        config = MonitorConfig(
            process_patterns=tuple(processes),
            exe_paths=tuple(exe_paths),
            pids=tuple(pids),
            interval_sec=interval,
            data_dir=self.data_dir,
            disk_limit_mb=self.disk_limit_mb,
            fsync_every_rows=self.fsync_every_rows,
            self_status_interval_sec=self.app_settings.self_status_interval_sec,
            include_children=include_children,
        )
        try:
            with CsvStore(
                data_dir=self.data_dir,
                disk_limit_mb=self.disk_limit_mb,
                fsync_every_rows=self.fsync_every_rows,
                backup_every_rows=self.backup_every_rows,
                backup_keep=self.backup_keep,
            ) as store:
                store.ensure_day(date.today())
                self._event_queue.put(("started", str(store.current_path or "")))
                monitor = PerfMonitor(config, store)
                condition_monitor = EndConditionMonitor(self._end_condition, has_process_targets=config.has_process_targets) if self._end_condition.enabled else None
                notifier = DingTalkNotifier(self._dingtalk)
                target = ", ".join(processes + [f"APP:{path}" for path in exe_paths] + [f"PID:{pid}" for pid in pids]) or "整机资源"

                def on_sample(now, rows):
                    self._event_queue.put(("sample", now, rows))
                    if condition_monitor is None:
                        return
                    alert = condition_monitor.record(now, rows)
                    if alert is None:
                        return
                    sent = notifier.send_alert(alert, target, store.current_path)
                    self._event_queue.put(("alert", alert.condition_text, sent))
                    if alert.stop_requested:
                        self._stop_event.set()

                monitor.run_until_stopped(self._stop_event, on_sample=on_sample)
            self._event_queue.put(("stopped", "监控已停止"))
        except Exception as exc:
            self._event_queue.put(("error", f"{type(exc).__name__}: {exc}"))

    def _stop_monitor(self) -> None:
        self._stop_event.set()
        self.live_var.set("正在停止")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                kind = event[0]
                if kind == "started":
                    self.live_var.set("监控运行中")
                    self.overview_status.configure(text="正在写入采样数据")
                    self.overview_file.configure(text=_truncate(event[1], 42))
                    self._set_status(f"正在写入：{event[1]}")
                elif kind == "sample":
                    _, now, rows = event
                    self._sample_count += len(rows)
                    self.count_var.set(f"{self._sample_count} 个样本")
                    self.overview_samples.configure(text=f"{self._sample_count:,}")
                    ok_rows = [row for row in rows if len(row) >= 6 and row[4] == STATUS_OK]
                    system_rows = [row for row in ok_rows if str(row[0]).upper() == "SYSTEM" and str(row[1]) == "-1"]
                    target_rows = [row for row in ok_rows if row not in system_rows]
                    system_cpu = [float(row[2]) for row in system_rows if str(row[2]).strip() and str(row[2]) != "None"]
                    target_cpu = [float(row[2]) for row in target_rows if str(row[2]).strip() and str(row[2]) != "None"]
                    memory_values = [float(row[3]) for row in (target_rows or system_rows) if str(row[3]).strip() and str(row[3]) != "None"]
                    total_cpu = max(system_cpu) if system_cpu else (machine_cpu_percent(sum(target_cpu)) if target_cpu else None)
                    memory = max(memory_values) if memory_values else 0.0
                    cpu = total_cpu or 0.0
                    self.cpu_value_label.configure(text=f"{cpu:.1f}%")
                    self.cpu_progress.set(max(0.0, min(1.0, cpu / 100.0)))
                    self.memory_value_label.configure(text=f"{memory:.1f} MB")
                    self.memory_progress.set(max(0.0, min(1.0, memory / 4096.0)))
                    self.overview_cpu.configure(text=f"{cpu:.1f}%")
                    self.overview_memory.configure(text=f"{memory:.1f} MB")
                    self.overview_status.configure(text=f"最新采样 {now:%H:%M:%S} · 本次写入 {len(rows)} 条记录")
                    self.live_var.set(f"CPU {cpu:.1f}% · {memory:.0f} MB")
                elif kind == "alert":
                    self.live_var.set("结束条件已触发")
                    self._set_status(f"触发条件：{event[1]}  ·  钉钉通知：{'已发送' if event[2] else '未启用或发送失败'}")
                elif kind == "stopped":
                    self.live_var.set(event[1])
                    self.overview_status.configure(text="监控已安全停止")
                    self.start_button.configure(state="normal")
                    self._set_stop_state(False)
                    self._worker_thread = None
                elif kind == "error":
                    self.live_var.set("采集异常")
                    self.overview_status.configure(text="采集异常")
                    self._set_status(event[1])
                    self.start_button.configure(state="normal")
                    self._set_stop_state(False)
                    messagebox.showerror("采集异常", event[1])
                    self._worker_thread = None
                elif kind == "history_ready":
                    html_path = Path(event[1])
                    self._last_html_path = html_path
                    self._records_count = int(event[2])
                    self.export_button.configure(state="normal")
                    self.overview_export_button.configure(state="normal")
                    self.load_history_button.configure(state="normal")
                    self._set_status(f"报告已生成：{html_path}  ·  {event[3]} 个文件 / {event[2]} 条样本")
                    webbrowser.open(html_path.as_uri())
                    self._history_thread = None
                elif kind == "history_error":
                    self.load_history_button.configure(state="normal")
                    if self._last_records:
                        self.export_button.configure(state="normal")
                    messagebox.showerror("历史分析失败", event[1])
                    self._history_thread = None
                elif kind == "export_ready":
                    self.export_button.configure(state="normal")
                    self._set_status(f"报告已导出：{event[1]}")
                    self._history_thread = None
                elif kind == "export_error":
                    self.export_button.configure(state="normal")
                    messagebox.showerror("导出失败", event[1])
                    self._history_thread = None
        except queue.Empty:
            pass
        except tk.TclError:
            self._closed = True
            return
        if not self._closed:
            try:
                self._poll_job = self.root.after(120, self._poll_events)
            except tk.TclError:
                self._closed = True

    # ---------------------------------------------------------------- history
    def _load_history(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择历史 CSV 文件（可多选）",
            initialdir=str(self.data_dir),
            filetypes=[("CSV 数据文件", "*.csv"), ("全部文件", "*.*")],
        )
        if not selected:
            return
        if self._history_thread is not None and self._history_thread.is_alive():
            messagebox.showinfo("正在处理", "已有历史分析任务正在运行。")
            return
        self.load_history_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self._set_status(f"正在解析 {len(selected)} 个 CSV 并生成 HTML…")
        self._history_thread = threading.Thread(target=self._history_worker, args=(tuple(selected),), name="watchdog-history", daemon=True)
        self._history_thread.start()

    def _history_worker(self, selected: tuple[str, ...]) -> None:
        try:
            from .plotting import load_files, write_html_report
            records, files = load_files(selected)
            if not records:
                raise ValueError("选中的 CSV 中没有可用记录。")
            self._last_records = records
            report_dir = self.data_dir / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            out = report_dir / f"report_{datetime.now():%Y%m%d_%H%M%S}.html"
            write_html_report(records, out, title="WatchdogPerf 资源监控报告")
            self._event_queue.put(("history_ready", str(out), len(records), len(files)))
        except Exception as exc:
            self._event_queue.put(("history_error", f"{type(exc).__name__}: {exc}"))

    def _export_figure(self) -> None:
        if not self._last_records:
            messagebox.showinfo("没有可导出的数据", "请先加载 CSV 生成报告。")
            return
        if self._history_thread is not None and self._history_thread.is_alive():
            messagebox.showinfo("正在处理", "请等待当前任务结束。")
            return
        path = filedialog.asksaveasfilename(
            title="导出报告",
            initialdir=str(self.data_dir / "reports"),
            defaultextension=".html",
            filetypes=[("交互 HTML", "*.html"), ("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")],
        )
        if not path:
            return
        self.export_button.configure(state="disabled")
        self._history_thread = threading.Thread(target=self._export_worker, args=(path,), name="watchdog-export", daemon=True)
        self._history_thread.start()

    def _export_worker(self, path: str) -> None:
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".html":
                from .plotting import write_html_report
                out = write_html_report(self._last_records, path)
            else:
                import matplotlib.pyplot as plt
                from .plotting import make_figure, save_figure
                figure = make_figure(self._last_records)
                try:
                    out = save_figure(figure, path)
                finally:
                    plt.close(figure)
            self._event_queue.put(("export_ready", str(out)))
        except Exception as exc:
            self._event_queue.put(("export_error", f"{type(exc).__name__}: {exc}"))

    def _on_close(self) -> None:
        self._closed = True
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=4)
        self.root.destroy()

    def run(self) -> None:
        self._closed = False
        self._poll_job = self.root.after(120, self._poll_events)
        self.root.mainloop()


def run_gui(
    default_processes: Sequence[str] = (),
    default_interval_sec: float = 1.0,
    data_dir: str | Path = "data",
    disk_limit_mb: int = 100,
    fsync_every_rows: int = 1,
    backup_every_rows: int = 10000,
    backup_keep: int = 3,
    app_settings: AppSettings | None = None,
) -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    WatchdogGui(
        root,
        default_processes=default_processes,
        default_interval_sec=default_interval_sec,
        data_dir=data_dir,
        disk_limit_mb=disk_limit_mb,
        fsync_every_rows=fsync_every_rows,
        backup_every_rows=backup_every_rows,
        backup_keep=backup_keep,
        app_settings=app_settings,
    ).run()
