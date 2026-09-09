from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from watchdog_perf.plotting import (
    _aggregate_target_series,
    load_csv_file,
    make_figure,
    save_figure,
    write_html_report,
)
from watchdog_perf.storage import COLUMNS, CsvStore


def _rows_from_csv(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as fp:
        return list(csv.reader(fp))


class CsvStoreTests(unittest.TestCase):
    def test_append_resume_keeps_single_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            day = date(2026, 9, 8)
            now = datetime(2026, 9, 8, 8, 0, 0)
            row_a = ("demo.exe", 100, 1.5, 80.25, 0, "")
            row_b = ("demo.exe", 100, 2.0, 81.0, 0, "")

            store = CsvStore(data)
            store.ensure_day(day)
            store.write_rows(now, [row_a])
            store.write_rows(now + timedelta(seconds=1), [row_b])
            store.close()

            # Simulate a restart: reopen the same file and continue appending.
            store = CsvStore(data)
            store.ensure_day(day)
            store.write_rows(now + timedelta(seconds=2), [row_a])
            store.close()

            rows = _rows_from_csv(data / "Monitor_2026-09-08.csv")
            self.assertEqual(rows[0], COLUMNS)
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[-1][1], "demo.exe")

    def test_partial_tail_is_removed_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            day = date(2026, 9, 8)
            store = CsvStore(data)
            store.ensure_day(day)
            store.write_rows(datetime(2026, 9, 8, 8, 0, 0), [("a.exe", 1, 0.1, 2.0, 0, "")])
            store.close()

            path = data / "Monitor_2026-09-08.csv"
            with path.open("a", encoding="utf-8", newline="") as fp:
                fp.write("2026-09-08 08:00:01.000,a.exe,1,broken")

            store = CsvStore(data)
            store.ensure_day(day)
            store.write_rows(datetime(2026, 9, 8, 8, 0, 2), [("a.exe", 1, 0.2, 2.1, 0, "")])
            store.close()

            rows = _rows_from_csv(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(rows[-1]), len(COLUMNS))
            self.assertNotIn("broken", rows[-1])

    def test_midnight_rollover_creates_second_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            store = CsvStore(data)
            store.ensure_day(date(2026, 9, 8))
            store.write_rows(
                datetime(2026, 9, 8, 23, 59, 59),
                [("a.exe", 1, 0.1, 2.0, 0, "")],
            )
            store.ensure_day(date(2026, 9, 9))
            store.write_rows(
                datetime(2026, 9, 9, 0, 0, 0),
                [("a.exe", 1, 0.2, 2.1, 0, "")],
            )
            store.close()

            first = _rows_from_csv(data / "Monitor_2026-09-08.csv")
            second = _rows_from_csv(data / "Monitor_2026-09-09.csv")
            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 2)
            self.assertEqual(second[0], COLUMNS)


class PlottingTests(unittest.TestCase):
    def test_aggregate_target_cpu_is_normalized_and_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            store = CsvStore(data)
            store.ensure_day(date(2026, 9, 8))
            start = datetime(2026, 9, 8, 8, 0, 0)
            for index in range(5):
                store.write_rows(
                    start + timedelta(seconds=index),
                    [
                        ("alpha.exe", 100, 300.0, 100.0, 0, "cpu_cores=8"),
                        ("beta.exe", 200, 200.0, 200.0, 0, "cpu_cores=8"),
                    ],
                )
            store.close()

            records = load_csv_file(data / "Monitor_2026-09-08.csv")
            cpu_points, memory_points = _aggregate_target_series(records, 8)
            self.assertTrue(cpu_points)
            self.assertTrue(memory_points)
            self.assertAlmostEqual(cpu_points[0][1], 62.5)
            self.assertTrue(all(point[1] <= 100.0 for point in cpu_points))
            self.assertAlmostEqual(memory_points[0][1], 300.0)

    def test_csv_and_figure_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            store = CsvStore(data)
            day = date(2026, 9, 8)
            store.ensure_day(day)
            start = datetime(2026, 9, 8, 8, 0, 0)
            for index in range(30):
                store.write_rows(
                    start + timedelta(seconds=index),
                    [
                        ("alpha.exe", 100, float(index % 10), 100.0 + index, 0, ""),
                        ("beta.exe", 200, float((index * 2) % 15), 200.0 + index, 0, ""),
                    ],
                )
            store.close()

            path = data / "Monitor_2026-09-08.csv"
            records = load_csv_file(path)
            self.assertEqual(len(records), 60)
            figure = make_figure(records, title="test")
            output = save_figure(figure, data / "chart.png")
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 1000)

    def test_interactive_html_report_is_written(self) -> None:
        start = datetime(2026, 9, 9, 9, 0, 0)
        records = [
            {
                "timestamp": start,
                "process_name": "alpha.exe",
                "pid": 100,
                "cpu_percent": 10.0,
                "memory_mb": 128.0,
                "status": 0,
                "extra": "",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = write_html_report(
                records,
                Path(tmp) / "report.html",
                title="html test",
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn("Licensed to the Apache Software Foundation", text)
            self.assertIn("chartCpu", text)
            self.assertIn("chartMem", text)
            self.assertIn("CPU 占用率", text)
            self.assertIn('"cpuMax": 100.0', text)
            self.assertIn('"memoryTotal":', text)
            self.assertNotIn("cdn.jsdelivr.net", text)
            self.assertNotIn("unpkg.com", text)
            self.assertNotIn("plotly-graph-div", text)


if __name__ == "__main__":
    unittest.main()
