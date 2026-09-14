"""CSV loading, ECharts HTML reports and matplotlib exports."""

from __future__ import annotations

import csv
import glob
import logging
import math
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import psutil

log = logging.getLogger("watchdog_perf.plotting")

from .metrics import machine_cpu_percent
from .storage import COLUMNS, daily_filename, format_timestamp

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


_PATH_DATE_RE = re.compile(r"^Monitor_(\d{4}-\d{2}-\d{2})")
_PARTIAL_TS_RE = re.compile(r"^(\d{1,2}):(\d{1,2})\.(\d+)$")
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d+)")


def _path_date(path):
    match = _PATH_DATE_RE.search(Path(path).name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _parse_minsec(value):
    text = str(value or "").strip()
    match = _PARTIAL_TS_RE.match(text)
    if not match:
        return None
    minute = int(match.group(1))
    second = int(match.group(2))
    if minute > 59 or second > 59:
        return None
    millis = int((match.group(3) + "000")[:3])
    return minute, second, millis


def _log_anchor_for_partial(path, first):
    if first is None:
        return None
    log_path = Path(path).parent / "monitor.log"
    if not log_path.is_file():
        return None
    day = _path_date(path)
    try:
        with log_path.open(encoding="utf-8-sig", errors="replace") as fp:
            for line in fp:
                match = _LOG_TS_RE.match(line)
                if not match:
                    continue
                dt = datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                    int(match.group(6)),
                )
                if day is not None and dt.date() < day:
                    continue
                if dt.minute == first[0] and dt.second == first[1]:
                    return dt
    except OSError:
        return None
    return None


def _sibling_anchor_for_partial(path, first):
    if first is None:
        return None
    day = _path_date(path)
    if day is None:
        return None
    target = Path(path)
    for candidate in sorted(target.parent.glob(f"Monitor_{day.isoformat()}*.csv")):
        if candidate.resolve() == target.resolve():
            continue
        try:
            with candidate.open(newline="", encoding="utf-8-sig") as fp:
                reader = csv.reader(fp)
                next(reader, None)
                for row in reader:
                    if not row:
                        continue
                    dt = _parse_timestamp(str(row[0]).strip())
                    if dt is not None and dt.minute == first[0] and dt.second == first[1]:
                        return dt
        except (OSError, csv.Error):
            continue
    return None


def _partial_base(path, records):
    first = None
    for record in records:
        partial = _parse_minsec(record.get(TIMESTAMP_KEY))
        if partial is not None:
            first = partial
            break
    if first is None:
        return None
    anchor = _sibling_anchor_for_partial(path, first) or _log_anchor_for_partial(path, first)
    if anchor is not None:
        offset = first[0] * 60 + first[1] + first[2] / 1000
        return anchor - timedelta(seconds=offset)
    day = _path_date(path)
    if day is None:
        return None
    return datetime.combine(day, datetime.min.time())


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

    partial_base = _partial_base(path, records)
    parsed: list[dict] = []
    last_minsec: tuple[int, int] | None = None
    partial_hours = 0
    partial_used = False
    for record in records:
        dt = _parse_timestamp(str(record.get(TIMESTAMP_KEY, "")))
        if dt is None and partial_base is not None:
            partial = _parse_minsec(record.get(TIMESTAMP_KEY))
            if partial is not None:
                minute, second, millis = partial
                if last_minsec is not None and (minute, second) < last_minsec:
                    partial_hours += 1
                last_minsec = (minute, second)
                offset = partial_hours * 3600 + minute * 60 + second + millis / 1000
                dt = partial_base + timedelta(seconds=offset)
                partial_used = True
        if dt is None:
            continue
        if last_minsec is None and dt is not None:
            last_minsec = (dt.minute, dt.second)
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
    if partial_used:
        log.info("%s: 检测到不完整时间戳，已按基准 %s 重建", Path(path).name, partial_base)
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


def write_csv_file(records: Iterable[dict], path: str | Path) -> Path:
    """Write canonical Watchdog CSV rows (full timestamps) to a new file."""
    path = Path(path)

    def _text(value):
        if value is None or value == "":
            return ""
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp, lineterminator="\n")
        writer.writerow(COLUMNS)
        for record in records:
            ts = record.get(TIMESTAMP_KEY)
            stamp = format_timestamp(ts) if isinstance(ts, datetime) else str(ts or "")
            writer.writerow(
                [
                    stamp,
                    _text(record.get(NAME_KEY, "")),
                    _text(record.get(PID_KEY, -1)),
                    _text(record.get(CPU_KEY)),
                    _text(record.get(MEMORY_KEY)),
                    _text(record.get(STATUS_KEY, 0)),
                    _text(record.get(EXTRA_KEY, "")),
                ]
            )
    return path


