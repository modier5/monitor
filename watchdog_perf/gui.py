"""Tkinter control panel for live monitoring and offline HTML reporting."""

from __future__ import annotations

import queue
import re
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .alerting import DingTalkNotifier, EndConditionMonitor
from .config import AppSettings
from .metrics import machine_cpu_percent
from .monitor import MonitorConfig, PerfMonitor
from .storage import CsvStore, STATUS_OK


def _split_processes(text: str) -> list[str]:
    parts = re.split(r"[,，;；]+", text or "")
    return [part.strip() for part in parts if part.strip()]


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
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.disk_limit_mb = disk_limit_mb
        self.fsync_every_rows = max(1, fsync_every_rows)
        self.backup_every_rows = max(0, backup_every_rows)
        self.backup_keep = max(1, backup_keep)
        self.app_settings = app_settings
        self._end_condition = (
            app_settings.end_condition if app_settings is not None else None
        )
        self._dingtalk = app_settings.dingtalk if app_settings is not None else None

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

        self.target_var = tk.StringVar(value=", ".join(default_processes))
        self.interval_var = tk.StringVar(value=f"{default_interval_sec:g}")
        self.live_var = tk.StringVar(value="未开始监控")
        self.status_var = tk.StringVar(value=f"数据目录: {self.data_dir}")
        self.count_var = tk.StringVar(value="样本 0")
        self.children_var = tk.BooleanVar(
            value=bool(
                app_settings.include_children if app_settings is not None else True
            )
        )

        root.title("Watchdog-Perf 进程性能监控与回溯")
        root.minsize(980, 620)
        root.geometry("1180x760")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="目标进程:").grid(row=0, column=0, sticky="w")
        self.target_entry = ttk.Entry(top, textvariable=self.target_var)
        self.target_entry.grid(row=0, column=1, sticky="ew", padx=(4, 10))
        ttk.Label(top, text="采样间隔:").grid(row=0, column=2, sticky="e")
        self.interval_spin = tk.Spinbox(
            top,
            from_=0.5,
            to=60.0,
            increment=0.5,
            width=6,
            textvariable=self.interval_var,
        )
        self.interval_spin.grid(row=0, column=3, padx=(4, 2))
        ttk.Label(top, text="秒").grid(row=0, column=4, sticky="w")

        ttk.Label(
            top,
            text=(
                "留空仅监控系统全局；非空时同时记录进程与整机占用；支持 * 通配符、"
                "PID:1234、EXE:C:\\path\\app.exe"
            ),
            foreground="#666666",
        ).grid(row=0, column=5, columnspan=4, sticky="e", padx=(14, 0))

        controls = ttk.Frame(top)
        controls.grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 0))
        self.start_button = ttk.Button(
            controls, text="开始监控", command=self._start_monitor
        )
        self.start_button.grid(row=0, column=0)
        self.stop_button = ttk.Button(
            controls, text="停止监控", command=self._stop_monitor, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, padx=(8, 18))
        self.load_history_button = ttk.Button(
            controls, text="加载 CSV 生成 HTML", command=self._load_history
        )
        self.load_history_button.grid(row=0, column=2, padx=(0, 8))
        self.export_button = ttk.Button(
            controls,
            text="导出 PNG/SVG/HTML",
            command=self._export_figure,
            state="disabled",
        )
        self.export_button.grid(row=0, column=3)
        self.children_check = ttk.Checkbutton(
            controls,
            text="包含子进程",
            variable=self.children_var,
        )
        self.children_check.grid(row=0, column=4, padx=(8, 0))

        self.live_label = ttk.Label(
            controls, textvariable=self.live_var, font=("TkDefaultFont", 10, "bold")
        )
        self.live_label.grid(row=0, column=5, padx=(18, 10))
        ttk.Label(controls, textvariable=self.count_var).grid(
            row=0, column=6, sticky="w"
        )

        info_frame = ttk.Frame(self.root)
        info_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(2, 0))
        info_frame.columnconfigure(0, weight=1)
        hint = ttk.Label(
            info_frame,
            text=(
                "1) 开始监控后，后台线程持续采集并实时写入 CSV；\n"
                "2) 加载 CSV / 生成 HTML 报告在独立线程执行，不会暂停正在运行的监控；\n"
                "3) 交互 HTML 报告包含上下两张图：CPU 占用率与内存占用，可缩放/平移/筛选图例。"
            ),
            foreground="#555555",
            justify="left",
        )
        hint.grid(row=0, column=0, sticky="nw", padx=20, pady=24)

        bottom = ttk.Frame(self.root, padding=(10, 4, 10, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(
            row=0, column=0, sticky="w"
        )

    def _start_monitor(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return

        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError("采样间隔必须大于 0")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        processes: list[str] = []
        pids: list[int] = []
        exe_paths: list[str] = []
        for raw in _split_processes(self.target_var.get()):
            lowered = raw.lower()
            if lowered.startswith("pid:") or lowered.startswith("pid="):
                try:
                    pids.append(int(raw.split(":", 1)[-1].split("=", 1)[-1]))
                except ValueError:
                    messagebox.showerror("参数错误", f"无效 PID: {raw}")
                    return
            elif lowered.startswith("exe:") or lowered.startswith("path="):
                path = raw.split(":", 1)[-1].strip().strip('"')
                if not path:
                    messagebox.showerror("参数错误", f"无效 EXE 路径: {raw}")
                    return
                exe_paths.append(path)
            else:
                if (
                    re.match(r"^[A-Za-z]:[\\/]", raw)
                    or raw.startswith("\\\\")
                    or raw.startswith("/")
                ):
                    exe_paths.append(raw.strip().strip('"'))
                else:
                    processes.append(raw)
        if not processes and not pids and not exe_paths:
            self.status_var.set("监控系统全局 CPU 与内存")
        else:
            target_text = ", ".join(
                processes
                + [f"EXE:{path}" for path in exe_paths]
                + [f"PID:{pid}" for pid in pids]
            )
            self.status_var.set(
                f"监控目标: {target_text} | 数据目录: {self.data_dir}"
            )

        include_children = bool(self.children_var.get())
        self._stop_event = threading.Event()
        self._sample_count = 0
        self._worker_thread = threading.Thread(
            target=self._monitor_worker,
            args=(processes, exe_paths, pids, interval, include_children),
            name="watchdog-collector",
            daemon=True,
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.children_check.configure(state="disabled")
        self.live_var.set("正在启动采集...")
        self._worker_thread.start()

    def _monitor_worker(
        self,
        processes: list[str],
        exe_paths: list[str],
        pids: list[int],
        interval: float,
        include_children: bool,
    ) -> None:
        config = MonitorConfig(
            process_patterns=tuple(processes),
            exe_paths=tuple(exe_paths),
            pids=tuple(pids),
            interval_sec=interval,
            data_dir=self.data_dir,
            fsync_every_rows=self.fsync_every_rows,
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

            condition_monitor = None
            notifier = DingTalkNotifier(self._dingtalk or AppSettings().dingtalk)
            if self._end_condition is not None and self._end_condition.enabled:
                condition_monitor = EndConditionMonitor(
                    self._end_condition,
                    has_process_targets=config.has_process_targets,
                )
            target = ", ".join(
                processes
                + [f"EXE:{path}" for path in exe_paths]
                + [f"PID:{pid}" for pid in pids]
            ) or "系统全局"

            def on_sample(now, rows):
                self._event_queue.put(("sample", now, rows))
                if condition_monitor is None:
                    return
                alert = condition_monitor.record(now, rows)
                if alert is None:
                    return
                sent = notifier.send_alert(
                    alert,
                    target,
                    store.current_path,
                )
                self._event_queue.put(("alert", alert.condition_text, sent))
                if alert.stop_requested:
                    self._stop_event.set()

            monitor.run_until_stopped(self._stop_event, on_sample=on_sample)
            self._event_queue.put(("stopped", "监控已停止"))
        except Exception as exc:
            self._event_queue.put(("error", f"{type(exc).__name__}: {exc}"))

    def _stop_monitor(self) -> None:
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=8)
            self._worker_thread = None
        self.live_var.set("监控已停止")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.children_check.configure(state="normal")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                kind = event[0]
                if kind == "started":
                    path = event[1]
                    self.status_var.set(f"正在写入: {path}")
                elif kind == "sample":
                    _, now, rows = event
                    self._sample_count += len(rows)
                    self.count_var.set(f"样本 {self._sample_count}")
                    ok_rows = [
                        row
                        for row in rows
                        if len(row) >= 6 and row[4] == STATUS_OK
                    ]
                    system_rows = [
                        row
                        for row in ok_rows
                        if str(row[0]).upper() == "SYSTEM"
                        and str(row[1]) == "-1"
                    ]
                    target_rows = [row for row in ok_rows if row not in system_rows]
                    system_cpu = [
                        float(row[2])
                        for row in system_rows
                        if str(row[2]).strip() and str(row[2]) != "None"
                    ]
                    target_cpu = [
                        float(row[2])
                        for row in target_rows
                        if str(row[2]).strip() and str(row[2]) != "None"
                    ]
                    target_memory = [
                        float(row[3])
                        for row in target_rows
                        if str(row[3]).strip() and str(row[3]) != "None"
                    ]
                    system_memory = [
                        float(row[3])
                        for row in system_rows
                        if str(row[3]).strip() and str(row[3]) != "None"
                    ]
                    if system_cpu:
                        total_cpu = max(system_cpu)
                    elif target_cpu:
                        total_cpu = machine_cpu_percent(sum(target_cpu))
                    else:
                        total_cpu = None
                    memory_values = target_memory or system_memory
                    text = f"{now:%H:%M:%S}  样本 {len(rows)} 条"
                    if total_cpu is not None:
                        text += f" | 总CPU {total_cpu:.1f}%"
                    if memory_values:
                        text += f" | 内存峰值 {max(memory_values):.1f} MB"
                    self.live_var.set(text)
                elif kind == "alert":
                    condition_text = event[1]
                    sent = bool(event[2])
                    self.live_var.set("结束条件已触发，准备停止")
                    self.status_var.set(
                        f"触发条件: {condition_text} | 钉钉通知: "
                        f"{'已发送' if sent else '未启用或失败'}"
                    )
                elif kind == "stopped":
                    self.live_var.set(event[1])
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.children_check.configure(state="normal")
                    self._worker_thread = None
                elif kind == "error":
                    self.live_var.set("采集异常")
                    self.status_var.set(event[1])
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.children_check.configure(state="normal")
                    messagebox.showerror("采集异常", event[1])
                    self._worker_thread = None
                elif kind == "history_ready":
                    html_path = Path(event[1])
                    records_count = int(event[2])
                    files_count = int(event[3])
                    self._last_html_path = html_path
                    self._records_count = records_count
                    self.export_button.configure(state="normal")
                    self.load_history_button.configure(state="normal")
                    self.status_var.set(
                        f"HTML 报告已生成: {html_path} | {files_count} 个文件 / "
                        f"{records_count} 条样本"
                    )
                    webbrowser.open(html_path.as_uri())
                    self._history_thread = None
                elif kind == "history_error":
                    self.load_history_button.configure(state="normal")
                    if self._last_records:
                        self.export_button.configure(state="normal")
                    self.status_var.set(f"历史分析失败: {event[1]}")
                    messagebox.showerror("历史分析失败", event[1])
                    self._history_thread = None
                elif kind == "export_ready":
                    self.export_button.configure(state="normal")
                    self.load_history_button.configure(state="normal")
                    self.status_var.set(f"图表已导出: {event[1]}")
                    self._history_thread = None
                elif kind == "export_error":
                    self.export_button.configure(state="normal")
                    self.load_history_button.configure(state="normal")
                    self.status_var.set(f"导出失败: {event[1]}")
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
            filetypes=[("CSV 数据文件", "*.csv"), ("全部文件", "*.*")],
        )
        if not selected:
            return
        if self._history_thread is not None and self._history_thread.is_alive():
            messagebox.showinfo("正在处理", "已有历史分析任务在后台运行，请稍候")
            return
        self.load_history_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.status_var.set(
            f"正在后台解析 {len(selected)} 个文件并生成 HTML，监控不受影响..."
        )
        self._history_thread = threading.Thread(
            target=self._history_worker,
            args=(tuple(selected),),
            name="watchdog-history",
            daemon=True,
        )
        self._history_thread.start()

    def _history_worker(self, selected: tuple[str, ...]) -> None:
        try:
            from .plotting import load_files, write_html_report

            records, files = load_files(selected)
            if not records:
                raise ValueError("所选文件中没有可解析的有效记录")
            title = " / ".join(path.name for path in files[:3])
            if len(files) > 3:
                title += f" 等 {len(files)} 个文件"
            report_dir = self.data_dir / "reports"
            output = report_dir / f"report_{datetime.now():%Y%m%d_%H%M%S}.html"
            write_html_report(records, output, title=title)
            self._last_records = records
            self._last_html_path = output
            self._event_queue.put(
                ("history_ready", str(output), len(records), len(files))
            )
        except Exception as exc:
            self._event_queue.put(
                ("history_error", f"{type(exc).__name__}: {exc}")
            )

    def _export_figure(self) -> None:
        if not self._last_records:
            messagebox.showinfo("没有历史数据", "请先加载 CSV 生成 HTML 报告")
            return
        if self._history_thread is not None and self._history_thread.is_alive():
            messagebox.showinfo("正在处理", "已有后台分析/导出任务在运行")
            return
        path = filedialog.asksaveasfilename(
            title="导出图表",
            defaultextension=".html",
            filetypes=[
                ("交互 HTML", "*.html"),
                ("PNG 图片", "*.png"),
                ("SVG 矢量图", "*.svg"),
                ("PDF 文档", "*.pdf"),
            ],
        )
        if not path:
            return
        self.export_button.configure(state="disabled")
        self.status_var.set(f"正在后台导出: {path}")
        self._history_thread = threading.Thread(
            target=self._export_worker,
            args=(path,),
            name="watchdog-export",
            daemon=True,
        )
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
            self._event_queue.put(
                ("export_error", f"{type(exc).__name__}: {exc}")
            )

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
        if self._history_thread is not None and self._history_thread.is_alive():
            self._history_thread.join(timeout=2)
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
