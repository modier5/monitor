"""Command line entry points."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__
from .alerting import DingTalkNotifier, EndConditionMonitor
from .config import AppSettings, load_settings, write_default_config
from .monitor import MonitorConfig, PerfMonitor
from .storage import CsvStore, DiskFullError

log = logging.getLogger("watchdog_perf")


def _add_monitor_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="JSON 配置文件路径；缺省时自动读取当前目录 config.json",
    )
    parser.add_argument(
        "-p",
        "--process",
        dest="processes",
        action="append",
        metavar="NAME",
        help="根进程名/通配符（默认连同子进程一起监控），可多次指定",
    )
    parser.add_argument(
        "--exe",
        dest="exe_paths",
        action="append",
        metavar="PATH",
        help="按完整 exe 路径监控（进程后续启动也会被发现），可多次指定",
    )
    parser.add_argument(
        "--pid",
        dest="pids",
        type=int,
        action="append",
        metavar="PID",
        help="按根 PID 监控，可多次指定",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=None,
        metavar="SEC",
        help="采样间隔秒数；缺省使用配置文件或 1.0",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="只监控匹配到的进程本身，不递归子进程",
    )
    parser.add_argument(
        "-d",
        "--data-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="CSV 输出目录；缺省使用配置",
    )
    parser.add_argument(
        "--disk-limit-mb",
        type=int,
        default=None,
        help="磁盘剩余空间阈值；缺省使用配置或 100",
    )
    parser.add_argument(
        "--fsync-every",
        type=int,
        default=None,
        help="每多少行执行一次 os.fsync；缺省使用配置或 1",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchdog-perf",
        description="Watchdog-Perf：进程树/系统长时监控，逐行落盘 CSV，支持持续条件与钉钉通知。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    monitor = sub.add_parser(
        "monitor",
        help="启动监控（默认 GUI；加 --headless 后台运行）",
    )
    _add_monitor_options(monitor)
    monitor.add_argument(
        "--headless",
        action="store_true",
        help="无界面后台运行；未指定目标时监控系统全局",
    )
    monitor.add_argument("--log-level", default="INFO")

    gui = sub.add_parser("gui", help="打开图形界面")
    _add_monitor_options(gui)
    gui.add_argument("--log-level", default="INFO")

    plot = sub.add_parser(
        "plot",
        help="加载 CSV 生成交互 HTML（默认），或导出 PNG/SVG/PDF",
    )
    plot.add_argument(
        "targets",
        nargs="+",
        metavar="FILE|DATE|DIR",
        help="CSV 文件、YYYY-MM-DD 日期、目录或通配符",
    )
    plot.add_argument("--data-dir", type=Path, default=Path("data"))
    plot.add_argument(
        "--save",
        metavar="OUT",
        help="输出 .html 为交互报告；.png/.svg/.pdf 为静态图",
    )

    repair = sub.add_parser(
        "repair",
        help="读取含不完整时间戳的 CSV，重建完整时间并输出新文件",
    )
    repair.add_argument(
        "targets",
        nargs="+",
        metavar="FILE",
        help="输入 CSV 文件路径（可多个）",
    )
    repair.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="OUT",
        help="输出 CSV 路径；缺省为输入同目录 <原名>_repaired.csv",
    )

    init = sub.add_parser(
        "init-config",
        help="生成 config.json 示例配置文件",
    )
    init.add_argument("--path", type=Path, default=Path("config.json"))
    return parser


def setup_logging(data_dir: Path, level: str = "INFO") -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers):
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        root.addHandler(stream)
    file_handler = logging.FileHandler(
        data_dir / "monitor.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _apply_cli_overrides(args: argparse.Namespace, settings: AppSettings) -> None:
    # Command line targets replace the config's target lists instead of merging,
    # because an accidental config example would otherwise pollute the run.
    explicit_targets = any(
        value is not None
        for value in (args.processes, args.pids, args.exe_paths)
    )
    if explicit_targets:
        settings.processes = tuple(args.processes or ())
        settings.pids = tuple(args.pids or ())
        settings.exe_paths = tuple(args.exe_paths or ())
        settings.apps = ()
    if args.interval is not None:
        settings.interval_sec = float(args.interval)
    if args.flat:
        settings.include_children = False
    if explicit_targets:
        if not args.flat:
            settings.mode = "tree"
    if args.data_dir is not None:
        settings.data_dir = Path(args.data_dir)
    if args.disk_limit_mb is not None:
        settings.disk_limit_mb = int(args.disk_limit_mb)
    if args.fsync_every is not None:
        settings.fsync_every_rows = int(args.fsync_every)


def target_text(settings: AppSettings) -> str:
    parts: list[str] = []
    if settings.apps:
        for app in settings.apps:
            roots = ", ".join(app.roots)
            if app.name:
                parts.append(f"{app.name}({roots})")
            else:
                parts.append(roots)
    else:
        named = ", ".join(settings.processes)
        if named:
            parts.append(named)
    for path in settings.exe_paths:
        parts.append(f"EXE:{path}")
    pid_text = ", ".join(f"PID:{pid}" for pid in settings.pids)
    if pid_text:
        parts.append(pid_text)
    return " / ".join(parts)


def build_monitor_config(settings: AppSettings) -> MonitorConfig:
    patterns = settings.processes
    exe_paths = settings.exe_paths
    pids = settings.pids
    include_children = settings.include_children
    if settings.mode == "system":
        patterns = ()
        exe_paths = ()
        pids = ()
    elif settings.mode == "pid":
        patterns = ()
        exe_paths = ()
    elif settings.mode == "name":
        include_children = False
    return MonitorConfig(
        process_patterns=patterns,
        exe_paths=exe_paths,
        pids=pids,
        interval_sec=settings.interval_sec,
        data_dir=settings.data_dir,
        disk_limit_mb=settings.disk_limit_mb,
        fsync_every_rows=settings.fsync_every_rows,
        self_status_interval_sec=settings.self_status_interval_sec,
        include_children=include_children,
    )


def run_headless(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    _apply_cli_overrides(args, settings)
    data_dir = Path(settings.data_dir)
    setup_logging(data_dir, args.log_level)
    if settings.config_path is not None:
        log.info("配置文件: %s", settings.config_path)
    log.info(
        "监控对象: %s | 递归子进程: %s",
        target_text(settings) or "系统全局",
        settings.include_children,
    )
    log.info(
        "采样间隔 %.2fs，数据目录 %s，fsync 每 %d 行，备份每 %d 行保留 %d 份",
        settings.interval_sec,
        data_dir,
        settings.fsync_every_rows,
        settings.backup_every_rows,
        settings.backup_keep,
    )

    monitor_config = build_monitor_config(settings)
    condition_monitor = (
        EndConditionMonitor(
            settings.end_condition,
            has_process_targets=monitor_config.has_process_targets,
        )
        if settings.end_condition.enabled
        else None
    )
    notifier = DingTalkNotifier(settings.dingtalk)
    stop_event = threading.Event()

    def on_sample(now, rows):
        if condition_monitor is None:
            return
        alert = condition_monitor.record(now, rows)
        if alert is None:
            return
        data_file = None
        try:
            data_file = store.current_path
        except UnboundLocalError:
            pass
        sent = notifier.send_alert(
            alert,
            target_text(settings) or "系统全局",
            data_file,
        )
        log.info(
            "结束条件触发: %s | 钉钉发送: %s | 停止采集: %s",
            alert.condition_text,
            sent,
            alert.stop_requested,
        )
        if alert.stop_requested:
            stop_event.set()

    try:
        with CsvStore(
            data_dir=data_dir,
            disk_limit_mb=settings.disk_limit_mb,
            fsync_every_rows=settings.fsync_every_rows,
            backup_every_rows=settings.backup_every_rows,
            backup_keep=settings.backup_keep,
        ) as store:
            PerfMonitor(monitor_config, store).run_until_stopped(
                stop_event,
                on_sample=on_sample,
            )
    except DiskFullError as exc:
        log.error("因磁盘空间不足停止: %s", exc)
        return 3
    except KeyboardInterrupt:
        log.info("收到中断信号，已安全关闭数据文件")
    return 0


def run_gui_command(args: argparse.Namespace) -> int:
    try:
        from .gui import run_gui
    except Exception as exc:
        print(f"无法启动图形界面: {exc}", file=sys.stderr)
        return 2
    settings = load_settings(args.config)
    _apply_cli_overrides(args, settings)
    setup_logging(settings.data_dir, args.log_level)
    default_processes = list(settings.processes)
    default_processes.extend(f"PID:{pid}" for pid in settings.pids)
    default_processes.extend(f"EXE:{path}" for path in settings.exe_paths)
    run_gui(
        default_processes=tuple(default_processes),
        default_interval_sec=settings.interval_sec,
        data_dir=settings.data_dir,
        disk_limit_mb=settings.disk_limit_mb,
        fsync_every_rows=settings.fsync_every_rows,
        backup_every_rows=settings.backup_every_rows,
        backup_keep=settings.backup_keep,
        app_settings=settings,
    )
    return 0


def run_plot(args: argparse.Namespace) -> int:
    from .plotting import load_files, resolve_input_paths

    paths = resolve_input_paths(args.targets, args.data_dir)
    if not paths:
        print(
            "没有找到 CSV 文件。可传文件路径、目录、YYYY-MM-DD 日期或通配符。",
            file=sys.stderr,
        )
        return 2
    records, files = load_files(paths)
    if not records:
        print("CSV 中没有可绘图的有效记录", file=sys.stderr)
        return 2
    title = " / ".join(path.name for path in files[:3])
    if len(files) > 3:
        title += f" 等 {len(files)} 个文件"

    if args.save:
        save_target = Path(args.save)
        if save_target.suffix.lower() == ".html":
            from .plotting import write_html_report

            out = write_html_report(records, save_target, title=title)
        else:
            import matplotlib.pyplot as plt

            from .plotting import make_figure, save_figure

            figure = make_figure(records, title=title)
            try:
                out = save_figure(figure, save_target)
            finally:
                plt.close(figure)
        print(f"图表已保存: {out}")
    else:
        report_dir = Path(args.data_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        out = report_dir / f"report_{datetime.now():%Y%m%d_%H%M%S}.html"
        from .plotting import write_html_report

        write_html_report(records, out, title=title)
        print(f"交互 HTML 已生成: {out}")
        webbrowser.open(out.as_uri())
    return 0


def run_repair(args: argparse.Namespace) -> int:
    from .plotting import load_csv_file, write_csv_file

    for item in args.targets:
        path = Path(item)
        records = load_csv_file(path)
        if not records:
            print(f"{path}: 没有可修复的有效记录", file=sys.stderr)
            return 2
        out = args.output or path.with_name(f"{path.stem}_repaired.csv")
        write_csv_file(records, out)
        print(f"已修复并输出: {out} ({len(records)} 行)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "monitor":
        if args.headless:
            return run_headless(args)
        return run_gui_command(args)
    if args.command == "gui":
        return run_gui_command(args)
    if args.command == "plot":
        return run_plot(args)
    if args.command == "repair":
        return run_repair(args)
    if args.command == "init-config":
        out = write_default_config(args.path)
        print(f"已生成配置文件: {out}")
        return 0
    parser.print_help()
    return 2
