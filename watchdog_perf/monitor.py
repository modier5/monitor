"""Sampling loop, process matching and process-tree discovery."""

from __future__ import annotations

import csv
import fnmatch
import io
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

from .storage import (
    STATUS_MEASURE_ERROR,
    STATUS_NOT_FOUND,
    STATUS_OK,
    CsvStore,
    DiskFullError,
)

log = logging.getLogger("watchdog_perf.monitor")


def _normalize_exe_path(path: str) -> str:
    """Normalize a Windows path for case-insensitive exact comparison."""
    return os.path.normcase(os.path.normpath(str(path).strip().strip('"')))


def _matches_exe_path(actual_path: str, configured_paths: tuple[str, ...]) -> bool:
    try:
        actual = _normalize_exe_path(os.path.realpath(actual_path or ""))
    except OSError:
        actual = _normalize_exe_path(actual_path or "")
    if not actual:
        return False
    for configured in configured_paths:
        try:
            expected = _normalize_exe_path(os.path.realpath(configured))
        except OSError:
            expected = _normalize_exe_path(configured)
        if actual == expected:
            return True
    return False


@dataclass
class MonitorConfig:
    process_patterns: tuple[str, ...] = ()
    exe_paths: tuple[str, ...] = ()
    pids: tuple[int, ...] = ()
    interval_sec: float = 1.0
    data_dir: str | Path = "data"
    disk_limit_mb: int = 100
    fsync_every_rows: int = 1
    self_status_interval_sec: float = 300.0
    include_children: bool = True

    def __post_init__(self) -> None:
        if self.interval_sec <= 0:
            raise ValueError("sampling interval must be positive")
        self.process_patterns = tuple(self.process_patterns)
        self.exe_paths = tuple(self.exe_paths)
        self.pids = tuple(int(pid) for pid in self.pids)

    @property
    def has_process_targets(self) -> bool:
        return bool(self.process_patterns or self.exe_paths or self.pids)


@dataclass
class _Tracked:
    name: str
    create_time: float | None
    handle: psutil.Process | None
    explicit: bool = False
    missing: bool = False
    error_active: bool = False
    root_pid: int | None = None
    root_name: str = ""
    parent_pid: int | None = None
    depth: int = 0


def round_cpu(value: float) -> float:
    """psutil process CPU may exceed 100 for multi-threaded processes."""
    return round(max(0.0, float(value)), 2)