def _is_system_row(row: dict) -> bool:
    return (
        str(row.get(NAME_KEY, "")).upper() == "SYSTEM"
        and _parse_int(row.get(PID_KEY)) == -1
    )


def _series_group_key(row: dict) -> tuple:
    if _is_system_row(row):
        return ("system", -1)
    name = str(row.get(NAME_KEY, "") or "unknown")
    extra = _parse_extra_text(str(row.get(EXTRA_KEY, "") or ""))
    exe_path = str(extra.get("exe_path", "") or "").strip().strip('"')
    if exe_path:
        return ("exe", os.path.normcase(os.path.normpath(exe_path)))
    return ("name", name.casefold())


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
    """Matplotlib variant of target aggregation (date2num x values)."""
    import matplotlib.dates as mdates

    (
        cpu_times,
        cpu_values,
        memory_times,
        memory_values,
    ) = _aggregate_target_values(records, cpu_count)
    return (
        [
            (mdates.date2num(timestamp), value)
            for timestamp, value in zip(cpu_times, cpu_values)
        ],
        [
            (mdates.date2num(timestamp), value)
            for timestamp, value in zip(memory_times, memory_values)
        ],
    )


def _aggregate_target_values(
    records: Iterable[dict],
    cpu_count: int,
) -> tuple[list[datetime], list[float], list[datetime], list[float]]:
    """Aggregate monitored processes into machine-wide equivalent lines."""
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

    cpu_times: list[datetime] = []
    cpu_values: list[float] = []
    memory_times: list[datetime] = []
    memory_values: list[float] = []
    for timestamp in sorted(buckets):
        raw_cpu, memory_mb, cpu_rows, memory_rows = buckets[timestamp]
        if cpu_rows:
            machine_cpu = machine_cpu_percent(raw_cpu, cpu_count)
            if machine_cpu is not None:
                cpu_times.append(timestamp)
                cpu_values.append(machine_cpu)
        if memory_rows:
            memory_times.append(timestamp)
            memory_values.append(memory_mb)
    return cpu_times, cpu_values, memory_times, memory_values


def _memory_axis_upper(records: Iterable[dict]) -> float:
    """Use machine physical memory (or CSV total) as the memory axis ceiling."""
    observed_max = 0.0
    recorded_total = 0.0
    for row in records:
        if int(row[STATUS_KEY]) != 0:
            continue
        memory = _parse_float(row.get(MEMORY_KEY))
        if memory is not None:
            observed_max = max(observed_max, float(memory))
        if _is_system_row(row):
            extra = _parse_extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
            if extra.get("memory_total_mb"):
                recorded_total = max(recorded_total, extra["memory_total_mb"])
    machine_total = psutil.virtual_memory().total / 1024 / 1024
    upper = recorded_total or machine_total
    return max(float(upper), observed_max)


def _downsample(points: list[tuple[float, float]], limit: int = 30000):
    if len(points) <= limit:
        return points
    step = max(1, math.ceil(len(points) / limit))
    return points[::step]


def _downsample_lists(
    times: list,
    values: list[float],
    limit: int = 30000,
) -> tuple[list, list[float]]:
    if len(times) <= limit:
        return times, values
    step = max(1, math.ceil(len(times) / limit))
    return times[::step], values[::step]


