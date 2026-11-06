"""Standalone ECharts HTML report rendering."""

from __future__ import annotations

import html as html_mod
import json
import math
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import psutil

from .metrics import SERIES_PALETTE, machine_cpu_percent

TS_KEY = "timestamp"
NAME_KEY = "process_name"
PID_KEY = "pid"
CPU_KEY = "cpu_percent"
MEMORY_KEY = "memory_mb"
STATUS_KEY = "status"
EXTRA_KEY = "extra"

STATUS_OK = 0
MAX_POINTS = 4000

PALETTE = list(SERIES_PALETTE)

_ECHARTS_FILE = Path(__file__).with_name("report_assets") / "echarts.min.js"
_echarts_js: str | None = None


def _load_echarts_js() -> str:
    global _echarts_js
    if _echarts_js is None:
        _echarts_js = _ECHARTS_FILE.read_text(encoding="utf-8")
    return _echarts_js


def _extra_pairs(extra: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for part in str(extra or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            values[key.strip()] = float(value.strip())
        except ValueError:
            continue
    return values


def _is_system(row: dict) -> bool:
    return (
        str(row.get(NAME_KEY, "")).upper() == "SYSTEM"
        and int(row.get(PID_KEY) or -1) == -1
    )


def _extra_fields(extra: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in str(extra or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _normalize_path(path: str) -> str:
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _series_identity(row: dict) -> tuple:
    if _is_system(row):
        return ("system", -1)
    name = str(row.get(NAME_KEY, "") or "unknown")
    exe_path = _extra_fields(str(row.get(EXTRA_KEY, "") or "")).get("exe_path")
    if exe_path:
        return ("exe", _normalize_path(exe_path))
    return ("name", name.casefold())


def _mean(values: list[float | None]) -> float:
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else 0.0


def _infer_cpu_count(rows: Iterable[dict]) -> int:
    for row in rows:
        extra = _extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
        for key in ("cpu_cores", "cpu_count", "cores"):
            value = extra.get(key)
            if value is not None and value > 0:
                return int(value)
    return psutil.cpu_count() or 1


def _memory_total_mb(rows: Iterable[dict]) -> float:
    for row in rows:
        if _is_system(row):
            extra = _extra_pairs(str(row.get(EXTRA_KEY, "") or ""))
            if extra.get("memory_total_mb"):
                return extra["memory_total_mb"]
    return psutil.virtual_memory().total / 1024 / 1024


def _build_payload(records: Iterable[dict]) -> dict:
    rows = sorted(records, key=lambda row: row[TS_KEY])
    if not rows:
        raise ValueError("没有可绘图的数据")

    cpu_count = _infer_cpu_count(rows)
    memory_total = _memory_total_mb(rows)
    timeline = list(
        OrderedDict.fromkeys(row[TS_KEY] for row in rows if int(row[STATUS_KEY]) == STATUS_OK)
    )
    if not timeline:
        raise ValueError("CSV 中没有可绘制的正常采样记录")

    proc_names: dict[tuple, str] = {}
    proc_raw_cpu: dict[tuple, dict[datetime, float]] = {}
    proc_mem: dict[tuple, dict[datetime, float]] = {}
    sys_rows: dict[datetime, dict] = {}
    raw_cpu_sum: dict[datetime, float] = {}
    target_mem_sum: dict[datetime, float] = {}

    for row in rows:
        if int(row[STATUS_KEY]) != STATUS_OK:
            continue
        timestamp = row[TS_KEY]
        if _is_system(row):
            sys_rows[timestamp] = row
            continue
        key = _series_identity(row)
        name = str(row[NAME_KEY] or "unknown")
        proc_names.setdefault(key, name)
        cpu = row.get(CPU_KEY)
        memory = row.get(MEMORY_KEY)
        if cpu is not None:
            cpu_value = float(cpu)
            raw_cpu = proc_raw_cpu.setdefault(key, {})
            raw_cpu[timestamp] = raw_cpu.get(timestamp, 0.0) + cpu_value
            raw_cpu_sum[timestamp] = raw_cpu_sum.get(timestamp, 0.0) + cpu_value
        if memory is not None:
            proc_mem_values = proc_mem.setdefault(key, {})
            proc_mem_values[timestamp] = (
                proc_mem_values.get(timestamp, 0.0) + float(memory)
            )
            target_mem_sum[timestamp] = target_mem_sum.get(timestamp, 0.0) + float(memory)

    has_system = bool(sys_rows)
    sys_cpu = []
    sys_used = []
    sys_total_array = []
    for timestamp in timeline:
        row = sys_rows.get(timestamp)
        if row is not None:
            sys_cpu.append(float(row[CPU_KEY]) if row.get(CPU_KEY) is not None else None)
            sys_used.append(float(row[MEMORY_KEY]) if row.get(MEMORY_KEY) is not None else None)
        elif has_system:
            sys_cpu.append(None)
            sys_used.append(None)
        else:
            raw_cpu = raw_cpu_sum.get(timestamp, 0.0)
            sys_cpu.append(machine_cpu_percent(raw_cpu, cpu_count))
            sys_used.append(target_mem_sum.get(timestamp))
        sys_total_array.append(memory_total)

    cpu_series: list[dict] = []
    mem_series: list[dict] = []
    stats: list[dict] = []
    for index, key in enumerate(proc_names):
        name = proc_names[key]
        label = name
        color = PALETTE[index % len(PALETTE)]
        raw_cpu_by_time = proc_raw_cpu.get(key, {})
        cpu_values = [
            machine_cpu_percent(raw_cpu_by_time.get(ts), cpu_count)
            if ts in raw_cpu_by_time
            else None
            for ts in timeline
        ]
        mem_by_time = proc_mem.get(key, {})
        mem_values = [mem_by_time.get(ts) for ts in timeline]
        cpu_series.append({"name": label, "data": cpu_values, "color": color})
        mem_series.append({"name": label, "data": mem_values, "color": color})
        clean_cpu = [v for v in cpu_values if v is not None]
        clean_mem = [v for v in mem_values if v is not None]
        stats.append(
            {
                "label": label,
                "system": False,
                "avg_cpu": _mean(cpu_values),
                "max_cpu": max(clean_cpu) if clean_cpu else 0.0,
                "avg_mem": _mean(mem_values),
                "max_mem": max(clean_mem) if clean_mem else 0.0,
                "cnt": len(clean_cpu),
            }
        )

    if sys_cpu and any(value is not None for value in sys_cpu):
        system_label_cpu = "系统整体 CPU" if has_system else "监控目标合计 (整机当量)"
        system_label_mem = "系统已用内存" if has_system else "监控目标合计内存"
        stats.insert(
            0,
            {
                "label": system_label_cpu,
                "system": True,
                "avg_cpu": _mean(sys_cpu),
                "max_cpu": max(value or 0.0 for value in sys_cpu),
                "avg_mem": _mean(sys_used),
                "max_mem": max(value or 0.0 for value in sys_used),
                "cnt": sum(1 for value in sys_cpu if value is not None),
            },
        )
    else:
        system_label_cpu = "系统整体 CPU"
        system_label_mem = "系统已用内存"

    axis_ts = timeline
    axis_labels = [
        ts.strftime("%H:%M:%S")
        if axis_ts[0].date() == axis_ts[-1].date()
        else ts.strftime("%m-%d %H:%M")
        for ts in axis_ts
    ]

    step = max(1, math.ceil((len(axis_ts) or 1) / MAX_POINTS))
    indices = list(range(0, len(axis_ts), step)) or [0]
    if indices[-1] != len(axis_ts) - 1:
        indices.append(len(axis_ts) - 1)
    axis_labels = [axis_labels[index] for index in indices]
    sys_cpu = [sys_cpu[index] for index in indices]
    sys_used = [sys_used[index] for index in indices]
    sys_total_array = [sys_total_array[index] for index in indices]
    for series in cpu_series:
        series["data"] = [series["data"][index] for index in indices]
    for series in mem_series:
        series["data"] = [series["data"][index] for index in indices]

    return {
        "axis": axis_labels,
        "sysCpu": sys_cpu,
        "sysUsed": sys_used,
        "sysTotal": sys_total_array,
        "sysCpuName": system_label_cpu,
        "sysMemName": system_label_mem,
        "cpuSeries": cpu_series,
        "memSeries": mem_series,
        "stats": stats,
        "memoryTotal": memory_total,
        "start": axis_ts[0],
        "end": axis_ts[-1],
        "points": len(timeline),
        "rawPoints": len(timeline),
        "durationSec": max(0.0, (axis_ts[-1] - axis_ts[0]).total_seconds()),
        "cpuMax": 100.0,
        "files": 0,
    }


def _build_table(rows: list[dict]) -> str:
    columns = (
        "进程",
        "平均CPU%",
        "峰值CPU%",
        "平均内存MB",
        "峰值内存MB",
        "采样数",
    )
    head = "".join(f"<th>{col}</th>" for col in columns)
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td class='pn'>{html_mod.escape(str(row['label']))}</td>"
            f"<td>{row['avg_cpu']:.2f}</td>"
            f"<td>{row['max_cpu']:.2f}</td>"
            f"<td>{row['avg_mem']:.0f}</td>"
            f"<td>{row['max_mem']:.0f}</td>"
            f"<td>{row['cnt']}</td></tr>"
        )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _render_html(payload: dict, title: str) -> str:
    stats = payload["stats"]
    top_cpu = sorted(stats, key=lambda row: -row["avg_cpu"])[:12]
    top_mem = sorted(stats, key=lambda row: -row["max_mem"])[:12]
    data_json = json.dumps(payload, ensure_ascii=False, default=str)
    config = json.dumps(
        {
            "memoryTotal": payload["memoryTotal"],
            "cpuMax": payload["cpuMax"],
        },
        ensure_ascii=False,
    )
    clean_cpu = [value for value in payload["sysCpu"] if value is not None]
    sys_cpu_avg = sum(clean_cpu) / len(clean_cpu) if clean_cpu else None
    clean_used = [value for value in payload["sysUsed"] if value is not None]
    sys_mem_pct = 0.0
    if clean_used and payload["memoryTotal"]:
        sys_mem_pct = max(clean_used) / payload["memoryTotal"] * 100.0

    replacements = {
        "__TITLE__": html_mod.escape(title),
        "__DATA_JSON__": data_json,
        "__CONFIG_JSON__": config,
        "__PALETTE__": json.dumps(PALETTE),
        "__POINTS__": str(payload["rawPoints"]),
        "__DURATION__": f"{payload['durationSec'] / 60.0:.1f}",
        "__PROCESS_COUNT__": str(
            sum(1 for row in payload["stats"] if not row.get("system"))
        ),
        "__CPU_AVG__": f"{sys_cpu_avg:.1f}" if sys_cpu_avg is not None else "-",
        "__MEM_PEAK__": f"{sys_mem_pct:.1f}",
        "__TOP_CPU__": _build_table(top_cpu),
        "__TOP_MEM__": _build_table(top_mem),
    }
    template = _HTML_TEMPLATE
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template.replace("__ECHARTS_JS__", _load_echarts_js())


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Watchdog-Perf 监控报告</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #F4F6FB; --panel: #FFFFFF; --panel-alt: #EFF3F9; --border: #E2E8F2;
    --text: #0F1829; --muted: #55647C; --faint: #8090A6;
    --accent: #0FA396; --accent-soft: #E2F6F2; --accent-ink: #0A6B63;
    --sys: #0F1829; --grid: #E7ECF3; --shadow: 0 10px 30px rgba(28, 42, 67, .07);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #090D15; --panel: #121A27; --panel-alt: #0D1520; --border: #1E2938;
      --text: #F2F6FC; --muted: #95A4B9; --faint: #7C8CA1;
      --accent: #0FA396; --accent-soft: #10302F; --accent-ink: #6FE3D2;
      --sys: #E8EEF7; --grid: #1B2634; --shadow: 0 14px 36px rgba(0, 0, 0, .35);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI", system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 32px 24px 56px; }
  .hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 26px; }
  .brand { display: flex; align-items: center; gap: 14px; }
  .mark {
    width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center;
    background: linear-gradient(145deg, #2BD1BD, #1C7CD0);
    box-shadow: 0 10px 24px rgba(26, 184, 174, .28);
  }
  .mark svg { width: 24px; height: 24px; }
  h1 { font-size: 24px; font-weight: 600; margin: 0 0 5px; letter-spacing: .2px; }
  .sub { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.7; }
  .pill {
    display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px;
    background: var(--accent-soft); color: var(--accent-ink); font-size: 12px; font-weight: 600; white-space: nowrap;
  }
  .pill i { display: block; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
  .cards { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; margin-bottom: 20px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 18px;
    padding: 18px 20px; box-shadow: var(--shadow);
  }
  .card .k { font-size: 11px; color: var(--muted); }
  .card .v { font-size: 26px; font-weight: 600; margin-top: 8px; letter-spacing: .3px; }
  .card .v small { font-size: 13px; font-weight: 500; color: var(--muted); margin-left: 3px; }
  .panel {
    background: var(--panel); border: 1px solid var(--border); border-radius: 18px;
    padding: 22px 24px; margin-bottom: 18px; box-shadow: var(--shadow);
  }
  .panel h2 { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
  .panel .hint { font-size: 12px; color: var(--muted); margin: 0 0 14px; }
  .chart { width: 100%; height: 460px; }
  table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  th, td { padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--border); }
  th { font-size: 11px; font-weight: 500; color: var(--muted); }
  th:first-child, td.pn { text-align: left; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover td { background: var(--panel-alt); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  .two h3 { font-size: 13px; font-weight: 600; margin: 0 0 10px; color: var(--muted); }
  .err {
    display: none; background: var(--accent-soft); border: 1px solid var(--accent);
    color: var(--accent-ink); padding: 10px 14px; border-radius: 12px; margin-bottom: 12px; font-size: 12px;
  }
  footer { margin-top: 6px; color: var(--faint); font-size: 11px; line-height: 1.8; }
  @media (max-width: 980px) {
    .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .two { grid-template-columns: 1fr; }
    .chart { height: 380px; }
  }
  @media (max-width: 560px) {
    .cards { grid-template-columns: 1fr; }
    .hero { display: block; }
    .pill { margin-top: 12px; }
    .wrap { padding: 20px 14px 40px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <div class="brand">
      <span class="mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2.6 12h4.2l2.5-6.6 4 13.2 2.4-6.6h5.7"/>
        </svg>
      </span>
      <div>
        <h1>进程资源监控报告</h1>
        <p class="sub">
          数据范围：__TITLE__<br>
          进程 CPU 已按逻辑核数归一化，整机占用范围 0&ndash;100%。
        </p>
      </div>
    </div>
    <span class="pill"><i></i>离线本地报告</span>
  </header>

  <section class="cards">
    <div class="card"><div class="k">采样点数</div><div class="v">__POINTS__</div></div>
    <div class="card"><div class="k">监控时长</div><div class="v">__DURATION__<small>分钟</small></div></div>
    <div class="card"><div class="k">监控进程数</div><div class="v">__PROCESS_COUNT__</div></div>
    <div class="card"><div class="k">整机平均 CPU</div><div class="v">__CPU_AVG__<small>%</small></div></div>
    <div class="card"><div class="k">整机内存峰值</div><div class="v">__MEM_PEAK__<small>%</small></div></div>
  </section>

  <section class="panel">
    <h2>CPU 占用率随时间变化</h2>
    <p class="hint">悬停查看数值，滚轮或底部滑条缩放时间轴；虚线为整机占用。</p>
    <div class="err" id="errCpu">图表库加载失败：下方静态摘要表仍可查看核心数据。</div>
    <div class="chart" id="chartCpu"></div>
  </section>

  <section class="panel">
    <h2>内存占用随时间变化</h2>
    <p class="hint">横向虚线为系统总内存，纵轴按本机物理内存上限显示。</p>
    <div class="err" id="errMem">图表库加载失败：下方静态摘要表仍可查看核心数据。</div>
    <div class="chart" id="chartMem"></div>
  </section>

  <section class="panel">
    <h2>进程占用排行</h2>
    <p class="hint">不依赖脚本的静态摘要，便于直接复制或打印。</p>
    <div class="two">
      <div><h3>按平均 CPU 排序 Top 12</h3>__TOP_CPU__</div>
      <div><h3>按峰值内存排序 Top 12</h3>__TOP_MEM__</div>
    </div>
  </section>

  <footer>由 WatchdogPerf 生成。图表为离线 ECharts，不依赖网络；页面会随系统浅色 / 深色外观自动切换。</footer>
</div>
<script>var DATA = __DATA_JSON__; var CFG = __CONFIG_JSON__; var SERIES_COLORS = __PALETTE__;</script>
<script>__ECHARTS_JS__</script>
<script>
(function () {
  if (typeof window === 'undefined' || !window.echarts) {
    document.getElementById('errCpu').style.display = 'block';
    document.getElementById('errMem').style.display = 'block';
    return;
  }
  var root = document.documentElement;
  function css(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }
  function baseAxis(kind) {
    var text = css('--text'), muted = css('--muted'), grid = css('--grid');
    return {
      textStyle: { fontFamily: '"Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif', color: text },
      tooltip: {
        trigger: 'axis', confine: true, padding: [8, 12],
        backgroundColor: css('--panel'), borderColor: css('--border'), borderWidth: 1,
        textStyle: { fontSize: 12, color: text },
        extraCssText: 'border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.16);'
      },
      legend: {
        type: 'scroll', top: 2, itemWidth: 16, itemHeight: 8,
        textStyle: { fontSize: 11, color: muted }, pageTextStyle: { color: muted }
      },
      grid: { left: kind === 'mem' ? 72 : 58, right: 24, top: 60, bottom: 68, containLabel: true },
      xAxis: {
        type: 'category', boundaryGap: false, data: DATA.axis,
        axisLine: { lineStyle: { color: grid } },
        axisTick: { show: false },
        axisLabel: { color: muted, fontSize: 11, hideOverlap: true }
      },
      dataZoom: [
        { type: 'inside' },
        {
          type: 'slider', height: 18, bottom: 12,
          borderColor: grid, backgroundColor: 'transparent',
          fillerColor: 'rgba(15,163,150,.12)',
          handleStyle: { color: css('--accent') },
          moveHandleStyle: { color: css('--accent') },
          textStyle: { fontSize: 10, color: muted }
        }
      ]
    };
  }
  function series(list, sysName, sysData, extra) {
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      out.push({
        name: s.name, type: 'line', data: s.data, symbol: 'none', connectNulls: false,
        lineStyle: { width: 1.4, color: s.color }, itemStyle: { color: s.color },
        emphasis: { focus: 'series', lineStyle: { width: 2.8 } }
      });
    }
    out.push({
      name: sysName, type: 'line', data: sysData, symbol: 'none', connectNulls: false,
      lineStyle: { width: 2.6, type: 'dashed', color: css('--sys') },
      itemStyle: { color: css('--sys') }, z: 99
    });
    if (extra) out.push(extra);
    return out;
  }
  function cpuOption() {
    var opt = baseAxis('cpu');
    opt.color = SERIES_COLORS;
    opt.yAxis = {
      type: 'value', min: 0, max: CFG.cpuMax, name: '%',
      nameTextStyle: { color: css('--muted'), fontSize: 11, align: 'left' },
      axisLabel: { color: css('--muted'), fontSize: 11 },
      splitLine: { lineStyle: { type: 'dashed', color: css('--grid') } }
    };
    opt.series = series(DATA.cpuSeries, DATA.sysCpuName, DATA.sysCpu, null);
    return opt;
  }
  function memOption() {
    var opt = baseAxis('mem');
    opt.color = SERIES_COLORS;
    opt.yAxis = {
      type: 'value', min: 0,
      max: Math.ceil(CFG.memoryTotal / 1024) * 1024,
      name: 'MB',
      nameTextStyle: { color: css('--muted'), fontSize: 11, align: 'left' },
      axisLabel: {
        color: css('--muted'), fontSize: 11,
        formatter: function (value) { return value.toLocaleString(); }
      },
      splitLine: { lineStyle: { type: 'dashed', color: css('--grid') } }
    };
    var totalLine = {
      name: '系统总内存', type: 'line', symbol: 'none', data: [],
      markLine: {
        symbol: 'none', silent: true,
        lineStyle: { type: 'dotted', color: css('--grid'), width: 1 },
        label: { color: css('--muted'), fontSize: 10 },
        data: [{ yAxis: CFG.memoryTotal, name: '系统总内存' }]
      }
    };
    opt.series = series(DATA.memSeries, DATA.sysMemName, DATA.sysUsed, totalLine);
    return opt;
  }
  var cpuChart = echarts.init(document.getElementById('chartCpu'));
  var memChart = echarts.init(document.getElementById('chartMem'));
  function render() {
    cpuChart.setOption(cpuOption(), true);
    memChart.setOption(memOption(), true);
  }
  render();
  var media = window.matchMedia('(prefers-color-scheme: dark)');
  if (media.addEventListener) { media.addEventListener('change', render); }
  else if (media.addListener) { media.addListener(render); }
  window.addEventListener('resize', function () { cpuChart.resize(); memChart.resize(); });
})();
</script>
</body>
</html>
"""


def write_html_report(
    records: Iterable[dict],
    path: str | Path,
    title: str | None = None,
) -> Path:
    """Write an ECharts interactive HTML report for loaded CSV records."""
    payload = _build_payload(records)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_html(payload, title or "Watchdog-Perf"), encoding="utf-8")
    return out