class PerfMonitor:
    """Collect process trees or whole-system samples into a CsvStore."""

    def __init__(self, config: MonitorConfig, store: CsvStore) -> None:
        self.config = config
        self.store = store
        self._tracked: dict[int, _Tracked] = {}
        self._last_status_log = 0.0
        self._rows_written = 0
        self._cached_name_pids: set[int] = set()
        self._cached_exe_pids: set[int] = set()
        self._last_name_scan = 0.0
        self._last_exe_scan = 0.0
        self._name_scan_interval = 5.0

    def _scan_pattern_pids(self) -> set[int]:
        if os.name == "nt":
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=flags,
                )
                pids: set[int] = set()
                rows = list(csv.reader(io.StringIO(result.stdout)))
                for row in csv.reader(io.StringIO(result.stdout)):
                    if len(row) < 2:
                        continue
                    name = row[0].strip().lower()
                    for pattern in self.config.process_patterns:
                        if fnmatch.fnmatchcase(name, pattern.lower()):
                            try:
                                pids.add(int(row[1].strip()))
                            except ValueError:
                                pass
                            break
                if rows:
                    return pids
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        matched: set[int] = set()
        try:
            for proc in psutil.process_iter():
                try:
                    name = proc.name()
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue
                for pattern in self.config.process_patterns:
                    if fnmatch.fnmatchcase(name.lower(), pattern.lower()):
                        matched.add(proc.pid)
                        break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return matched

    def _scan_exe_pids(self) -> set[int]:
        matched: set[int] = set()
        try:
            for proc in psutil.process_iter():
                try:
                    exe_path = proc.exe()
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue
                if exe_path and _matches_exe_path(exe_path, self.config.exe_paths):
                    matched.add(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        self._cached_exe_pids = matched

    @staticmethod
    def _read_proc(
        proc: psutil.Process,
    ) -> tuple[str, float | None, int | None, str | None] | None:
        try:
            name = proc.name()
            create_time = proc.create_time()
            ppid = proc.ppid() or None
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        try:
            exe_path = proc.exe() or None
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            exe_path = None
        return name, create_time, ppid, exe_path

    def _add_tree(
        self,
        live: dict[int, tuple[str, float | None, int | None, str | None]],
        pid: int,
    ) -> None:
        try:
            root = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return
        info = self._read_proc(root)
        if info is not None:
            live[pid] = info
        if not self.config.include_children:
            return
        try:
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        for child in descendants:
            child_info = self._read_proc(child)
            if child_info is not None:
                live[child.pid] = child_info

    def _scan_live(self) -> dict[int, tuple[str, float | None, int | None, str | None]]:
        live: dict[int, tuple[str, float | None, int | None, str | None]] = {}

        # PID targets: avoid scanning the whole process table; just read the tree.
        now = time.monotonic()
        if self.config.process_patterns and (
            not self._last_name_scan
            or now - self._last_name_scan >= self._name_scan_interval
        ):
            self._cached_name_pids = self._scan_pattern_pids()
            self._last_name_scan = now

        for pid in self._cached_name_pids:
            self._add_tree(live, pid)

        if self.config.exe_paths and (
            not self._last_exe_scan
            or now - self._last_exe_scan >= self._name_scan_interval
        ):
            self._scan_exe_pids()
            self._last_exe_scan = now

        for pid in self._cached_exe_pids:
            self._add_tree(live, pid)

        for pid in self.config.pids:
            if pid not in live:
                self._add_tree(live, pid)

        # A descendant can survive its root process. Keep it tracked until it
        # actually exits instead of dropping it on the next sample.
        for pid, state in list(self._tracked.items()):
            if pid in live or state.root_pid is None:
                continue
            if psutil.pid_exists(pid):
                self._add_tree(live, pid)
        return live

    def _find_roots(
        self,
        live: dict[int, tuple[str, float | None, int | None, str | None]],
    ) -> dict[int, tuple[str, float | None, int | None, str | None]]:
        explicit = set(self.config.pids)
        roots: dict[int, tuple[str, float | None, int | None, str | None]] = {}
        for pid, info in live.items():
            name = info[0]
            matched = pid in explicit
            if not matched:
                for pattern in self.config.process_patterns:
                    if fnmatch.fnmatchcase(name.lower(), pattern.lower()):
                        matched = True
                        break
            if not matched and info[3] and self.config.exe_paths:
                matched = _matches_exe_path(info[3], self.config.exe_paths)
            if matched:
                roots[pid] = info
        return roots

    def _select_wanted(
        self,
        live: dict[int, tuple[str, float | None, int | None, str | None]],
        roots: dict[int, tuple[str, float | None, int | None, str | None]],
    ) -> dict[int, tuple]:
        """Return wanted pids with root metadata.

        A wanted tuple is:
        (name, create_time, root_pid, root_name, parent_pid, depth)
        """
        wanted: dict[int, tuple] = {}
        if not self.config.include_children or not roots:
            for pid, (name, create_time, ppid, _exe) in roots.items():
                wanted[pid] = (name, create_time, pid, name, ppid, 0)
            return wanted

        children: dict[int, list[int]] = {}
        for pid, (_name, _create_time, ppid, _exe) in live.items():
            if ppid is not None:
                children.setdefault(ppid, []).append(pid)

        visited: set[int] = set()
        for root_pid in sorted(roots):
            if root_pid in visited:
                continue
            root_info = roots[root_pid]
            root_name = root_info[0]
            stack: list[tuple[int, int]] = [(root_pid, 0)]
            while stack:
                pid, depth = stack.pop()
                if pid in visited:
                    continue
                visited.add(pid)
                if pid not in live:
                    continue
                name, create_time, ppid, _exe = live[pid]
                wanted[pid] = (
                    name,
                    create_time,
                    root_pid,
                    root_name,
                    ppid,
                    depth,
                )
                stack.extend((child, depth + 1) for child in children.get(pid, []))

        # Keep live descendants whose root process has disappeared but whose
        # process itself is still running (orphan processes).
        for pid, state in list(self._tracked.items()):
            if pid in wanted or pid not in live:
                continue
            if state.root_pid is None:
                continue
            name, create_time, ppid, _exe = live[pid]
            wanted[pid] = (
                name,
                create_time,
                state.root_pid,
                state.root_name or name,
                state.parent_pid if state.parent_pid is not None else ppid,
                state.depth,
            )
        return wanted

    @staticmethod
    def _extra(state: _Tracked) -> str:
        parts: list[str] = []
        if state.root_pid is not None:
            parts.append(f"root_pid={state.root_pid}")
            parts.append(f"root_name={state.root_name or state.name}")
            parts.append(f"parent_pid={state.parent_pid or ''}")
            parts.append(f"depth={state.depth}")
        return ";".join(parts)

    def _collect_process_rows(
        self,
        now: datetime,
    ) -> list[tuple]:
        explicit_pids = set(self.config.pids)
        live = self._scan_live()
        roots = self._find_roots(live)
        wanted = self._select_wanted(live, roots)

        rows: list[tuple] = []

        # Explicit root pids that are absent receive a one-time not-found marker.
        for pid in explicit_pids:
            if pid in wanted:
                continue
            state = self._tracked.get(pid)
            if state is None:
                try:
                    name = psutil.Process(pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    name = f"PID:{pid}"
                self._tracked[pid] = _Tracked(
                    name=name,
                    create_time=None,
                    handle=None,
                    explicit=True,
                    root_pid=pid,
                    root_name=name,
                    depth=0,
                )
                state = self._tracked[pid]
            if not state.missing:
                rows.append(
                    (
                        state.name,
                        pid,
                        "",
                        "",
                        STATUS_NOT_FOUND,
                        self._extra(state) + ";process_not_found",
                    )
                )
                state.missing = True
                log.warning("target pid %s not found", pid)

        # Mark exits for previously tracked pids that are no longer alive.
        for pid in list(self._tracked):
            state = self._tracked[pid]
            if pid in live:
                if pid not in wanted and not state.explicit:
                    # Process left the selected tree while still alive.
                    self._tracked.pop(pid, None)
                continue
            if state.missing:
                continue
            rows.append(
                (
                    state.name,
                    pid,
                    "",
                    "",
                    STATUS_NOT_FOUND,
                    self._extra(state) + ";process_exit",
                )
            )
            state.missing = True
            log.info("tracked process ended: %s pid=%s", state.name, pid)
            if not state.explicit:
                self._tracked.pop(pid, None)

        for pid in sorted(wanted):
            (
                name,
                create_time,
                root_pid,
                root_name,
                parent_pid,
                depth,
            ) = wanted[pid]
            previous = self._tracked.get(pid)
            if previous is not None and not previous.missing:
                if (
                    previous.create_time is not None
                    and create_time is not None
                    and previous.create_time != create_time
                ):
                    rows.append(
                        (
                            previous.name,
                            pid,
                            "",
                            "",
                            STATUS_NOT_FOUND,
                            self._extra(previous) + ";process_restarted",
                        )
                    )
                    log.info("pid %s reused by a new process", pid)
                    previous.missing = True
                    previous = None
            elif previous is not None and previous.missing:
                log.info("process returned: %s pid=%s", name, pid)
                previous = None

            if previous is None:
                try:
                    handle = psutil.Process(pid)
                except psutil.NoSuchProcess:
                    continue
                state = _Tracked(
                    name=name,
                    create_time=create_time,
                    handle=handle,
                    explicit=pid in explicit_pids,
                    root_pid=root_pid,
                    root_name=root_name,
                    parent_pid=parent_pid,
                    depth=depth,
                )
                self._tracked[pid] = state
            else:
                state = previous
                state.root_pid = root_pid
                state.root_name = root_name
                state.parent_pid = parent_pid
                state.depth = depth

            cpu_value: float | None = None
            memory_bytes: int | None = None
            try:
                if state.handle is None:
                    state.handle = psutil.Process(pid)
                cpu_raw = state.handle.cpu_percent(interval=None)
                if cpu_raw is not None:
                    cpu_value = round_cpu(float(cpu_raw))
                memory_bytes = state.handle.memory_info().rss
            except psutil.NoSuchProcess:
                rows.append(
                    (
                        name,
                        pid,
                        "",
                        "",
                        STATUS_NOT_FOUND,
                        self._extra(state) + ";process_exit",
                    )
                )
                state.missing = True
                if not state.explicit:
                    self._tracked.pop(pid, None)
                continue
            except (psutil.AccessDenied, psutil.Error) as exc:
                if not state.error_active:
                    error_text = f"{type(exc).__name__}"
                    rows.append(
                        (
                            name,
                            pid,
                            "",
                            "",
                            STATUS_MEASURE_ERROR,
                            self._extra(state) + ";" + error_text,
                        )
                    )
                    state.error_active = True
                continue

            memory_mb = (
                round(memory_bytes / 1024 / 1024, 2)
                if memory_bytes is not None
                else ""
            )
            rows.append(
                (
                    name,
                    pid,
                    "" if cpu_value is None else cpu_value,
                    memory_mb,
                    STATUS_OK,
                    self._extra(state),
                )
            )
            state.missing = False
            state.error_active = False

        return rows

    def _collect_system_row(self) -> list[tuple]:
        cpu_raw = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        memory_mb = round(vm.used / 1024 / 1024, 2)
        total_mb = round(vm.total / 1024 / 1024, 2)
        extra = (
            f"memory_percent={vm.percent:.2f};"
            f"memory_total_mb={total_mb:.2f};"
            f"cpu_cores={psutil.cpu_count() or '?'}"
        )
        return [
            (
                "SYSTEM",
                -1,
                round(min(100.0, max(0.0, float(cpu_raw or 0.0))), 2),
                memory_mb,
                STATUS_OK,
                extra,
            )
        ]

    def _collect_once(self, now: datetime) -> list[tuple]:
        if self.config.has_process_targets:
            return self._collect_process_rows(now)
        return self._collect_system_row()

    def _log_self_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and (now - self._last_status_log)
            < self.config.self_status_interval_sec
        ):
            return
        self._last_status_log = now
        proc = psutil.Process()
        memory_mb = proc.memory_info().rss / 1024 / 1024
        log.info(
            "status rows=%d monitor_rss=%.2f MB",
            self._rows_written,
            memory_mb,
        )

    def run_until_stopped(
        self,
        stop_event: threading.Event | None = None,
        on_sample: Optional[Callable[[datetime, list[tuple]], None]] = None,
    ) -> None:
        """Run until stop_event is set or a fatal storage error occurs."""
        if stop_event is None:
            stop_event = threading.Event()
        next_tick = time.monotonic()
        consecutive_errors = 0
        last_error_text = ""
        try:
            self.store.ensure_day(datetime.now().date())
            self._log_self_status(force=True)
            while not stop_event.is_set():
                now = datetime.now()
                try:
                    self.store.ensure_day(now.date())
                    rows = self._collect_once(now)
                    if rows:
                        written = self.store.write_rows(now, rows)
                        self._rows_written += written
                    if on_sample is not None:
                        on_sample(now, rows)
                    consecutive_errors = 0
                    last_error_text = ""
                except DiskFullError as exc:
                    log.error("stopping: %s", exc)
                    self.store.write_error_marker(str(exc))
                    raise
                except Exception as exc:  # keep monitoring despite transient errors
                    consecutive_errors += 1
                    text = f"{type(exc).__name__}: {exc}"
                    if text != last_error_text or consecutive_errors == 1:
                        try:
                            self.store.write_rows(
                                now,
                                [
                                    (
                                        "",
                                        -1,
                                        "",
                                        "",
                                        STATUS_MEASURE_ERROR,
                                        text[:200],
                                    )
                                ],
                            )
                        except Exception:
                            pass
                        log.exception("sample failed: %s", text)
                    last_error_text = text

                self._log_self_status()
                next_tick += self.config.interval_sec
                delay = max(0.0, next_tick - time.monotonic())
                if consecutive_errors >= 5:
                    delay = max(delay, 5.0)
                    next_tick = time.monotonic()
                    consecutive_errors = 0
                if stop_event.wait(delay):
                    break
        finally:
            self._log_self_status(force=True)
            self.store.close()
