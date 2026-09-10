"""Crash-safe monitoring-session CSV storage.

Every sample is appended and flushed immediately so that a blue screen or an
unexpected process kill can only lose at most the row that was being written.
"""

from __future__ import annotations

import csv
import errno
import logging
import os
import re
import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, TextIO

COLUMNS = [
    "timestamp",
    "process_name",
    "pid",
    "cpu_percent",
    "memory_mb",
    "status",
    "extra",
]

STATUS_OK = 0
STATUS_NOT_FOUND = 1
STATUS_MEASURE_ERROR = 2

log = logging.getLogger("watchdog_perf.storage")


class DiskFullError(RuntimeError):
    """Raised when the configured free-disk threshold is crossed."""


def format_timestamp(dt: datetime) -> str:
    """Return millisecond ISO-like local timestamp, e.g. 2026-09-08 14:32:05.123."""
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def daily_filename(day: date) -> str:
    return f"Monitor_{day.isoformat()}.csv"


def session_filename(day: date, sequence: int) -> str:
    """Return the CSV name for one monitoring run: date plus daily sequence."""
    return f"Monitor_{day.isoformat()}_{max(1, int(sequence)):03d}.csv"


def _next_session_sequence(data_dir: Path, day: date) -> int:
    prefix = f"Monitor_{day.isoformat()}_"
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.csv$")
    highest = 0
    for path in data_dir.glob(f"{prefix}*.csv"):
        match = pattern.match(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _to_text(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class CsvStore:
    """Append-only writer for one monitoring run with immediate flush."""

    def __init__(
        self,
        data_dir: str | Path = "data",
        disk_limit_mb: int = 100,
        fsync_every_rows: int = 1,
        backup_every_rows: int = 10000,
        backup_keep: int = 3,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.disk_limit_bytes = int(disk_limit_mb) * 1024 * 1024
        self.fsync_every_rows = max(1, fsync_every_rows)
        self.backup_every_rows = max(0, int(backup_every_rows))
        self.backup_keep = max(1, int(backup_keep))
        self.backup_dir = self.data_dir / "backups"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._file: TextIO | None = None
        self._writer: csv.writer | None = None
        self._current_path: Path | None = None
        self._session_day: date | None = None
        self._session_sequence: int | None = None
        self._rows_since_check = 0
        self._rows_since_fsync = 0
        self._rows_since_backup = 0
        self._backup_sequence = 0
        self._last_disk_check = 0.0

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    def ensure_day(self, day: date) -> None:
        """Open a session file once; later date changes stay in the same file."""
        if self._file is not None:
            return
        self._check_disk_usage(force=True)
        for _ in range(1000):
            sequence = _next_session_sequence(self.data_dir, day)
            path = self.data_dir / session_filename(day, sequence)
            try:
                file = path.open("x", encoding="utf-8", newline="")
            except FileExistsError:
                continue
            try:
                writer = csv.writer(file, lineterminator="\n")
                file.write("\ufeff")
                writer.writerow(COLUMNS)
                file.flush()
                os.fsync(file.fileno())
            except Exception:
                file.close()
                try:
                    path.unlink()
                except OSError:
                    pass
                raise

            self._file = file
            self._writer = writer
            self._current_path = path
            self._session_day = day
            self._session_sequence = sequence
            self._rows_since_fsync = 0
            self._rows_since_backup = 0
            self._backup_sequence = 0
            return
        raise RuntimeError(f"could not allocate a session file for {day}")

    def _check_disk_usage(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_disk_check) < 30:
            return
        self._last_disk_check = now
        usage = shutil.disk_usage(self.data_dir)
        if usage.free < self.disk_limit_bytes:
            raise DiskFullError(
                f"free disk {usage.free / 1024 / 1024:.1f} MB is below "
                f"limit {self.disk_limit_bytes / 1024 / 1024:.0f} MB"
            )

    def write_rows(
        self,
        timestamp: datetime,
        rows: Iterable[tuple],
    ) -> int:
        """Write and flush one or more rows for the same timestamp."""
        if self._file is None or self._writer is None or self._session_day is None:
            self.ensure_day(timestamp.date())
        if self._file is None or self._writer is None:
            raise RuntimeError("CsvStore is not open")

        row_list = list(rows)
        if not row_list:
            return 0

        self._rows_since_check += len(row_list)
        if self._rows_since_check >= 30:
            self._rows_since_check = 0
            self._check_disk_usage()

        stamp = format_timestamp(timestamp)
        for row in row_list:
            if len(row) != len(COLUMNS) - 1 and len(row) != len(COLUMNS):
                raise ValueError(f"unexpected row width: {len(row)}")
            try:
                self._writer.writerow((stamp, *(_to_text(v) for v in row)))
                self._file.flush()
                self._rows_since_fsync += 1
                if self._rows_since_fsync >= self.fsync_every_rows:
                    self._rows_since_fsync = 0
                    self._file.flush()
                    os.fsync(self._file.fileno())
            except OSError as exc:
                if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == 112:
                    raise DiskFullError(f"write failed, disk is full: {exc}") from exc
                raise

        self._rows_since_backup += len(row_list)
        if (
            self.backup_every_rows > 0
            and self._rows_since_backup >= self.backup_every_rows
        ):
            self._create_backup()
            self._rows_since_backup = 0
        return len(row_list)

    def _flush_and_fsync(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._rows_since_fsync = 0

    def _create_backup(self) -> Path | None:
        """Copy the current CSV into the backup directory as a durable snapshot."""
        if (
            self._file is None
            or self._current_path is None
            or not self._current_path.exists()
            or self._current_path.stat().st_size == 0
        ):
            return None

        self._flush_and_fsync()
        self._backup_sequence += 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = (
            f"{self._current_path.stem}_backup_"
            f"{stamp}_{self._backup_sequence:04d}.csv"
        )
        target = self.backup_dir / name
        temp = target.with_suffix(target.suffix + ".tmp")
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._current_path, temp)
            os.replace(temp, target)
            self._prune_backups()
            log.info("backup created: %s", target)
            return target
        except OSError as exc:
            try:
                temp.unlink()
            except OSError:
                pass
            log.warning("backup failed: %s", exc)
            return None

    def _prune_backups(self) -> None:
        if self._current_path is None:
            return
        prefix = f"{self._current_path.stem}_backup_"
        backups = sorted(
            self.backup_dir.glob(f"{prefix}*.csv"),
            key=lambda path: path.name,
            reverse=True,
        )
        for old in backups[self.backup_keep :]:
            try:
                old.unlink()
            except OSError as exc:
                log.warning("could not remove old backup %s: %s", old, exc)

    def close(self) -> None:
        if self._file is not None:
            if self.backup_every_rows > 0 and self._rows_since_backup > 0:
                self._create_backup()
                self._rows_since_backup = 0
            self._flush_and_fsync()
            self._file.close()
        self._file = None
        self._writer = None
        self._current_path = None
        self._session_day = None
        self._session_sequence = None

    def write_error_marker(self, text: str) -> None:
        """Write a small non-CSV marker after a fatal storage error."""
        marker = self.data_dir / "ERROR_DISK_FULL.txt"
        try:
            marker.write_text(
                f"{format_timestamp(datetime.now())} {text}\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def __enter__(self) -> "CsvStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
