"""Helpers shared by collectors, alerting and charting."""

from __future__ import annotations

import psutil

# One shared series palette so the desktop JSON/PNG/SVG exports and the
# interactive HTML report keep the same colours for the same series.
SERIES_PALETTE = (
    "#0FA396",
    "#4C8DE6",
    "#8E7CF0",
    "#E0A33E",
    "#D2687A",
    "#3FB1C8",
    "#7BA65A",
    "#A07CC9",
    "#E9825B",
    "#5B8FA8",
    "#C97FA8",
    "#C9A227",
    "#6E8B9E",
    "#8FA6C4",
    "#B57EDC",
    "#D08C60",
)

# Chart chrome shared by the HTML and matplotlib renderers.
CHART_INK = "#0F1829"
CHART_MUTED = "#55647C"
CHART_GRID = "#E7ECF3"
CHART_ACCENT = "#0FA396"


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