def make_figure(records: Iterable[dict], title: str | None = None) -> Figure:
    """Build a dual-axis figure. Left: CPU%; right: memory MB."""
    import matplotlib
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

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

    rows = sorted(records, key=lambda rec: rec[TIMESTAMP_KEY])
    if not rows:
        raise ValueError("没有可绘图的数据")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    exit_events: list[datetime] = []
    for row in rows:
        groups[_series_group_key(row)].append(row)
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
        color = colors[index % len(colors)]
        group.sort(key=lambda rec: rec[TIMESTAMP_KEY])
        cpu_points: list[tuple[float, float]] = []
        memory_points: list[tuple[float, float]] = []
        cpu_values: list[float] = []
        memory_values: list[float] = []
        memory_total: float | None = None
        cpu_buckets: dict[float, float] = {}
        mem_buckets: dict[float, float] = {}

        for row in group:
            if int(row[STATUS_KEY]) != 0:
                continue
            ts = mdates.date2num(row[TIMESTAMP_KEY])
            cpu = row[CPU_KEY]
            memory = row[MEMORY_KEY]
            if cpu is not None:
                cpu_buckets[ts] = cpu_buckets.get(ts, 0.0) + float(cpu)
            if memory is not None:
                mem_buckets[ts] = mem_buckets.get(ts, 0.0) + float(memory)
            extra = _parse_extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
            if "memory_total_mb" in extra:
                memory_total = extra["memory_total_mb"]

        is_system = key == ("system", -1)
        if is_system:
            label = "整机占用"
        else:
            label = str(group[0].get(NAME_KEY, "") or "unknown")

        for ts in sorted(cpu_buckets):
            if is_system:
                cpu_percent = min(100.0, max(0.0, cpu_buckets[ts]))
            else:
                cpu_percent = machine_cpu_percent(cpu_buckets[ts], cpu_count)
                if cpu_percent is None:
                    cpu_percent = 0.0
            cpu_values.append(cpu_percent)
            cpu_points.append((ts, cpu_percent))
        for ts in sorted(mem_buckets):
            memory_values.append(mem_buckets[ts])
            memory_points.append((ts, mem_buckets[ts]))

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
    ax_mem.set_ylim(0, _memory_axis_upper(rows))
    first_x = mdates.date2num(start_dt)
    last_x = mdates.date2num(end_dt)
    if last_x <= first_x:
        last_x = first_x + 0.001
    ax_cpu.set_xlim(first_x, last_x)

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


