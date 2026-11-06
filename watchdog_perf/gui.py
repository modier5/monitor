"""Modern CustomTkinter desktop control panel and configuration editor."""

from __future__ import annotations

import queue
import sys
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import customtkinter as ctk
import tkinter as tk
from PIL import Image
from tkinter import filedialog, messagebox

from .alerting import DingTalkNotifier, EndConditionMonitor
from .config import AppSettings, ConditionRule, save_settings
from .metrics import machine_cpu_percent
from .monitor import MonitorConfig, PerfMonitor
from .storage import CsvStore, STATUS_OK


APP_BG = ("#F5F7FB", "#0B1019")
SURFACE = ("#FFFFFF", "#121A27")
SURFACE_ALT = ("#F0F3F8", "#0C131F")
TEXT = ("#172033", "#ECF2FB")
MUTED = ("#6E7B90", "#94A2B7")
BORDER = ("#DFE5EE", "#273348")
SIDEBAR = "#111C32"
SIDEBAR_TEXT = "#AEBBD0"
TEAL = "#137F87"
TEAL_HOVER = "#0F6B72"
TEAL_SOFT = ("#E5F6F4", "#173338")
DANGER = "#C94E5A"
DANGER_HOVER = "#A93A45"

METRIC_LABELS = {
    "cpu_machine_percent": "CPU 占用率 (%)",
    "memory_mb": "内存占用 (MB)",
}
METRIC_KEYS = {label: key for key, label in METRIC_LABELS.items()}


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
        self._icon_image: tk.PhotoImage | None = None
        self._brand_image: ctk.CTkImage | None = None
        self._active_page = "targets"
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._pages: dict[str, ctk.CTkFrame] = {}
        self.target_records: list[tuple[str, str]] = []
        self.rule_records: list[ConditionRule] = []
        self._font_name = "SF Pro Display" if sys.platform == "darwin" else "Segoe UI"
        self.target_labels = target_labels()

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
        self.combine_var = tk.StringVar(value=condition.combine)
        self.sustained_var = tk.StringVar(value=f"{condition.sustained_sec:g}")
        self.cooldown_var = tk.StringVar(value=f"{condition.cooldown_sec:g}")
        self.action_var = tk.StringVar(value=condition.action)
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

        root.title("WatchdogPerf · 资源监控")
        root.configure(fg_color=APP_BG)
        root.minsize(1040, 720)
        root.geometry("1240x820")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_icon_assets()
        self._load_targets()
        self._load_rules()
        self._build_ui()
        self._show_page("targets")

    def _font(self, size: int = 13, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family=self._font_name, size=size, weight=weight)

    def _load_icon_assets(self) -> None:
        icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.png"
        if not icon_path.exists():
            return
        try:
            self._icon_image = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._icon_image)
            pil_image = Image.open(icon_path).convert("RGBA")
            self._brand_image = ctk.CTkImage(
                light_image=pil_image,
                dark_image=pil_image,
                size=(38, 38),
            )
        except (tk.TclError, OSError):
            self._icon_image = None
            self._brand_image = None

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        main = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew", padx=(28, 30), pady=(24, 22))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self.header = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.header.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(
            self.header,
            text="监控目标",
            text_color=TEXT,
            font=self._font(26, "bold"),
            anchor="w",
        )
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ctk.CTkLabel(
            self.header,
            text="选择需要持续跟踪的应用、进程或 PID。",
            text_color=MUTED,
            font=self._font(13),
            anchor="w",
        )
        self.page_subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        status_badge = ctk.CTkFrame(
            self.header,
            fg_color=TEAL_SOFT,
            corner_radius=16,
            height=34,
        )
        status_badge.grid(row=0, column=1, rowspan=2, sticky="e")
        status_badge.grid_propagate(False)
        status_badge.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            status_badge,
            text="●",
            text_color="#24BFA9",
            font=self._font(12),
            width=24,
        ).grid(row=0, column=0, padx=(8, 0), pady=5)
        ctk.CTkLabel(
            status_badge,
            textvariable=self.live_var,
            text_color=("#12685F", "#79E2D4"),
            font=self._font(12, "bold"),
        ).grid(row=0, column=1, padx=(0, 13), pady=5)

        self.page_host = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)
        self._pages["overview"] = self._build_overview_page()
        self._pages["targets"] = self._build_targets_page()
        self._pages["storage"] = self._build_storage_page()
        self._pages["conditions"] = self._build_conditions_page()
        self._pages["notifications"] = self._build_notifications_page()

        self._build_footer(main)

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self.root,
            width=224,
            corner_radius=0,
            fg_color=SIDEBAR,
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(2, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=0)
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 28))
        if self._brand_image is not None:
            ctk.CTkLabel(brand, text="", image=self._brand_image, width=40).grid(row=0, column=0, padx=(0, 10))
        else:
            ctk.CTkLabel(
                brand,
                text="W",
                width=38,
                height=38,
                corner_radius=11,
                fg_color=TEAL,
                text_color="white",
                font=self._font(17, "bold"),
            ).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(
            brand,
            text="WatchdogPerf",
            text_color="#F7FBFF",
            font=self._font(17, "bold"),
        ).grid(row=0, column=1, sticky="w")

        nav = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=0)
        nav.grid(row=1, column=0, sticky="new", padx=12)
        nav_items = (
            ("overview", "◫", "运行概览"),
            ("targets", "◎", "监控目标"),
            ("storage", "▣", "采集与存储"),
            ("conditions", "◇", "结束条件"),
            ("notifications", "○", "通知设置"),
        )
        for row, (key, icon, text) in enumerate(nav_items):
            button = ctk.CTkButton(
                nav,
                text=f"{icon}    {text}",
                height=42,
                corner_radius=11,
                anchor="w",
                fg_color="transparent",
                hover_color="#1B2A44",
                text_color=SIDEBAR_TEXT,
                font=self._font(13),
                command=lambda page=key: self._show_page(page),
            )
            button.grid(row=row, column=0, sticky="ew", pady=3)
            self._nav_buttons[key] = button

        sync = ctk.CTkFrame(
            sidebar,
            fg_color="#17243A",
            border_width=1,
            border_color="#263550",
            corner_radius=14,
        )
        sync.grid(row=3, column=0, sticky="sew", padx=16, pady=18)
        ctk.CTkLabel(
            sync,
            text="配置已同步",
            text_color="#DFFBF6",
            font=self._font(12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 1))
        self.config_sync_label = ctk.CTkLabel(
            sync,
            text="config.json · 已加载",
            text_color="#8FA0B8",
            font=self._font(11),
            anchor="w",
        )
        self.config_sync_label.pack(fill="x", padx=14, pady=(0, 12))

    def _build_footer(self, parent: ctk.CTkFrame) -> None:
        footer = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            textvariable=self.status_var,
            text_color=MUTED,
            font=self._font(11),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(2, 12))
        self.load_history_button = self._secondary_button(footer, "加载 CSV", self._load_history, width=92)
        self.load_history_button.grid(row=0, column=1, padx=4)
        self.export_button = self._secondary_button(footer, "导出报告", self._export_figure, width=92)
        self.export_button.configure(state="disabled")
        self.export_button.grid(row=0, column=2, padx=4)
        self.save_button = self._secondary_button(footer, "保存配置", self._save_config, width=96)
        self.save_button.grid(row=0, column=3, padx=(12, 4))
        self.stop_button = ctk.CTkButton(
            footer,
            text="停止监控",
            width=96,
            height=38,
            corner_radius=11,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            font=self._font(12, "bold"),
            command=self._stop_monitor,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=4, padx=4)
        self.start_button = ctk.CTkButton(
            footer,
            text="▶  保存并开始",
            width=132,
            height=38,
            corner_radius=11,
            fg_color=TEAL,
            hover_color=TEAL_HOVER,
            font=self._font(12, "bold"),
            command=self._start_monitor,
        )
        self.start_button.grid(row=0, column=5, padx=(4, 0))

    def _page_frame(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.page_host, fg_color="transparent", corner_radius=0)
        frame.grid(row=0, column=0, sticky="nsew")
        return frame

    def _panel(self, parent, **kwargs) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            corner_radius=18,
            **kwargs,
        )

    def _panel_heading(self, parent, title: str, subtitle: str = "") -> None:
        ctk.CTkLabel(
            parent,
            text=title,
            text_color=TEXT,
            font=self._font(16, "bold"),
            anchor="w",
        ).pack(fill="x")
        if subtitle:
            ctk.CTkLabel(
                parent,
                text=subtitle,
                text_color=MUTED,
                font=self._font(12),
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

    def _build_targets_page(self) -> ctk.CTkFrame:
        page = self._page_frame()
        page.grid_columnconfigure(0, weight=3)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)

        left = self._panel(page)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(4, weight=1)
        heading = ctk.CTkFrame(left, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=22, pady=(21, 0))
        self._panel_heading(heading, "添加监控目标", "支持同时添加不同类型的目标，留空则监控整机。")

        self.target_kind = ctk.CTkSegmentedButton(
            left,
            values=list(self.target_labels.values()),
            height=38,
            corner_radius=11,
            fg_color=SURFACE_ALT,
            selected_color=TEAL,
            selected_hover_color=TEAL_HOVER,
            unselected_color=SURFACE_ALT,
            unselected_hover_color=("#E5EAF1", "#1A2433"),
            text_color=TEXT,
            font=self._font(12),
            command=self._target_kind_changed,
        )
        self.target_kind.grid(row=1, column=0, sticky="ew", padx=22, pady=(18, 12))
        self.target_kind.set(self.target_labels["exe"])

        input_row = ctk.CTkFrame(left, fg_color="transparent", corner_radius=0)
        input_row.grid(row=2, column=0, sticky="ew", padx=22)
        input_row.grid_columnconfigure(0, weight=1)
        self.target_entry = ctk.CTkEntry(
            input_row,
            textvariable=self.target_value_var,
            height=40,
            corner_radius=11,
            border_color=BORDER,
            fg_color=("#FFFFFF", "#0B111C"),
            text_color=TEXT,
            placeholder_text=target_placeholder("exe"),
            font=self._font(12),
        )
        self.target_entry.grid(row=0, column=0, sticky="ew")
        self.browse_target_button = self._secondary_button(input_row, "浏览…", self._browse_target, width=78)
        self.browse_target_button.grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            input_row,
            text="＋  添加",
            width=88,
            height=40,
            corner_radius=11,
            fg_color=TEAL,
            hover_color=TEAL_HOVER,
            font=self._font(12, "bold"),
            command=self._add_target,
        ).grid(row=0, column=2, padx=(8, 0))

        ctk.CTkLabel(
            left,
            text="已添加目标",
            text_color=TEXT,
            font=self._font(12, "bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=22, pady=(20, 8))
        self.targets_scroll = ctk.CTkScrollableFrame(
            left,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=("#D5DCE6", "#344055"),
            scrollbar_button_hover_color=("#C4CDD9", "#43516A"),
        )
        self.targets_scroll.grid(row=4, column=0, sticky="nsew", padx=(18, 10), pady=(0, 14))
        self.targets_scroll.grid_columnconfigure(0, weight=1)
        self._render_targets()

        right = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        live = self._panel(right)
        live.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        live_inner = ctk.CTkFrame(live, fg_color="transparent")
        live_inner.pack(fill="both", padx=20, pady=18)
        self._panel_heading(live_inner, "实时概览", "监控运行后实时更新")
        self.cpu_value_label, self.cpu_progress = self._meter(live_inner, "整机 CPU", "0.0%")
        self.memory_value_label, self.memory_progress = self._meter(live_inner, "目标内存", "0 MB")

        current = self._panel(right)
        current.grid(row=1, column=0, sticky="nsew", pady=(9, 0))
        current_inner = ctk.CTkFrame(current, fg_color="transparent")
        current_inner.pack(fill="both", expand=True, padx=20, pady=18)
        self._panel_heading(current_inner, "当前配置")
        self.summary_interval = self._summary_row(current_inner, "采样间隔", f"{self.app_settings.interval_sec:g} 秒")
        self.summary_children = self._summary_row(current_inner, "包含子进程", "开启" if self.children_var.get() else "关闭")
        self.summary_data = self._summary_row(current_inner, "CSV 路径", self._short_path(self.app_settings.data_dir))
        self.summary_backup = self._summary_row(current_inner, "自动备份", self._backup_summary())
        return page

    def _build_overview_page(self) -> ctk.CTkFrame:
        page = self._page_frame()
        page.grid_columnconfigure((0, 1, 2), weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.overview_cpu = self._stat_card(page, 0, "整机 CPU", "0.0%", "实时采样")
        self.overview_memory = self._stat_card(page, 1, "目标内存", "0 MB", "当前峰值")
        self.overview_samples = self._stat_card(page, 2, "已采集样本", "0", "本次运行")

        panel = self._panel(page)
        panel.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(18, 0))
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=26, pady=24)
        self._panel_heading(inner, "运行状态", "启动监控后，这里会显示最新采样和输出文件。")
        self.overview_status = ctk.CTkLabel(
            inner,
            text="尚未开始监控",
            text_color=TEXT,
            font=self._font(22, "bold"),
            anchor="center",
        )
        self.overview_status.pack(expand=True)
        ctk.CTkButton(
            inner,
            text="前往监控目标",
            width=128,
            height=40,
            corner_radius=11,
            fg_color=TEAL,
            hover_color=TEAL_HOVER,
            command=lambda: self._show_page("targets"),
        ).pack(pady=(0, 8))
        return page

    def _build_storage_page(self) -> ctk.CTkFrame:
        page = self._page_frame()
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        panel = self._panel(page)
        panel.grid(row=0, column=0, sticky="nsew")
        inner = ctk.CTkScrollableFrame(panel, fg_color="transparent", corner_radius=0)
        inner.pack(fill="both", expand=True, padx=12, pady=12)
        inner.grid_columnconfigure((0, 1), weight=1)

        heading = ctk.CTkFrame(inner, fg_color="transparent")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(5, 15))
        self._panel_heading(heading, "采样设置", "控制采集频率、CSV 输出及数据安全策略。")
        self._setting_field(inner, 1, 0, "采样间隔（秒）", self.interval_var, "建议 1 秒；更低间隔会增加磁盘写入。")
        self._setting_switch(inner, 1, 1, "包含子进程", self.children_var, "自动跟踪目标启动的全部子进程。")

        path_box = ctk.CTkFrame(inner, fg_color=SURFACE_ALT, corner_radius=14)
        path_box.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=10)
        path_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(path_box, text="CSV 保存位置", text_color=TEXT, font=self._font(13, "bold"), anchor="w").grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(13, 7))
        ctk.CTkEntry(path_box, textvariable=self.data_dir_var, height=40, corner_radius=10, border_color=BORDER, fg_color=SURFACE, text_color=TEXT).grid(row=1, column=0, sticky="ew", padx=(16, 8), pady=(0, 14))
        self._secondary_button(path_box, "选择…", self._browse_data_dir, width=76).grid(row=1, column=1, padx=(0, 8), pady=(0, 14))
        self._secondary_button(path_box, "打开目录", self._open_data_dir, width=86).grid(row=1, column=2, padx=(0, 16), pady=(0, 14))

        self._setting_field(inner, 3, 0, "磁盘保留阈值（MB）", self.disk_limit_var, "低于此空间时停止写入，避免磁盘写满。")
        self._setting_field(inner, 3, 1, "每 N 行强制落盘", self.fsync_var, "数值越小，异常断电时丢失的数据越少。")
        self._setting_field(inner, 4, 0, "每 N 行生成备份", self.backup_rows_var, "输入 0 关闭自动备份。")
        self._setting_field(inner, 4, 1, "保留备份数量", self.backup_keep_var, "自动删除更早的备份文件。")
        self._setting_field(inner, 5, 0, "状态输出间隔（秒）", self.self_status_var, "控制运行日志中的自身状态频率。")
        return page

    def _build_conditions_page(self) -> ctk.CTkFrame:
        page = self._page_frame()
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        panel = self._panel(page)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        top = ctk.CTkFrame(panel, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 10))
        top.grid_columnconfigure(0, weight=1)
        top_copy = ctk.CTkFrame(top, fg_color="transparent")
        top_copy.grid(row=0, column=0, rowspan=2, sticky="ew")
        self._panel_heading(top_copy, "自动结束条件", "规则持续满足指定时间后，可通知并停止采集。")
        ctk.CTkSwitch(top, text="启用", variable=self.condition_enabled_var, progress_color=TEAL, font=self._font(12)).grid(row=0, column=1, rowspan=2, sticky="e")

        settings = ctk.CTkFrame(panel, fg_color=SURFACE_ALT, corner_radius=14)
        settings.grid(row=1, column=0, sticky="ew", padx=22, pady=8)
        settings.grid_columnconfigure((0, 1, 2), weight=1)
        self._compact_option(settings, 0, "规则关系", self.combine_var, ["all", "any"])
        self._compact_option(settings, 1, "触发动作", self.action_var, ["notify_and_stop", "notify_only", "stop"])
        self._compact_entry(settings, 2, "持续时间（秒）", self.sustained_var)
        self._compact_entry(settings, 3, "冷却时间（秒）", self.cooldown_var)
        self._compact_entry(settings, 4, "退出宽限（秒）", self.targets_grace_var)
        ctk.CTkSwitch(settings, text="目标退出后结束", variable=self.targets_gone_var, progress_color=TEAL, font=self._font(12)).grid(row=1, column=2, sticky="w", padx=14, pady=(0, 14))

        rule_add = ctk.CTkFrame(panel, fg_color="transparent")
        rule_add.grid(row=2, column=0, sticky="ew", padx=22, pady=(12, 8))
        rule_add.grid_columnconfigure(1, weight=1)
        ctk.CTkSwitch(rule_add, text="启用", variable=self.rule_enabled_var, width=70, progress_color=TEAL, font=self._font(12)).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkOptionMenu(rule_add, variable=self.rule_metric_var, values=list(METRIC_LABELS.values()), height=38, corner_radius=10, fg_color=SURFACE_ALT, button_color=TEAL, text_color=TEXT, font=self._font(12)).grid(row=0, column=1, sticky="ew", padx=4)
        ctk.CTkOptionMenu(rule_add, variable=self.rule_operator_var, values=[">=", ">", "<=", "<"], width=72, height=38, corner_radius=10, fg_color=SURFACE_ALT, button_color=TEAL, text_color=TEXT).grid(row=0, column=2, padx=4)
        ctk.CTkEntry(rule_add, textvariable=self.rule_value_var, width=100, height=38, corner_radius=10, fg_color=SURFACE, border_color=BORDER, text_color=TEXT).grid(row=0, column=3, padx=4)
        ctk.CTkButton(rule_add, text="＋ 添加规则", width=100, height=38, corner_radius=10, fg_color=TEAL, hover_color=TEAL_HOVER, command=self._add_rule).grid(row=0, column=4, padx=(4, 0))

        self.rules_scroll = ctk.CTkScrollableFrame(panel, fg_color="transparent", corner_radius=0)
        self.rules_scroll.grid(row=3, column=0, sticky="nsew", padx=(18, 10), pady=(2, 14))
        self.rules_scroll.grid_columnconfigure(0, weight=1)
        self._render_rules()
        return page

    def _build_notifications_page(self) -> ctk.CTkFrame:
        page = self._page_frame()
        page.grid_columnconfigure(0, weight=1)
        panel = self._panel(page)
        panel.grid(row=0, column=0, sticky="new")
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=22)
        inner.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(inner, text="钉钉机器人", text_color=TEXT, font=self._font(16, "bold"), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkSwitch(inner, text="启用通知", variable=self.ding_enabled_var, progress_color=TEAL, font=self._font(12)).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(inner, text="触发结束条件时发送运行汇总与进程明细。", text_color=MUTED, font=self._font(12), anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 18))
        self._form_entry(inner, 2, "Webhook", self.webhook_var)
        self._form_entry(inner, 3, "加签 Secret", self.secret_var, show="•")
        self._form_entry(inner, 4, "请求超时（秒）", self.timeout_var, width=180)
        warning = ctk.CTkFrame(inner, fg_color=("#FFF7E8", "#302719"), corner_radius=12)
        warning.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ctk.CTkLabel(warning, text="密钥仅保存在本机 config.json，请勿把真实密钥提交到公开仓库。", text_color=("#8B6319", "#E7C57E"), font=self._font(11), anchor="w").pack(fill="x", padx=14, pady=11)
        return page

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
                frame.tkraise()
        for page_key, button in self._nav_buttons.items():
            active = page_key == key
            button.configure(
                fg_color="#1B3A47" if active else "transparent",
                text_color="#FFFFFF" if active else SIDEBAR_TEXT,
            )
        title, subtitle = titles[key]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)

    def _secondary_button(self, parent, text: str, command, width: int = 90) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            width=width,
            height=38,
            corner_radius=11,
            fg_color=SURFACE_ALT,
            hover_color=("#E2E7EE", "#263246"),
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=self._font(12),
            command=command,
        )

    def _meter(self, parent, label: str, value: str):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(17, 0))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text=label, text_color=MUTED, font=self._font(11), anchor="w").grid(row=0, column=0, sticky="w")
        value_label = ctk.CTkLabel(row, text=value, text_color=TEXT, font=self._font(12, "bold"), anchor="e")
        value_label.grid(row=0, column=1, sticky="e")
        progress = ctk.CTkProgressBar(row, height=7, corner_radius=4, progress_color=TEAL, fg_color=SURFACE_ALT)
        progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        progress.set(0)
        return value_label, progress

    def _summary_row(self, parent, label: str, value: str) -> ctk.CTkLabel:
        row = ctk.CTkFrame(parent, fg_color="transparent", height=45)
        row.pack(fill="x", pady=(8, 0))
        row.pack_propagate(False)
        ctk.CTkLabel(row, text=label, text_color=MUTED, font=self._font(11), anchor="w").pack(side="left")
        value_label = ctk.CTkLabel(row, text=value, text_color=TEXT, font=self._font(11, "bold"), anchor="e")
        value_label.pack(side="right")
        return value_label

    def _stat_card(self, parent, column: int, label: str, value: str, note: str) -> ctk.CTkLabel:
        card = self._panel(parent)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        ctk.CTkLabel(card, text=label, text_color=MUTED, font=self._font(11), anchor="w").pack(fill="x", padx=18, pady=(15, 1))
        value_label = ctk.CTkLabel(card, text=value, text_color=TEXT, font=self._font(24, "bold"), anchor="w")
        value_label.pack(fill="x", padx=18)
        ctk.CTkLabel(card, text=note, text_color=MUTED, font=self._font(10), anchor="w").pack(fill="x", padx=18, pady=(1, 15))
        return value_label

    def _setting_field(self, parent, row: int, column: int, label: str, variable: tk.Variable, hint: str) -> None:
        box = ctk.CTkFrame(parent, fg_color=SURFACE_ALT, corner_radius=14)
        box.grid(row=row, column=column, sticky="nsew", padx=12, pady=10)
        ctk.CTkLabel(box, text=label, text_color=TEXT, font=self._font(13, "bold"), anchor="w").pack(fill="x", padx=16, pady=(13, 2))
        ctk.CTkLabel(box, text=hint, text_color=MUTED, font=self._font(10), anchor="w", wraplength=370, justify="left").pack(fill="x", padx=16)
        ctk.CTkEntry(box, textvariable=variable, height=38, corner_radius=10, fg_color=SURFACE, border_color=BORDER, text_color=TEXT).pack(fill="x", padx=16, pady=(10, 14))

    def _setting_switch(self, parent, row: int, column: int, label: str, variable: tk.Variable, hint: str) -> None:
        box = ctk.CTkFrame(parent, fg_color=SURFACE_ALT, corner_radius=14)
        box.grid(row=row, column=column, sticky="nsew", padx=12, pady=10)
        ctk.CTkLabel(box, text=label, text_color=TEXT, font=self._font(13, "bold"), anchor="w").pack(fill="x", padx=16, pady=(13, 2))
        ctk.CTkLabel(box, text=hint, text_color=MUTED, font=self._font(10), anchor="w", wraplength=370, justify="left").pack(fill="x", padx=16)
        ctk.CTkSwitch(box, text="开启", variable=variable, progress_color=TEAL, font=self._font(11)).pack(anchor="w", padx=16, pady=(13, 15))

    def _compact_option(self, parent, column: int, label: str, variable: tk.Variable, values: list[str]) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=0, column=column, sticky="ew", padx=14, pady=13)
        ctk.CTkLabel(box, text=label, text_color=MUTED, font=self._font(10), anchor="w").pack(fill="x")
        ctk.CTkOptionMenu(box, variable=variable, values=values, height=34, corner_radius=9, fg_color=SURFACE, button_color=TEAL, text_color=TEXT, font=self._font(11)).pack(fill="x", pady=(5, 0))

    def _compact_entry(self, parent, column: int, label: str, variable: tk.Variable) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=1, column=column, sticky="ew", padx=14, pady=(0, 13))
        ctk.CTkLabel(box, text=label, text_color=MUTED, font=self._font(10), anchor="w").pack(fill="x")
        ctk.CTkEntry(box, textvariable=variable, height=34, corner_radius=9, fg_color=SURFACE, border_color=BORDER, text_color=TEXT).pack(fill="x", pady=(5, 0))

    def _form_entry(self, parent, row: int, label: str, variable: tk.Variable, show: str = "", width: int | None = None) -> None:
        ctk.CTkLabel(parent, text=label, text_color=TEXT, font=self._font(12), anchor="w").grid(row=row, column=0, sticky="w", pady=8)
        entry = ctk.CTkEntry(parent, textvariable=variable, show=show, height=40, corner_radius=10, fg_color=SURFACE_ALT, border_color=BORDER, text_color=TEXT)
        entry.grid(row=row, column=1, sticky="w" if width else "ew", padx=(18, 0), pady=8)
        if width:
            entry.configure(width=width)

    def _target_kind_changed(self, label: str) -> None:
        kind = next((key for key, text in self.target_labels.items() if text == label), "exe")
        self.target_entry.configure(placeholder_text=target_placeholder(kind))
        self.browse_target_button.configure(state="normal" if kind == "exe" else "disabled")
        self.target_value_var.set("")
        self.target_entry.focus_set()

    def _selected_target_kind(self) -> str:
        label = self.target_kind.get()
        return next((key for key, text in self.target_labels.items() if text == label), "exe")

    def _browse_target(self) -> None:
        if sys.platform == "darwin":
            selected = filedialog.askopenfilename(
                title="选择要监控的 macOS 应用程序 (.app)",
                initialdir="/Applications",
                filetypes=[("macOS 应用程序", "*.app"), ("Unix 可执行文件", "*")],
            )
        else:
            selected = filedialog.askopenfilename(
                title="选择要监控的 Windows 程序 (.exe)",
                filetypes=[("Windows 可执行文件", "*.exe"), ("所有文件", "*.*")],
            )
        if selected:
            self.target_value_var.set(selected)

    def _add_target(self) -> None:
        kind = self._selected_target_kind()
        value = self.target_value_var.get().strip().strip('"')
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
        if not self.target_records:
            empty = ctk.CTkFrame(self.targets_scroll, fg_color=SURFACE_ALT, corner_radius=13)
            empty.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
            ctk.CTkLabel(empty, text="尚未添加目标，将监控整机 CPU 与内存", text_color=MUTED, font=self._font(11)).pack(pady=25)
            return
        for index, (kind, value) in enumerate(self.target_records):
            row = ctk.CTkFrame(self.targets_scroll, fg_color=SURFACE_ALT, corner_radius=13, height=58)
            row.grid(row=index, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(1, weight=1)
            icon_text = {"exe": "▣", "pid": "#", "name": ">_"}[kind]
            ctk.CTkLabel(row, text=icon_text, width=36, height=36, corner_radius=10, fg_color=TEAL_SOFT, text_color=TEAL, font=self._font(12, "bold")).grid(row=0, column=0, padx=(11, 10), pady=11)
            copy = ctk.CTkFrame(row, fg_color="transparent")
            copy.grid(row=0, column=1, sticky="ew")
            display = Path(value).name if kind == "exe" else value
            ctk.CTkLabel(copy, text=display or value, text_color=TEXT, font=self._font(12, "bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(copy, text=f"{self.target_labels[kind]} · {value}", text_color=MUTED, font=self._font(10), anchor="w").pack(fill="x")
            ctk.CTkButton(row, text="×", width=32, height=32, corner_radius=9, fg_color="transparent", hover_color=("#FDECEE", "#381D24"), text_color=MUTED, font=self._font(18), command=lambda item=index: self._remove_target(item)).grid(row=0, column=2, padx=11)

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
                import os
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(path)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc))

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
            empty = ctk.CTkFrame(self.rules_scroll, fg_color=SURFACE_ALT, corner_radius=13)
            empty.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
            ctk.CTkLabel(empty, text="尚未添加指标规则", text_color=MUTED, font=self._font(11)).pack(pady=24)
            return
        for index, rule in enumerate(self.rule_records):
            row = ctk.CTkFrame(self.rules_scroll, fg_color=SURFACE_ALT, corner_radius=13, height=54)
            row.grid(row=index, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="●" if rule.enabled else "○", width=34, text_color="#24BFA9" if rule.enabled else MUTED, font=self._font(12)).grid(row=0, column=0, padx=(10, 4))
            metric = METRIC_LABELS.get(rule.metric, rule.metric)
            ctk.CTkLabel(row, text=metric, text_color=TEXT, font=self._font(12, "bold"), anchor="w").grid(row=0, column=1, sticky="ew", pady=14)
            ctk.CTkLabel(row, text=f"{rule.operator}  {rule.value:g}", text_color=TEXT, font=self._font(12), width=110).grid(row=0, column=2)
            ctk.CTkButton(row, text="×", width=32, height=32, corner_radius=9, fg_color="transparent", hover_color=("#FDECEE", "#381D24"), text_color=MUTED, font=self._font(18), command=lambda item=index: self._remove_rule(item)).grid(row=0, column=3, padx=10)

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
            condition.combine = self.combine_var.get()
            condition.sustained_sec = float(self._number(self.sustained_var.get(), "持续时间", minimum=0))
            condition.cooldown_sec = float(self._number(self.cooldown_var.get(), "冷却时间", minimum=0))
            condition.action = self.action_var.get()
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
            self.status_var.set(f"配置已保存：{saved}  ·  数据目录：{self.data_dir}")
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
        self.summary_data.configure(text=self._short_path(self.app_settings.data_dir))
        self.summary_backup.configure(text=self._backup_summary())

    def _backup_summary(self) -> str:
        rows = self.app_settings.backup_every_rows
        return "关闭" if rows <= 0 else f"每 {rows:,} 行"

    @staticmethod
    def _short_path(path: str | Path) -> str:
        text = str(path)
        return text if len(text) <= 24 else f"…{text[-22:]}"

    def _targets(self) -> tuple[list[str], list[str], list[int]]:
        processes = [value for kind, value in self.target_records if kind == "name"]
        exe_paths = [value for kind, value in self.target_records if kind == "exe"]
        pids = [int(value) for kind, value in self.target_records if kind == "pid"]
        return processes, exe_paths, pids

    def _start_monitor(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        if not self._save_config(show_confirmation=False):
            return
        processes, exe_paths, pids = self._targets()
        target_text = ", ".join(processes + [f"APP:{path}" for path in exe_paths] + [f"PID:{pid}" for pid in pids]) or "整机资源"
        self.status_var.set(f"监控目标：{target_text}  ·  数据目录：{self.data_dir}")
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
        self.stop_button.configure(state="normal")
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
                    self.overview_status.configure(text=f"正在写入\n{event[1]}")
                    self.status_var.set(f"正在写入：{event[1]}")
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
                    self.overview_status.configure(text=f"最新采样  {now:%H:%M:%S}\n本次写入 {len(rows)} 条记录")
                    self.live_var.set(f"CPU {cpu:.1f}% · {memory:.0f} MB")
                elif kind == "alert":
                    self.live_var.set("结束条件已触发")
                    self.status_var.set(f"触发条件：{event[1]}  ·  钉钉通知：{'已发送' if event[2] else '未启用或发送失败'}")
                elif kind == "stopped":
                    self.live_var.set(event[1])
                    self.overview_status.configure(text="监控已安全停止")
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._worker_thread = None
                elif kind == "error":
                    self.live_var.set("采集异常")
                    self.overview_status.configure(text="采集异常")
                    self.status_var.set(event[1])
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    messagebox.showerror("采集异常", event[1])
                    self._worker_thread = None
                elif kind == "history_ready":
                    html_path = Path(event[1])
                    self._last_html_path = html_path
                    self._records_count = int(event[2])
                    self.export_button.configure(state="normal")
                    self.load_history_button.configure(state="normal")
                    self.status_var.set(f"报告已生成：{html_path}  ·  {event[3]} 个文件 / {event[2]} 条样本")
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
                    self.status_var.set(f"报告已导出：{event[1]}")
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
        self.status_var.set(f"正在解析 {len(selected)} 个 CSV 并生成 HTML…")
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
