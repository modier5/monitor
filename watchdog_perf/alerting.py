"""Sustained-condition evaluation, session summaries and DingTalk robot alerts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import psutil

from .config import ConditionRule, DingTalkSettings, EndConditionSettings
from .metrics import machine_cpu_percent
from .storage import STATUS_MEASURE_ERROR, STATUS_OK

log = logging.getLogger("watchdog_perf.alerting")


def parse_extra_values(extra: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in str(extra or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: object) -> int | None:
    parsed = _float_or_none(value)
    return int(parsed) if parsed is not None else None


def _is_system_row(row: tuple) -> bool:
    return (
        len(row) >= 6
        and str(row[0]).upper() == "SYSTEM"
        and _int_or_none(row[1]) == -1
    )


@dataclass
class SampleMetrics:
    cpu_machine_percent: float | None
    memory_mb: float | None
    process_count: int
    raw_cpu_sum: float = 0.0

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[tuple],
        cpu_count: int | None = None,
    ) -> "SampleMetrics":
        normal = [
            row
            for row in rows
            if len(row) >= 6
            and int(row[4]) == STATUS_OK
            and _float_or_none(row[2]) is not None
            and _float_or_none(row[3]) is not None
        ]
        if not normal:
            return cls(None, None, 0)

        system_rows = [row for row in normal if _is_system_row(row)]
        process_rows = [row for row in normal if not _is_system_row(row)]

        if system_rows and not process_rows:
            cpu = float(system_rows[0][2])
            memory = sum(float(row[3]) for row in normal)
            return cls(min(100.0, cpu), memory, len(normal), cpu)

        raw_cpu = sum(float(row[2]) for row in process_rows)
        memory = (
            sum(float(row[3]) for row in process_rows)
            if process_rows
            else None
        )
        machine_cpu = machine_cpu_percent(raw_cpu, cpu_count)
        return cls(machine_cpu, memory, len(process_rows), raw_cpu)

    def satisfies(self, rule: ConditionRule) -> bool:
        if not rule.enabled:
            return True
        metric = rule.metric
        if metric == "cpu_machine_percent":
            value = self.cpu_machine_percent
        elif metric == "memory_mb":
            value = self.memory_mb
        elif metric == "process_count":
            value = float(self.process_count)
        else:
            raise ValueError(f"不支持的结束条件指标: {metric}")
        if value is None:
            return False
        operator = rule.operator
        if operator == ">=":
            return value >= rule.value
        if operator == "<=":
            return value <= rule.value
        if operator == ">":
            return value > rule.value
        if operator == "<":
            return value < rule.value
        raise ValueError(f"不支持的比较运算符: {operator}")


@dataclass
class _ProcessStats:
    name: str
    pid: int
    root_pid: int | None = None
    root_name: str = ""
    depth: int = 0
    observations: int = 0
    cpu_sum: float = 0.0
    cpu_max: float = 0.0
    memory_sum: float = 0.0
    memory_max: float = 0.0

    @property
    def cpu_avg(self) -> float:
        return self.cpu_sum / self.observations if self.observations else 0.0

    @property
    def memory_avg(self) -> float:
        return self.memory_sum / self.observations if self.observations else 0.0


@dataclass
class SessionSummary:
    started_at: datetime
    ended_at: datetime | None = None
    rows_written: int = 0
    exit_events: int = 0
    cpu_count: int = field(default_factory=lambda: psutil.cpu_count() or 1)
    cpu_observations: int = 0
    cpu_sum: float = 0.0
    cpu_max: float = 0.0
    memory_observations: int = 0
    memory_sum: float = 0.0
    memory_max: float = 0.0
    process_stats: dict[tuple[str, int], _ProcessStats] = field(default_factory=dict)

    def record(
        self,
        now: datetime,
        rows: list[tuple],
        metrics: SampleMetrics,
        include_system_rows: bool = True,
    ) -> None:
        if self.ended_at is None:
            self.started_at = self.started_at or now
        self.rows_written += len(rows)
        self.exit_events += sum(
            1 for row in rows if len(row) >= 6 and int(row[4]) != STATUS_OK
        )

        if metrics.cpu_machine_percent is not None:
            self.cpu_observations += 1
            self.cpu_sum += metrics.cpu_machine_percent
            self.cpu_max = max(self.cpu_max, metrics.cpu_machine_percent)
        if metrics.memory_mb is not None:
            self.memory_observations += 1
            self.memory_sum += metrics.memory_mb
            self.memory_max = max(self.memory_max, metrics.memory_mb)

        for row in rows:
            if len(row) < 6 or int(row[4]) != STATUS_OK:
                continue
            name = str(row[0])
            raw_pid = _int_or_none(row[1])
            pid = raw_pid if raw_pid is not None else 0
            if name.upper() == "SYSTEM" and pid == -1 and not include_system_rows:
                continue
            cpu = _float_or_none(row[2])
            memory = _float_or_none(row[3])
            extra = parse_extra_values(str(row[5] or ""))
            key = (name, pid)
            stats = self.process_stats.get(key)
            if stats is None:
                stats = _ProcessStats(
                    name=name,
                    pid=pid,
                    root_pid=_int_or_none(extra.get("root_pid")),
                    root_name=extra.get("root_name", ""),
                    depth=_int_or_none(extra.get("depth")) or 0,
                )
                self.process_stats[key] = stats
            if cpu is not None:
                stats.observations += 1
                stats.cpu_sum += cpu
                stats.cpu_max = max(stats.cpu_max, cpu)
            if memory is not None:
                stats.memory_sum += memory
                stats.memory_max = max(stats.memory_max, memory)

    def finish(self, ended_at: datetime | None = None) -> "SessionSummary":
        self.ended_at = ended_at or datetime.now()
        return self

    @property
    def duration_sec(self) -> float:
        end = self.ended_at or datetime.now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def cpu_machine_avg(self) -> float:
        return self.cpu_sum / self.cpu_observations if self.cpu_observations else 0.0

    @property
    def memory_avg(self) -> float:
        return (
            self.memory_sum / self.memory_observations
            if self.memory_observations
            else 0.0
        )

    def ordered_processes(self, limit: int = 30) -> list[_ProcessStats]:
        values = list(self.process_stats.values())
        values.sort(key=lambda item: item.cpu_avg, reverse=True)
        return values[:limit]


@dataclass
class AlertContext:
    triggered_at: datetime
    metrics: SampleMetrics
    summary: SessionSummary
    condition_text: str
    sustained_sec: float
    stop_requested: bool = True


class EndConditionMonitor:
    """Watches metric rules and/or disappearance of all target processes."""

    def __init__(
        self,
        settings: EndConditionSettings,
        cpu_count: int | None = None,
        has_process_targets: bool | None = None,
    ) -> None:
        self.settings = settings
        self.cpu_count = cpu_count or psutil.cpu_count() or 1
        if has_process_targets is None:
            has_process_targets = bool(
                getattr(settings, "has_process_targets", False)
            )
        self.has_process_targets = bool(has_process_targets)
        self.summary = SessionSummary(started_at=datetime.now())
        self._satisfied_since: datetime | None = None
        self._cooldown_until: datetime | None = None
        self._ever_had_target = False
        self._all_gone_since: datetime | None = None
        self.triggered = False

    def condition_text(self) -> str:
        parts = [
            f"{rule.metric} {rule.operator} {rule.value}"
            for rule in self.settings.rules
            if rule.enabled
        ]
        join = " 且 " if self.settings.combine == "all" else " 或 "
        text = join.join(parts) or "无"
        return f"{text} 持续 {self.settings.sustained_sec:.0f} 秒"

    def _has_live_process_row(self, rows: list[tuple]) -> bool:
        """A current live/readable target row exists in this sample."""
        for row in rows:
            if len(row) < 6:
                continue
            if _is_system_row(tuple(row)):
                if not self.has_process_targets:
                    return True
                continue
            try:
                status = int(row[4])
            except (TypeError, ValueError):
                continue
            if status in (STATUS_OK, STATUS_MEASURE_ERROR):
                return True
        return False

    def record(
        self,
        now: datetime,
        rows: list[tuple],
    ) -> AlertContext | None:
        metrics = SampleMetrics.from_rows(rows, self.cpu_count)
        self.summary.record(
            now,
            rows,
            metrics,
            include_system_rows=not self.has_process_targets,
        )
        if not self.settings.enabled:
            return None

        has_live_target = self._has_live_process_row(rows)
        if has_live_target:
            self._ever_had_target = True
            self._all_gone_since = None

        enabled_rules = [rule for rule in self.settings.rules if rule.enabled]
        if enabled_rules and has_live_target:
            if self.settings.combine == "all":
                satisfied = all(metrics.satisfies(rule) for rule in enabled_rules)
            else:
                satisfied = any(metrics.satisfies(rule) for rule in enabled_rules)
            if satisfied:
                if self._satisfied_since is None:
                    self._satisfied_since = now
                if (
                    self._cooldown_until is None
                    or now >= self._cooldown_until
                ) and (
                    now - self._satisfied_since
                ).total_seconds() >= self.settings.sustained_sec:
                    self.triggered = True
                    self._cooldown_until = now + timedelta(
                        seconds=self.settings.cooldown_sec
                    )
                    self.summary.finish(now)
                    return AlertContext(
                        triggered_at=now,
                        metrics=metrics,
                        summary=self.summary,
                        condition_text=self.condition_text(),
                        sustained_sec=self.settings.sustained_sec,
                        stop_requested="stop" in self.settings.action,
                    )
            else:
                self._satisfied_since = None
        else:
            # Without a live target the metric rules must not start a "sustained"
            # timer: waiting for a process that has not started yet is normal.
            self._satisfied_since = None

        if not self.settings.targets_gone_enabled:
            return None
        if has_live_target:
            return None
        if not self._ever_had_target:
            # The app may simply not have started yet; keep waiting.
            return None

        if self._all_gone_since is None:
            self._all_gone_since = now
        if self._cooldown_until is not None and now < self._cooldown_until:
            return None
        if (
            now - self._all_gone_since
        ).total_seconds() < self.settings.targets_gone_grace_sec:
            return None

        self.triggered = True
        self._cooldown_until = now + timedelta(seconds=self.settings.cooldown_sec)
        self.summary.finish(now)
        gone_text = (
            "目标进程全部退出，连续 "
            f"{self.settings.targets_gone_grace_sec:.0f} 秒未发现任何目标"
        )
        return AlertContext(
            triggered_at=now,
            metrics=metrics,
            summary=self.summary,
            condition_text=gone_text,
            sustained_sec=self.settings.targets_gone_grace_sec,
            stop_requested="stop" in self.settings.action,
        )


def rule_description(rule: ConditionRule) -> str:
    labels = {
        "cpu_machine_percent": "整机当量 CPU",
        "memory_mb": "进程组合计内存",
        "process_count": "监控进程数",
    }
    return f"{labels.get(rule.metric, rule.metric)} {rule.operator} {rule.value}"


def build_markdown(
    summary: SessionSummary,
    target_text: str,
    data_file: Path | None,
    condition_text: str,
    alert_at: datetime,
) -> str:
    processes = summary.ordered_processes(limit=30)
    lines = [
        "### Watchdog-Perf 性能监控结束通知",
        "",
        f"- **监控对象**: {target_text or '系统全局'}",
        f"- **触发条件**: {condition_text}",
        f"- **触发时间**: {alert_at:%Y-%m-%d %H:%M:%S}",
        f"- **监控时长**: {summary.duration_sec / 60:.1f} 分钟",
        f"- **样本/行数**: {summary.rows_written}",
        f"- **退出事件**: {summary.exit_events}",
        "",
        "#### 总览",
        "",
        f"- CPU 整机当量均值: **{summary.cpu_machine_avg:.1f}%** / 峰值 {summary.cpu_max:.1f}%",
        f"- 内存合计均值: **{summary.memory_avg:.1f} MB** / 峰值 {summary.memory_max:.1f} MB",
        f"- 涉及进程数: {len(summary.process_stats)}",
        "",
        "#### 进程明细（按平均 CPU 排序）",
        "",
    ]
    if processes:
        lines.append("| 进程 | PID | 根进程 | 深度 | CPU均值 | 内存峰值(MB) |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in processes:
            root = (
                f"{item.root_name}[{item.root_pid}]"
                if item.root_pid is not None
                else "自身"
            )
            lines.append(
                f"| {item.name} | {item.pid} | {root} | {item.depth} | "
                f"{item.cpu_avg:.1f}% | {item.memory_max:.1f} |"
            )
    else:
        lines.append("没有采集到正常指标数据。")
    if len(processes) < len(summary.process_stats):
        lines.append("")
        lines.append(f"另有 {len(summary.process_stats) - len(processes)} 个进程未逐项列出。")
    if data_file is not None:
        lines.extend(["", f"数据文件: `{data_file}`"])
    return "\n".join(lines)


class DingTalkNotifier:
    """Send signed markdown messages to a DingTalk group robot."""

    def __init__(self, settings: DingTalkSettings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.usable

    def signed_url(self) -> str:
        url = self.settings.webhook
        if not self.settings.secret:
            return url
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.settings.secret}"
        digest = hmac.new(
            self.settings.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}timestamp={timestamp}&sign={sign}"

    def send_markdown(self, title: str, text: str) -> bool:
        if not self.enabled:
            log.info("钉钉通知未启用，跳过发送")
            return False
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
            "at": {"isAtAll": False},
        }
        request = urllib.request.Request(
            self.signed_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(1.0, self.settings.timeout_sec),
            ) as response:
                body = json.loads(response.read().decode("utf-8", errors="replace"))
            if body.get("errcode") not in (0, None):
                log.error("钉钉返回错误: %s %s", body.get("errcode"), body.get("errmsg"))
                return False
            log.info("钉钉通知已发送: %s", title)
            return True
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            log.error("钉钉通知发送失败: %s", exc)
            return False

    def send_alert(self, context: AlertContext, target_text: str, data_file: Path | None) -> bool:
        text = build_markdown(
            summary=context.summary,
            target_text=target_text,
            data_file=data_file,
            condition_text=context.condition_text,
            alert_at=context.triggered_at,
        )
        return self.send_markdown("Watchdog-Perf 性能监控结束通知", text)


def parse_extra_pairs(extra: str) -> dict[str, str]:
    return parse_extra_values(extra)