def make_plotly_figure(records: Iterable[dict], title: str | None = None):
    """Build an interactive stacked Plotly figure: CPU above, memory below."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise ImportError(
            "生成 HTML 报告需要 plotly，请先运行 pip install -r requirements.txt"
        ) from exc

    rows = sorted(records, key=lambda rec: rec[TIMESTAMP_KEY])
    if not rows:
        raise ValueError("没有可绘图的数据")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_series_group_key(row)].append(row)

    cpu_count = _infer_cpu_count(rows)
    has_system_line = any(
        _is_system_row(row)
        and int(row[STATUS_KEY]) == 0
        and row[CPU_KEY] is not None
        for row in rows
    )

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.10,
        row_heights=[0.58, 0.42],
        subplot_titles=("CPU 占用率 (%)", "内存占用 (MB)"),
    )
    palette = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
        "#aec7e8",
        "#ffbb78",
    ]

    def add_series(
        series_name: str,
        cpu_times: list,
        cpu_values: list[float],
        memory_times: list,
        memory_values: list[float],
        color: str,
        width: float = 1.4,
    ) -> None:
        cpu_times, cpu_values = _downsample_lists(cpu_times, cpu_values)
        memory_times, memory_values = _downsample_lists(memory_times, memory_values)
        if cpu_times:
            fig.add_trace(
                go.Scatter(
                    x=cpu_times,
                    y=cpu_values,
                    name=series_name,
                    legendgroup=series_name,
                    mode="lines",
                    line={"color": color, "width": width},
                    hovertemplate=(
                        f"{series_name}<br>%{{x|%Y-%m-%d %H:%M:%S}}<br>"
                        "CPU: %{y:.2f}%<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )
        if memory_times:
            fig.add_trace(
                go.Scatter(
                    x=memory_times,
                    y=memory_values,
                    name=series_name,
                    legendgroup=series_name,
                    mode="lines",
                    line={"color": color, "width": width},
                    showlegend=False,
                    hovertemplate=(
                        f"{series_name}<br>%{{x|%Y-%m-%d %H:%M:%S}}<br>"
                        "内存: %{y:.1f} MB<extra></extra>"
                    ),
                ),
                row=2,
                col=1,
            )

    ordered_keys = sorted(
        groups,
        key=lambda key: (
            0
            if key == ("system", -1)
            else 1,
            key,
        ),
    )
    for index, key in enumerate(ordered_keys):
        group = sorted(groups[key], key=lambda rec: rec[TIMESTAMP_KEY])
        is_system = key == ("system", -1)
        if is_system:
            series_name = "整机占用"
        else:
            series_name = str(group[0].get(NAME_KEY, "") or "unknown")

        cpu_times: list = []
        cpu_values: list[float] = []
        memory_times: list = []
        memory_values: list[float] = []
        cpu_buckets: dict[datetime, float] = {}
        mem_buckets: dict[datetime, float] = {}
        for row in group:
            if int(row[STATUS_KEY]) != 0:
                continue
            timestamp = row[TIMESTAMP_KEY]
            cpu = row[CPU_KEY]
            memory = row[MEMORY_KEY]
            if cpu is not None:
                cpu_buckets[timestamp] = cpu_buckets.get(timestamp, 0.0) + float(cpu)
            if memory is not None:
                mem_buckets[timestamp] = mem_buckets.get(timestamp, 0.0) + float(memory)

        for timestamp in sorted(cpu_buckets):
            if is_system:
                cpu_percent = min(100.0, max(0.0, cpu_buckets[timestamp]))
            else:
                cpu_percent = machine_cpu_percent(cpu_buckets[timestamp], cpu_count)
                if cpu_percent is None:
                    cpu_percent = 0.0
            cpu_times.append(timestamp)
            cpu_values.append(cpu_percent)
        for timestamp in sorted(mem_buckets):
            memory_times.append(timestamp)
            memory_values.append(mem_buckets[timestamp])

        color = palette[index % len(palette)]
        if is_system:
            color = "#111111"
        add_series(
            series_name,
            cpu_times,
            cpu_values,
            memory_times,
            memory_values,
            color,
            width=2.0 if is_system else 1.4,
        )

    memory_total: float | None = None
    for row in rows:
        if _is_system_row(row) and int(row[STATUS_KEY]) == 0:
            extra_pairs = _parse_extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
            if "memory_total_mb" in extra_pairs:
                memory_total = extra_pairs["memory_total_mb"]
                break

    if memory_total is not None:
        fig.add_trace(
            go.Scatter(
                x=[rows[0][TIMESTAMP_KEY], rows[-1][TIMESTAMP_KEY]],
                y=[memory_total, memory_total],
                name="系统总内存",
                legendgroup="系统总内存",
                mode="lines",
                line={"color": "#8c564b", "width": 1.2, "dash": "dot"},
                hovertemplate="系统总内存: %{y:.0f} MB<extra></extra>",
            ),
            row=2,
            col=1,
        )

    if not has_system_line:
        (
            total_cpu_times,
            total_cpu_values,
            total_memory_times,
            total_memory_values,
        ) = _aggregate_target_values(rows, cpu_count)
        if total_cpu_times or total_memory_times:
            add_series(
                "监控目标合计 (整机当量)",
                total_cpu_times,
                total_cpu_values,
                total_memory_times,
                total_memory_values,
                "#111111",
                width=1.8,
            )

    heading = title or f"进程性能回溯 · {len(groups)} 个进程序列"
    start_dt = rows[0][TIMESTAMP_KEY]
    end_dt = rows[-1][TIMESTAMP_KEY]
    if end_dt == start_dt:
        end_dt = start_dt + timedelta(seconds=1)
    memory_upper = _memory_axis_upper(rows)
    fig.update_layout(
        title={
            "text": f"{heading}",
            "x": 0.03,
            "xanchor": "left",
            "font": {"size": 16},
        },
        height=860,
        margin={"t": 55, "b": 48, "l": 74, "r": 250},
        legend={
            "orientation": "v",
            "yanchor": "top",
            "y": 0.98,
            "xanchor": "left",
            "x": 1.01,
            "bgcolor": "rgba(255,255,255,0.8)",
            "bordercolor": "#d8d8d8",
            "borderwidth": 1,
            "font": {"size": 11},
        },
        template="plotly_white",
        hovermode="x unified",
    )
    fig.update_xaxes(
        title_text="时间",
        range=[start_dt, end_dt],
        fixedrange=True,
        showgrid=True,
        gridcolor="#e6e6e6",
        row=2,
        col=1,
    )
    fig.update_xaxes(
        range=[start_dt, end_dt],
        fixedrange=True,
        showgrid=True,
        gridcolor="#e6e6e6",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="CPU (%)",
        range=[0, 100],
        fixedrange=True,
        zeroline=False,
        showgrid=True,
        gridcolor="#e6e6e6",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="内存 (MB)",
        range=[0, memory_upper],
        fixedrange=True,
        zeroline=False,
        showgrid=True,
        gridcolor="#e6e6e6",
        row=2,
        col=1,
    )
    fig.update_annotations(font={"size": 13})
    return fig


def write_html_report(
    records: Iterable[dict],
    path: str | Path,
    title: str | None = None,
    include_plotlyjs: bool = True,  # kept for backward compatibility
) -> Path:
    """Write an ECharts interactive HTML report for loaded CSV records."""
    from .html_report import write_html_report as _write_html

    return _write_html(records, path, title=title)
