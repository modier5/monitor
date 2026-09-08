"""CSV loading and interactive matplotlib charts."""

from __future__ import annotations

import csv
import glob
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from .metrics import machine_cpu_percent
from .storage import daily_filename

try:
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "sans-serif",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

TIMESTAMP_KEY = "timestamp"
NAME_KEY = "process_name"
PID_KEY = "pid"
CPU_KEY = "cpu_percent"
MEMORY_KEY = "memory_mb"
STATUS_KEY = "status"
EXTRA_KEY = "extra"

_COLUMN_ALIASES = {
    "timestamp": TIMESTAMP_KEY,
    "time": TIMESTAMP_KEY,
    "datetime": TIMESTAMP_KEY,
    "ts": TIMESTAMP_KEY,
    "record_time": TIMESTAMP_KEY,
    "processname": NAME_KEY,
    "process_name": NAME_KEY,
    "process": NAME_KEY,
    "name": NAME_KEY,
    "pid": PID_KEY,
    "processid": PID_KEY,
    "process_id": PID_KEY,
    "cpupercent": CPU_KEY,
    "cpu_percent": CPU_KEY,
    "cpu": CPU_KEY,
    "cpu_usage": CPU_KEY,
    "memorymb": MEMORY_KEY,
    "memory_mb": MEMORY_KEY,
    "memory": MEMORY_KEY,
    "mem": MEMORY_KEY,
    "rss_mb": MEMORY_KEY,
    "memory_usage_mb": MEMORY_KEY,
    "workingset_mb": MEMORY_KEY,
    "working_set_mb": MEMORY_KEY,
    "status": STATUS_KEY,
    "extra": EXTRA_KEY,
    "extrainfo": EXTRA_KEY,
    "extra_info": EXTRA_KEY,
    "note": EXTRA_KEY,
    "remark": EXTRA_KEY,
}


