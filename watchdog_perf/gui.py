"""Polished Tkinter desktop control panel and configuration editor."""

from __future__ import annotations

import queue
import sys
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .alerting import DingTalkNotifier, EndConditionMonitor
from .config import AppSettings, ConditionRule, save_settings
from .metrics import machine_cpu_percent
from .monitor import MonitorConfig, PerfMonitor
from .storage import CsvStore, STATUS_OK


BG = "#f4f7fb"
CARD = "#ffffff"
NAVY = "#10243e"
MUTED = "#617083"
ACCENT = "#087e8b"
ACCENT_DARK = "#066873"
RED = "#c2414b"

TARGET_LABELS = {
    "exe": "应用 / 可执行文件",
    "pid": "PID",
    "name": "进程名",
}
TARGET_KEYS = {label: key for key, label in TARGET_LABELS.items()}
METRIC_LABELS = {
    "cpu_machine_percent": "CPU 占用率 (%)",
    "memory_mb": "内存占用 (MB)",
}
METRIC_KEYS = {label: key for key, label in METRIC_LABELS.items()}


class WatchdogGui:
    def __init__(
        self,
        root: tk.Tk,
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

        self.live_var = tk.StringVar(value="准备就绪")
        self.status_var = tk.StringVar(value=f"数据目录：{self.data_dir}")
        self.count_var = tk.StringVar(value="0 个样本")
        self.target_type_var = tk.StringVar(value="exe")
        self.target_value_var = tk.StringVar()

        self.interval_var = tk.StringVar(value=f"{self.app_settings.interval_sec:g}")
        self.children_var = tk.BooleanVar(value=self.app_settings.include_children)
        self.data_dir_var = tk.StringVar(value=str(self.app_settings.data_dir))
        self.disk_limit_var = tk.StringVar(value=str(self.app_settings.disk_limit_mb))
        self.fsync_var = tk.StringVar(value=str(self.app_settings.fsync_every_rows))
        self.backup_rows_var = tk.StringVar(value=str(self.app_settings.backup_every_rows))
        self.backup_keep_var = tk.StringVar(value=str(self.app_settings.backup_keep))
        self.self_status_var = tk.StringVar(
            value=f"{self.app_settings.self_status_interval_sec:g}"
        )

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
        root.configure(bg=BG)
        root.minsize(980, 700)
        root.geometry("1180x820")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()
        self._configure_styles()
        self._build_ui()
        self._load_targets()
        self._load_rules()

    def _set_window_icon(self) -> None:
        icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.png"
        if not icon_path.exists():
            return
        try:
            self._icon_image = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._icon_image)
        except tk.TclError:
            self._icon_image = None

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=NAVY, font=("TkDefaultFont", 10))
        style.configure("Card.TLabel", background=CARD, foreground=NAVY)
        style.configure("Title.TLabel", background=BG, foreground=NAVY, font=("TkDefaultFont", 22, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("TkDefaultFont", 10))
        style.configure("Section.TLabel", background=CARD, foreground=NAVY, font=("TkDefaultFont", 12, "bold"))
        style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=("TkDefaultFont", 9))
        style.configure("Status.TLabel", background=NAVY, foreground="white", font=("TkDefaultFont", 10, "bold"))
        style.configure("Accent.TButton", background=ACCENT, foreground="white", padding=(16, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#8caeb2")])
        style.configure("Danger.TButton", background=RED, foreground="white", padding=(14, 9), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#9f3039")])
        style.configure("Soft.TButton", background="#e8eef5", foreground=NAVY, padding=(12, 8), borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#d8e3ef")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("TkDefaultFont", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
        style.configure("Treeview", rowheight=30, background=CARD, fieldbackground=CARD, foreground=NAVY)
        style.configure("Treeview.Heading", background="#eaf0f6", foreground=NAVY, font=("TkDefaultFont", 9, "bold"))

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=(24, 18, 24, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="WatchdogPerf", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="跨平台资源监控、配置管理与报告分析",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        config_path = self.app_settings.config_path or Path("config.json")
        ttk.Label(
            header,
            text=f"配置文件  {config_path}",
            style="Subtitle.TLabel",
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        status = tk.Frame(self.root, bg=NAVY, padx=22, pady=13)
        status.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))
        status.columnconfigure(1, weight=1)
        tk.Label(status, text="●", bg=NAVY, fg="#25d0b1", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, padx=(0, 9))
        tk.Label(status, textvariable=self.live_var, bg=NAVY, fg="white", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(status, textvariable=self.count_var, bg=NAVY, fg="#cbd8e6").grid(row=0, column=2, sticky="e")

        body = ttk.Frame(self.root, padding=(24, 0, 24, 0))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(body)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_targets_tab()
        self._build_storage_tab()
        self._build_condition_tab()
        self._build_notify_tab()

        footer = ttk.Frame(self.root, padding=(24, 12, 24, 18))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        self.load_history_button = ttk.Button(footer, text="加载 CSV", style="Soft.TButton", command=self._load_history)
        self.load_history_button.grid(row=0, column=1, padx=(8, 0))
        self.export_button = ttk.Button(footer, text="导出报告", style="Soft.TButton", command=self._export_figure, state="disabled")
        self.export_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(footer, text="保存配置", style="Soft.TButton", command=self._save_config).grid(row=0, column=3, padx=(16, 0))
        self.stop_button = ttk.Button(footer, text="停止监控", style="Danger.TButton", command=self._stop_monitor, state="disabled")
        self.stop_button.grid(row=0, column=4, padx=(8, 0))
        self.start_button = ttk.Button(footer, text="保存并开始监控", style="Accent.TButton", command=self._start_monitor)
        self.start_button.grid(row=0, column=5, padx=(8, 0))

    def _new_tab(self, title: str) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=20)
        tab.columnconfigure(0, weight=1)
        self.notebook.add(tab, text=title)
        return tab

    def _build_targets_tab(self) -> None:
        tab = self._new_tab("监控目标")
        ttk.Label(tab, text="选择添加方式", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            tab,
            text="可同时添加多种目标；列表为空时监控整机 CPU 与内存。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 14))

        selector = ttk.Frame(tab, style="Card.TFrame")
        selector.grid(row=2, column=0, sticky="ew")
        for index, (key, label) in enumerate(TARGET_LABELS.items()):
            ttk.Radiobutton(
                selector,
                text=label,
                value=key,
                variable=self.target_type_var,
                command=self._target_type_changed,
            ).grid(row=0, column=index, padx=(0, 20), sticky="w")

        add_row = ttk.Frame(tab, style="Card.TFrame")
        add_row.grid(row=3, column=0, sticky="ew", pady=(12, 12))
        add_row.columnconfigure(0, weight=1)
        self.target_entry = ttk.Entry(add_row, textvariable=self.target_value_var)
        self.target_entry.grid(row=0, column=0, sticky="ew")
        self.browse_target_button = ttk.Button(add_row, text="浏览…", style="Soft.TButton", command=self._browse_target)
        self.browse_target_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(add_row, text="添加目标", style="Accent.TButton", command=self._add_target).grid(row=0, column=2, padx=(8, 0))

        self.target_tree = ttk.Treeview(tab, columns=("type", "value"), show="headings", height=9, selectmode="extended")
        self.target_tree.heading("type", text="类型")
        self.target_tree.heading("value", text="目标")
        self.target_tree.column("type", width=180, stretch=False)
        self.target_tree.column("value", width=680, stretch=True)
        self.target_tree.grid(row=4, column=0, sticky="nsew")
        tab.rowconfigure(4, weight=1)

        target_actions = ttk.Frame(tab, style="Card.TFrame")
        target_actions.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(target_actions, text="移除选中", style="Soft.TButton", command=self._remove_targets).grid(row=0, column=0)
        ttk.Button(target_actions, text="清空（仅监控整机）", style="Soft.TButton", command=self._clear_targets).grid(row=0, column=1, padx=(8, 0))

    def _build_storage_tab(self) -> None:
        tab = self._new_tab("采集与存储")
        ttk.Label(tab, text="采样设置", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self._field(tab, 1, 0, "采样间隔（秒）", self.interval_var)
        ttk.Checkbutton(tab, text="包含子进程", variable=self.children_var).grid(row=1, column=2, columnspan=2, sticky="w", padx=(22, 0), pady=9)

        ttk.Label(tab, text="CSV 保存位置", style="Section.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(20, 8))
        path_row = ttk.Frame(tab, style="Card.TFrame")
        path_row.grid(row=3, column=0, columnspan=4, sticky="ew")
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=self.data_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(path_row, text="选择文件夹…", style="Soft.TButton", command=self._browse_data_dir).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(path_row, text="打开目录", style="Soft.TButton", command=self._open_data_dir).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(tab, text="写入与备份", style="Section.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(24, 6))
        self._field(tab, 5, 0, "磁盘保留阈值（MB）", self.disk_limit_var)
        self._field(tab, 5, 2, "每 N 行强制落盘", self.fsync_var)
        self._field(tab, 6, 0, "每 N 行生成备份（0=关闭）", self.backup_rows_var)
        self._field(tab, 6, 2, "保留备份数量", self.backup_keep_var)
        self._field(tab, 7, 0, "自身状态输出间隔（秒）", self.self_status_var)
        ttk.Label(
            tab,
            text="提示：数据目录可放在本机任意可写位置。配置保存后，新监控任务会写入该目录。",
            style="Hint.TLabel",
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(20, 0))

    def _build_condition_tab(self) -> None:
        tab = self._new_tab("结束条件")
        top = ttk.Frame(tab, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(top, text="启用自动结束条件", variable=self.condition_enabled_var).grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="规则关系", style="Card.TLabel").grid(row=0, column=1, padx=(28, 6))
        ttk.Combobox(top, textvariable=self.combine_var, values=("all", "any"), state="readonly", width=8).grid(row=0, column=2)
        ttk.Label(top, text="动作", style="Card.TLabel").grid(row=0, column=3, padx=(22, 6))
        ttk.Combobox(top, textvariable=self.action_var, values=("notify_and_stop", "notify_only", "stop"), state="readonly", width=18).grid(row=0, column=4)

        duration = ttk.Frame(tab, style="Card.TFrame")
        duration.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        self._inline_field(duration, 0, "持续时间（秒）", self.sustained_var)
        self._inline_field(duration, 2, "冷却时间（秒）", self.cooldown_var)
        ttk.Checkbutton(duration, text="目标退出后结束", variable=self.targets_gone_var).grid(row=0, column=4, padx=(24, 5))
        ttk.Entry(duration, textvariable=self.targets_grace_var, width=8).grid(row=0, column=5)
        ttk.Label(duration, text="秒宽限", style="Card.TLabel").grid(row=0, column=6, padx=(5, 0))

        ttk.Label(tab, text="指标规则", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 8))
        rule_add = ttk.Frame(tab, style="Card.TFrame")
        rule_add.grid(row=3, column=0, sticky="ew")
        ttk.Checkbutton(rule_add, text="启用", variable=self.rule_enabled_var).grid(row=0, column=0, padx=(0, 8))
        ttk.Combobox(rule_add, textvariable=self.rule_metric_var, values=tuple(METRIC_LABELS.values()), state="readonly", width=20).grid(row=0, column=1)
        ttk.Combobox(rule_add, textvariable=self.rule_operator_var, values=(">=", ">", "<=", "<"), state="readonly", width=6).grid(row=0, column=2, padx=(8, 0))
        ttk.Entry(rule_add, textvariable=self.rule_value_var, width=12).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(rule_add, text="添加规则", style="Accent.TButton", command=self._add_rule).grid(row=0, column=4, padx=(8, 0))

        self.rule_tree = ttk.Treeview(tab, columns=("enabled", "metric", "operator", "value"), show="headings", height=7)
        for column, text, width in (
            ("enabled", "启用", 80),
            ("metric", "指标", 280),
            ("operator", "关系", 90),
            ("value", "阈值", 140),
        ):
            self.rule_tree.heading(column, text=text)
            self.rule_tree.column(column, width=width, stretch=column == "metric")
        self.rule_tree.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        tab.rowconfigure(4, weight=1)
        ttk.Button(tab, text="移除选中规则", style="Soft.TButton", command=self._remove_rules).grid(row=5, column=0, sticky="w", pady=(10, 0))

    def _build_notify_tab(self) -> None:
        tab = self._new_tab("通知设置")
        ttk.Label(tab, text="钉钉机器人", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(tab, text="启用钉钉通知", variable=self.ding_enabled_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 8))
        self._wide_field(tab, 2, "Webhook", self.webhook_var)
        self._wide_field(tab, 3, "加签 Secret", self.secret_var, show="•")
        self._wide_field(tab, 4, "请求超时（秒）", self.timeout_var, width=16)
        ttk.Label(
            tab,
            text="Webhook 与 Secret 会保存在本机 config.json。请勿将包含真实密钥的配置提交到公开仓库。",
            style="Hint.TLabel",
            wraplength=760,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(18, 0))

    def _field(self, parent: ttk.Frame, row: int, column: int, label: str, variable: tk.Variable) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=column, sticky="w", pady=9)
        ttk.Entry(parent, textvariable=variable, width=18).grid(row=row, column=column + 1, sticky="w", padx=(8, 28), pady=9)

    def _inline_field(self, parent: ttk.Frame, column: int, label: str, variable: tk.Variable) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=10).grid(row=0, column=column + 1, padx=(6, 0))

    def _wide_field(self, parent: ttk.Frame, row: int, label: str, variable: tk.Variable, show: str = "", width: int | None = None) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=9)
        entry = ttk.Entry(parent, textvariable=variable, show=show)
        if width:
            entry.configure(width=width)
            entry.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=9)
        else:
            parent.columnconfigure(1, weight=1)
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=9)

    def _target_type_changed(self) -> None:
        kind = self.target_type_var.get()
        self.browse_target_button.configure(state="normal" if kind == "exe" else "disabled")
        hints = {"exe": "选择或输入应用/可执行文件路径", "pid": "输入数字 PID，例如 1234", "name": "输入进程名，例如 ffmpeg.exe 或 Google Chrome"}
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, "")
        self.status_var.set(hints.get(kind, "请输入监控目标"))

    def _browse_target(self) -> None:
        if sys.platform == "darwin":
            selected = filedialog.askopenfilename(
                title="选择应用或可执行文件",
                initialdir="/Applications",
                filetypes=[("macOS 应用", "*.app"), ("所有文件", "*.*")],
            )
        else:
            selected = filedialog.askopenfilename(
                title="选择可执行文件",
                filetypes=[("Windows 可执行文件", "*.exe"), ("所有文件", "*.*")],
            )
        if selected:
            self.target_value_var.set(selected)

    def _add_target(self) -> None:
        kind = self.target_type_var.get()
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
        existing = {(self.target_tree.set(item, "type"), self.target_tree.set(item, "value")) for item in self.target_tree.get_children()}
        record = (TARGET_LABELS[kind], value)
        if record in existing:
            messagebox.showinfo("目标已存在", "该监控目标已经在列表中。")
            return
        self.target_tree.insert("", "end", values=record)
        self.target_value_var.set("")

    def _remove_targets(self) -> None:
        for item in self.target_tree.selection():
            self.target_tree.delete(item)

    def _clear_targets(self) -> None:
        for item in self.target_tree.get_children():
            self.target_tree.delete(item)

    def _load_targets(self) -> None:
        for path in self.app_settings.exe_paths:
            self.target_tree.insert("", "end", values=(TARGET_LABELS["exe"], path))
        for pid in self.app_settings.pids:
            self.target_tree.insert("", "end", values=(TARGET_LABELS["pid"], str(pid)))
        for name in self.app_settings.processes:
            self.target_tree.insert("", "end", values=(TARGET_LABELS["name"], name))

    def _browse_data_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择 CSV 保存目录", initialdir=self.data_dir_var.get() or str(Path.home()))
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
        for rule in self.app_settings.end_condition.rules:
            self.rule_tree.insert("", "end", values=("是" if rule.enabled else "否", METRIC_LABELS.get(rule.metric, rule.metric), rule.operator, f"{rule.value:g}"))

    def _add_rule(self) -> None:
        try:
            value = float(self.rule_value_var.get())
        except ValueError:
            messagebox.showerror("阈值无效", "规则阈值必须是数字。")
            return
        self.rule_tree.insert("", "end", values=("是" if self.rule_enabled_var.get() else "否", self.rule_metric_var.get(), self.rule_operator_var.get(), f"{value:g}"))

    def _remove_rules(self) -> None:
        for item in self.rule_tree.selection():
            self.rule_tree.delete(item)

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
            processes: list[str] = []
            exe_paths: list[str] = []
            pids: list[int] = []
            for item in self.target_tree.get_children():
                kind = TARGET_KEYS.get(self.target_tree.set(item, "type"))
                value = self.target_tree.set(item, "value").strip()
                if kind == "exe":
                    exe_paths.append(value)
                elif kind == "pid":
                    pids.append(int(value))
                elif kind == "name":
                    processes.append(value)

            settings = self.app_settings
            settings.mode = "system" if not (processes or exe_paths or pids) else "tree"
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
            rules: list[ConditionRule] = []
            for item in self.rule_tree.get_children():
                rules.append(ConditionRule(
                    metric=METRIC_KEYS.get(self.rule_tree.set(item, "metric"), self.rule_tree.set(item, "metric")),
                    operator=self.rule_tree.set(item, "operator"),
                    value=float(self.rule_tree.set(item, "value")),
                    enabled=self.rule_tree.set(item, "enabled") == "是",
                ))
            condition.rules = rules

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
            if show_confirmation:
                messagebox.showinfo("保存成功", f"配置已写入：\n{saved}")
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置无法保存", str(exc))
            return False

    def _targets(self) -> tuple[list[str], list[str], list[int]]:
        processes: list[str] = []
        exe_paths: list[str] = []
        pids: list[int] = []
        for item in self.target_tree.get_children():
            kind = TARGET_KEYS.get(self.target_tree.set(item, "type"))
            value = self.target_tree.set(item, "value")
            if kind == "name":
                processes.append(value)
            elif kind == "exe":
                exe_paths.append(value)
            elif kind == "pid":
                pids.append(int(value))
        return processes, exe_paths, pids

    def _start_monitor(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        if not self._save_config(show_confirmation=False):
            return
        processes, exe_paths, pids = self._targets()
        interval = self.app_settings.interval_sec
        target_text = ", ".join(processes + [f"APP:{path}" for path in exe_paths] + [f"PID:{pid}" for pid in pids]) or "整机资源"
        self.status_var.set(f"监控目标：{target_text}  ·  数据目录：{self.data_dir}")
        self._stop_event = threading.Event()
        self._sample_count = 0
        self.count_var.set("0 个样本")
        self._worker_thread = threading.Thread(
            target=self._monitor_worker,
            args=(processes, exe_paths, pids, interval, bool(self.children_var.get())),
            name="watchdog-collector",
            daemon=True,
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.live_var.set("正在启动采集…")
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
            store = CsvStore(
                data_dir=self.data_dir,
                disk_limit_mb=self.disk_limit_mb,
                fsync_every_rows=self.fsync_every_rows,
                backup_every_rows=self.backup_every_rows,
                backup_keep=self.backup_keep,
            )
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
            store.close()
            self._event_queue.put(("stopped", "监控已停止"))
        except Exception as exc:
            self._event_queue.put(("error", f"{type(exc).__name__}: {exc}"))

    def _stop_monitor(self) -> None:
        self._stop_event.set()
        self.live_var.set("正在安全停止…")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                kind = event[0]
                if kind == "started":
                    self.live_var.set("监控运行中")
                    self.status_var.set(f"正在写入：{event[1]}")
                elif kind == "sample":
                    _, now, rows = event
                    self._sample_count += len(rows)
                    self.count_var.set(f"{self._sample_count} 个样本")
                    ok_rows = [row for row in rows if len(row) >= 6 and row[4] == STATUS_OK]
                    system_rows = [row for row in ok_rows if str(row[0]).upper() == "SYSTEM" and str(row[1]) == "-1"]
                    target_rows = [row for row in ok_rows if row not in system_rows]
                    system_cpu = [float(row[2]) for row in system_rows if str(row[2]).strip() and str(row[2]) != "None"]
                    target_cpu = [float(row[2]) for row in target_rows if str(row[2]).strip() and str(row[2]) != "None"]
                    memory_values = [float(row[3]) for row in (target_rows or system_rows) if str(row[3]).strip() and str(row[3]) != "None"]
                    total_cpu = max(system_cpu) if system_cpu else (machine_cpu_percent(sum(target_cpu)) if target_cpu else None)
                    text = f"{now:%H:%M:%S}  ·  本次 {len(rows)} 条"
                    if total_cpu is not None:
                        text += f"  ·  CPU {total_cpu:.1f}%"
                    if memory_values:
                        text += f"  ·  内存峰值 {max(memory_values):.1f} MB"
                    self.live_var.set(text)
                elif kind == "alert":
                    self.live_var.set("结束条件已触发")
                    self.status_var.set(f"触发条件：{event[1]}  ·  钉钉通知：{'已发送' if event[2] else '未启用或发送失败'}")
                elif kind == "stopped":
                    self.live_var.set(event[1])
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._worker_thread = None
                elif kind == "error":
                    self.live_var.set("采集异常")
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
    root = tk.Tk()
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
