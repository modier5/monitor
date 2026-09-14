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
    resolve_input_paths,
    save_figure,
    write_html_report,
)
from watchdog_perf.html_report import _build_payload
from watchdog_perf.storage import COLUMNS, CsvStore


def _rows_from_csv(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as fp:
        return list(csv.reader(fp))


class CsvStoreTests(unittest.TestCase):
    def test_each_start_creates_new_session_file(self) -> None:
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

            # A second launch on the same day gets its own numbered CSV.
            store = CsvStore(data)
            store.ensure_day(day)
            store.write_rows(now + timedelta(seconds=2), [row_a])
            store.close()

            first = _rows_from_csv(data / "Monitor_2026-09-08_001.csv")
            second = _rows_from_csv(data / "Monitor_2026-09-08_002.csv")
            self.assertEqual(first[0], COLUMNS)
            self.assertEqual(len(first), 3)
            self.assertEqual(second[0], COLUMNS)
            self.assertEqual(len(second), 2)
            self.assertEqual(second[-1][1], "demo.exe")

    def test_session_continues_across_midnight_in_single_file(self) -> None:
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

            rows = _rows_from_csv(data / "Monitor_2026-09-08_001.csv")
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0], COLUMNS)
            self.assertTrue(rows[2][0].startswith("2026-09-09"))

    def test_date_resolution_finds_all_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            day = date(2026, 9, 8)
            for second in (0, 1):
                store = CsvStore(data)
                store.ensure_day(day)
                store.write_rows(
                    datetime(2026, 9, 8, 8, 0, second),
                    [("a.exe", 1, 0.1, 2.0, 0, "")],
                )
                store.close()

            paths = resolve_input_paths(["2026-09-08"], data)
            self.assertEqual(
                [path.name for path in paths],
                ["Monitor_2026-09-08_001.csv", "Monitor_2026-09-08_002.csv"],
            )

    def test_backup_snapshots_are_created_and_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            day = date(2026, 9, 8)
            start = datetime(2026, 9, 8, 8, 0, 0)
            store = CsvStore(
                data,
                backup_every_rows=2,
                backup_keep=2,
            )
            store.ensure_day(day)
            for index in range(6):
                store.write_rows(
                    start + timedelta(seconds=index),
                    [("a.exe", 1, float(index), 10.0 + index, 0, "")],
                )
            store.close()

            backups = sorted((data / "backups").glob("*.csv"))
            self.assertEqual(len(backups), 2)
            latest_rows = _rows_from_csv(backups[-1])
            self.assertEqual(len(latest_rows), 7)
            self.assertEqual(latest_rows[0], COLUMNS)

            paths = resolve_input_paths([str(data)], data)
            self.assertEqual(
                [path.name for path in paths],
                ["Monitor_2026-09-08_001.csv"],
            )


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

            records = load_csv_file(data / "Monitor_2026-09-08_001.csv")
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

            path = data / "Monitor_2026-09-08_001.csv"
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
            self.assertNotIn("P90", text)

    def test_html_report_merges_repeated_exe_invocations(self) -> None:
        start = datetime(2026, 9, 9, 9, 0, 0)
        records = []
        for run_index, pid in enumerate((100, 101, 102)):
            for second in range(3):
                records.append(
                    {
                        "timestamp": start
                        + timedelta(minutes=run_index, seconds=second),
                        "process_name": "ffmpeg.exe",
                        "pid": pid,
                        "cpu_percent": 600.0,
                        "memory_mb": 180.0 + pid,
                        "status": 0,
                        "extra": (
                            "exe_path=D:\\media\\ffmpeg.exe;cpu_cores=8"
                        ),
                    }
                )

        payload = _build_payload(records)
        self.assertEqual(len(payload["cpuSeries"]), 1)
        self.assertEqual(payload["cpuSeries"][0]["name"], "ffmpeg.exe")
        self.assertEqual(len(payload["memSeries"]), 1)
        process_stats = [row for row in payload["stats"] if not row["system"]]
        self.assertEqual(len(process_stats), 1)

    def test_html_report_falls_back_to_process_name_for_old_csv(self) -> None:
        start = datetime(2026, 9, 9, 9, 0, 0)
        records = []
        for run_index, pid in enumerate((21736, 13060)):
            records.append(
                {
                    "timestamp": start + timedelta(minutes=run_index),
                    "process_name": "ffmpeg.exe",
                    "pid": pid,
                    "cpu_percent": 700.0,
                    "memory_mb": 188.0,
                    "status": 0,
                    "extra": "",
                }
            )

        payload = _build_payload(records)
        self.assertEqual(len(payload["cpuSeries"]), 1)
        self.assertEqual(payload["cpuSeries"][0]["name"], "ffmpeg.exe")

    def test_html_report_sums_concurrent_exe_cpu_without_exceeding_100(self) -> None:
        start = datetime(2026, 9, 9, 9, 0, 0)
        records = [
            {
                "timestamp": start,
                "process_name": "ffmpeg.exe",
                "pid": 100,
                "cpu_percent": 500.0,
                "memory_mb": 180.0,
                "status": 0,
                "extra": "exe_path=D:\\media\\ffmpeg.exe;cpu_cores=8",
            },
            {
                "timestamp": start,
                "process_name": "ffmpeg.exe",
                "pid": 101,
                "cpu_percent": 400.0,
                "memory_mb": 220.0,
                "status": 0,
                "extra": "exe_path=D:\\media\\ffmpeg.exe;cpu_cores=8",
            },
        ]

        payload = _build_payload(records)
        self.assertEqual(len(payload["cpuSeries"]), 1)
        self.assertEqual(len(payload["cpuSeries"][0]["data"]), 1)
        self.assertLessEqual(payload["cpuSeries"][0]["data"][0], 100.0)
        self.assertAlmostEqual(payload["memSeries"][0]["data"][0], 400.0)


    def test_load_csv_rebuilds_partial_minsec_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "Monitor_2026-09-12_需要核对时间.csv"
            header = "timestamp,process_name,pid,cpu_percent,memory_mb,status,extra\n"
            rows = (
                "00:00.4,demo.exe,100,1.0,10.0,0,\n"
                "00:00.4,demo.exe,101,1.0,10.1,0,\n"
                "00:00.4,SYSTEM,-1,2.0,100.0,0,\n"
                "00:00.4,demo.exe,102,1.0,10.2,0,\n"
                "00:01.4,demo.exe,100,1.0,10.0,0,\n"
                "00:01.4,demo.exe,101,1.0,10.1,0,\n"
                "00:01.4,SYSTEM,-1,2.0,100.0,0,\n"
                "00:01.4,demo.exe,102,1.0,10.2,0,\n"
                "59:59.4,demo.exe,100,1.0,10.0,0,\n"
                "59:59.4,demo.exe,101,1.0,10.1,0,\n"
                "59:59.4,SYSTEM,-1,2.0,100.0,0,\n"
                "59:59.4,demo.exe,102,1.0,10.2,0,\n"
                "00:00.2,demo.exe,100,1.0,10.0,0,\n"
                "00:00.2,demo.exe,101,1.0,10.1,0,\n"
                "00:00.2,SYSTEM,-1,2.0,100.0,0,\n"
                "00:00.2,demo.exe,102,1.0,10.2,0,\n"
            )
            data.write_text(header + rows, encoding="utf-8-sig")

            records = load_csv_file(data)
            stamps = sorted({rec["timestamp"] for rec in records})
            self.assertEqual(
                stamps,
                [
                    datetime(2026, 9, 12, 0, 0, 0, 400000),
                    datetime(2026, 9, 12, 0, 0, 1, 400000),
                    datetime(2026, 9, 12, 0, 59, 59, 400000),
                    datetime(2026, 9, 12, 1, 0, 0, 200000),
                ],
            )


if __name__ == "__main__":
    unittest.main()