def _parse_timestamp(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    formats = (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S.%f",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_extra_pairs(extra: str) -> dict[str, float]:
    pairs: dict[str, float] = {}
    for part in str(extra or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            pairs[key.strip()] = float(value.strip())
        except ValueError:
            continue
    return pairs


def _parse_extra_text(extra: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in str(extra or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _parse_int(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _normalize_header(raw_columns: Iterable[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for index, raw in enumerate(raw_columns):
        key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
        key = key.replace("%", "").replace("(", "").replace(")", "")
        mapping[index] = _COLUMN_ALIASES.get(key, "")
    return mapping


def load_csv_file(path: str | Path) -> list[dict]:
    """Load a Watchdog CSV into canonical dictionaries; tolerant of renamed headers."""
    with open(path, newline="", encoding="utf-8-sig") as fp:
        reader = csv.reader(fp)
        raw_header = next(reader, None)
        if not raw_header:
            return []
        columns = _normalize_header(raw_header)
        if not any(columns.values()):
            raise ValueError(f"{path}: 无法识别表头")

        records: list[dict] = []
        for raw_row in reader:
            if not raw_row or all(not cell.strip() for cell in raw_row):
                continue
            record: dict[str, object] = {}
            for index, key in columns.items():
                if key:
                    record[key] = raw_row[index] if index < len(raw_row) else ""
            records.append(record)

    parsed: list[dict] = []
    for record in records:
        dt = _parse_timestamp(str(record.get(TIMESTAMP_KEY, "")))
        if dt is None:
            continue
        parsed.append(
            {
                TIMESTAMP_KEY: dt,
                NAME_KEY: str(record.get(NAME_KEY, "") or "unknown"),
                PID_KEY: _parse_int(record.get(PID_KEY)) or -1,
                CPU_KEY: _parse_float(record.get(CPU_KEY)),
                MEMORY_KEY: _parse_float(record.get(MEMORY_KEY)),
                STATUS_KEY: _parse_int(record.get(STATUS_KEY)) or 0,
                EXTRA_KEY: str(record.get(EXTRA_KEY, "") or ""),
            }
        )
    parsed.sort(key=lambda rec: rec[TIMESTAMP_KEY])
    return parsed


def resolve_input_paths(
    items: Iterable[str],
    data_dir: str | Path = "data",
) -> list[Path]:
    """Resolve explicit files, directories, dates, or glob expressions."""
    resolved: list[Path] = []
    data_path = Path(data_dir)
    for item in items:
        item = item.strip()
        if not item:
            continue
        direct = Path(item)
        if direct.is_file():
            resolved.append(direct)
            continue
        if direct.is_dir():
            resolved.extend(sorted(p for p in direct.glob("Monitor_*.csv")))
            continue
        try:
            day = date.fromisoformat(item)
            candidate = data_path / daily_filename(day)
            if candidate.is_file():
                resolved.append(candidate)
            else:
                resolved.extend(sorted(data_path.glob(f"Monitor_{day.isoformat()}*.csv")))
            continue
        except ValueError:
            pass
        for base in (Path(item), data_path / item):
            expanded = sorted(Path(found) for found in glob.glob(str(base)))
            resolved.extend(expanded)

    unique: list[Path] = []
    for path in sorted(set(resolved)):
        if path.is_file() and path.suffix.lower() == ".csv":
            unique.append(path)
    return unique


def load_files(paths: Iterable[str | Path]) -> tuple[list[dict], list[Path]]:
    files = [Path(p) for p in paths]
    records: list[dict] = []
    for path in files:
        records.extend(load_csv_file(path))
    records.sort(key=lambda rec: rec[TIMESTAMP_KEY])
    return records, files


def _p90(values: list[float]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(math.ceil(len(ordered) * 0.9)) - 1)
    return ordered[index]


def _is_system_row(row: dict) -> bool:
    return (
        str(row.get(NAME_KEY, "")).upper() == "SYSTEM"
        and _parse_int(row.get(PID_KEY)) == -1
    )


def _infer_cpu_count(records: Iterable[dict]) -> int:
    for row in records:
        extra = _parse_extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
        for key in ("cpu_cores", "cpu_count", "cores"):
            value = extra.get(key)
            if value is not None and value > 0:
                return int(value)
    return os.cpu_count() or 1


def _aggregate_target_series(
    records: Iterable[dict],
    cpu_count: int,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Aggregate all monitored processes into machine-wide equivalent lines."""
    buckets: dict[datetime, list[float | int]] = {}
    for row in records:
        if int(row[STATUS_KEY]) != 0 or _is_system_row(row):
            continue
        timestamp = row[TIMESTAMP_KEY]
        if timestamp is None:
            continue
        bucket = buckets.setdefault(timestamp, [0.0, 0.0, 0, 0])
        cpu = _parse_float(row.get(CPU_KEY))
        memory = _parse_float(row.get(MEMORY_KEY))
        if cpu is not None:
            bucket[0] += float(cpu)
            bucket[2] += 1
        if memory is not None:
            bucket[1] += float(memory)
            bucket[3] += 1

    cpu_points: list[tuple[float, float]] = []
    memory_points: list[tuple[float, float]] = []
    for timestamp in sorted(buckets):
        raw_cpu, memory_mb, cpu_rows, memory_rows = buckets[timestamp]
        ts = mdates.date2num(timestamp)
        if cpu_rows:
            machine_cpu = machine_cpu_percent(raw_cpu, cpu_count)
            if machine_cpu is not None:
                cpu_points.append((ts, machine_cpu))
        if memory_rows:
            memory_points.append((ts, memory_mb))
    return cpu_points, memory_points


def _downsample(points: list[tuple[float, float]], limit: int = 30000):
    if len(points) <= limit:
        return points
    step = max(1, math.ceil(len(points) / limit))
    return points[::step]


def make_figure(records: Iterable[dict], title: str | None = None) -> Figure:
    """Build a dual-axis figure. Left: CPU%; right: memory MB."""
    rows = sorted(records, key=lambda rec: rec[TIMESTAMP_KEY])
    if not rows:
        raise ValueError("没有可绘图的数据")

    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    exit_events: list[datetime] = []
    for row in rows:
        key = (str(row[NAME_KEY]), int(row[PID_KEY]))
        groups[key].append(row)
        if int(row[STATUS_KEY]) == 1 and row[TIMESTAMP_KEY] is not None:
            exit_events.append(row[TIMESTAMP_KEY])

    fig, ax_cpu = plt.subplots(figsize=(12, 6.2), dpi=110)
    ax_mem = ax_cpu.twinx()
    fig.subplots_adjust(top=0.84, right=0.86)

    start_dt: datetime = rows[0][TIMESTAMP_KEY]
    end_dt: datetime = rows[-1][TIMESTAMP_KEY]
    duration = end_dt - start_dt
    if duration <= timedelta(hours=2):
        date_fmt = "%H:%M:%S"
    else:
        date_fmt = "%m-%d %H:%M"

    colors = plt.get_cmap("tab20").colors
    series_text: list[str] = []
    process_handles: list[Line2D] = []
    total_line_added = False
    cpu_count = _infer_cpu_count(rows)
    has_system_line = any(
        _is_system_row(row)
        and int(row[STATUS_KEY]) == 0
        and row[CPU_KEY] is not None
        for row in rows
    )

    for index, (key, group) in enumerate(groups.items()):
        name, pid = key
        color = colors[index % len(colors)]
        group.sort(key=lambda rec: rec[TIMESTAMP_KEY])
        cpu_points: list[tuple[float, float]] = []
        memory_points: list[tuple[float, float]] = []
        cpu_values: list[float] = []
        memory_values: list[float] = []
        memory_total: float | None = None

        for row in group:
            if int(row[STATUS_KEY]) != 0:
                continue
            ts = mdates.date2num(row[TIMESTAMP_KEY])
            cpu = row[CPU_KEY]
            memory = row[MEMORY_KEY]
            if cpu is not None:
                if not _is_system_row(row):
                    cpu_percent = machine_cpu_percent(
                        float(cpu),
                        cpu_count,
                    )
                    if cpu_percent is None:
                        cpu_percent = float(cpu)
                else:
                    cpu_percent = float(cpu)
                cpu_values.append(cpu_percent)
                cpu_points.append((ts, cpu_percent))
            if memory is not None:
                memory_values.append(float(memory))
                memory_points.append((ts, float(memory)))
            extra = _parse_extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
            if "memory_total_mb" in extra:
                memory_total = extra["memory_total_mb"]

        if name.upper() == "SYSTEM" and pid == -1:
            label = "整机占用"
        else:
            label = f"{name} [{pid}]"
        extra_values = _parse_extra_text(str(group[0].get(EXTRA_KEY, "") or ""))
        root_pid = _parse_int(extra_values.get("root_pid"))
        root_name = extra_values.get("root_name", "")
        depth = _parse_int(extra_values.get("depth")) or 0
        if root_pid is not None and root_pid != pid:
            label += f" <- {root_name or name}[{root_pid}] d{depth}"
        if cpu_points:
            points = _downsample(cpu_points)
            ax_cpu.plot(
                [p[0] for p in points],
                [p[1] for p in points],
                color=color,
                linewidth=1.0,
                label=label,
            )
        if memory_points:
            points = _downsample(memory_points)
            ax_mem.plot(
                [p[0] for p in points],
                [p[1] for p in points],
                color=color,
                linewidth=1.0,
                linestyle="--",
                alpha=0.85,
            )

        if cpu_values or memory_values:
            stats_parts: list[str] = []
            if cpu_values:
                average = sum(cpu_values) / len(cpu_values)
                stats_parts.append(f"CPU均值 {average:.1f}%")
                stats_parts.append(f"CPU P90 {_p90(cpu_values):.1f}%")
            if memory_values:
                stats_parts.append(f"内存峰值 {max(memory_values):.1f} MB")
            if memory_total is not None:
                stats_parts.append(f"内存总量 {memory_total:.0f} MB")
            if stats_parts:
                stats_line = f"{label}: {' / '.join(stats_parts)}"
                if label == "整机占用":
                    series_text.insert(0, stats_line)
                else:
                    series_text.append(stats_line)
                process_handles.append(
                    Line2D([], [], color=color, linewidth=1.2, label=label)
                )

        if (
            memory_total is not None
            and memory_points
            and not total_line_added
        ):
            first_x = memory_points[0][0]
            last_x = memory_points[-1][0]
            ax_mem.plot(
                [first_x, last_x],
                [memory_total, memory_total],
                color="#8c564b",
                linestyle="-.",
                linewidth=1.0,
                alpha=0.7,
            )
            total_line_added = True

    if not has_system_line:
        cpu_count = _infer_cpu_count(rows)
        total_cpu_points, total_memory_points = _aggregate_target_series(
            rows,
            cpu_count,
        )
        if total_cpu_points or total_memory_points:
            label = "监控目标合计 (整机当量)"
            if total_cpu_points:
                points = _downsample(total_cpu_points)
                ax_cpu.plot(
                    [p[0] for p in points],
                    [p[1] for p in points],
                    color="#111111",
                    linewidth=1.5,
                    alpha=0.9,
                    label=label,
                )
            if total_memory_points:
                points = _downsample(total_memory_points)
                ax_mem.plot(
                    [p[0] for p in points],
                    [p[1] for p in points],
                    color="#111111",
                    linewidth=1.5,
                    linestyle="--",
                    alpha=0.9,
                )
            stats_parts = []
            if total_cpu_points:
                values = [point[1] for point in total_cpu_points]
                stats_parts.append(f"CPU均值 {sum(values) / len(values):.1f}%")
                stats_parts.append(f"CPU P90 {_p90(values):.1f}%")
            if total_memory_points:
                values = [point[1] for point in total_memory_points]
                stats_parts.append(f"内存峰值 {max(values):.1f} MB")
            if stats_parts:
                series_text.insert(0, f"{label}: {' / '.join(stats_parts)}")
                process_handles.append(
                    Line2D([], [], color="#111111", linewidth=1.5, label=label)
                )

    if process_handles:
        process_handles.append(
            Line2D([], [], color="#555555", linewidth=1.0, label="CPU (左轴)")
        )
        process_handles.append(
            Line2D(
                [],
                [],
                color="#555555",
                linewidth=1.0,
                linestyle="--",
                label="内存 (右轴)",
            )
        )
        if total_line_added:
            process_handles.append(
                Line2D(
                    [],
                    [],
                    color="#8c564b",
                    linestyle="-.",
                    linewidth=1.0,
                    label="内存总量 (右轴)",
                )
            )
        ax_cpu.legend(
            handles=process_handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.11),
            ncol=min(3, max(2, len(process_handles))),
            fontsize=8,
            frameon=False,
        )

    for event_dt in exit_events:
        ax_cpu.axvline(
            mdates.date2num(event_dt),
            color="#d62728",
            linestyle=":",
            linewidth=0.9,
            alpha=0.65,
            zorder=0,
        )

    if series_text:
        stats_block = "\n".join(series_text[:8])
        if len(series_text) > 8:
            stats_block += f"\n... 共 {len(series_text)} 个序列"
        ax_cpu.text(
            0.005,
            0.985,
            stats_block,
            transform=ax_cpu.transAxes,
            verticalalignment="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.78, "pad": 4},
            zorder=5,
        )

    ax_cpu.set_xlabel("时间")
    ax_cpu.set_ylabel("CPU 占用率 (%)")
    ax_mem.set_ylabel("内存 (MB)")
    ax_cpu.set_zorder(ax_mem.get_zorder() + 1)
    ax_cpu.patch.set_visible(False)
    ax_cpu.grid(True, linestyle=":", alpha=0.35)
    ax_cpu.set_ylim(0, 100)

    locator = mdates.AutoDateLocator(minticks=4, maxticks=12)
    formatter = mdates.DateFormatter(date_fmt)
    ax_cpu.xaxis.set_major_locator(locator)
    ax_cpu.xaxis.set_major_formatter(formatter)
    fig.autofmt_xdate(rotation=30)

    heading = title or f"进程性能回溯 · {len(groups)} 个进程序列"
    fig.suptitle(
        f"{heading}\n样本数 {len(rows)} | 起止 {start_dt:%Y-%m-%d %H:%M:%S} ~ {end_dt:%Y-%m-%d %H:%M:%S}",
        fontsize=10,
        fontweight="bold",
    )
    return fig


def save_figure(fig: Figure, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, bbox_inches="tight")
    return out
