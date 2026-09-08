"""Helpers shared by collectors, alerting and charting."""

from __future__ import annotations

import psutil


def machine_cpu_percent(
    core_percent_sum: float | None,
    cpu_count: int | None = None,
    *,
    rounding: int = 2,
) -> float | None:
    """Convert psutil process-core CPU percent into whole-machine percent.

    psutil reports a busy process on one logical core as 100%.  Dividing the
    sum of those per-process values by the number of logical CPUs gives the
    machine-wide percentage, which must stay within 0-100.
    """
    if core_percent_sum is None:
        return None
    cores = int(cpu_count or psutil.cpu_count() or 1)
    value = min(100.0, max(0.0, float(core_percent_sum)) / max(1, cores))
    return round(value, rounding)
