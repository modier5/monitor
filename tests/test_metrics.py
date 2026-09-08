from __future__ import annotations

import unittest

from watchdog_perf.metrics import machine_cpu_percent


class MetricsTests(unittest.TestCase):
    def test_process_core_percent_is_converted_to_machine_percent(self) -> None:
        self.assertEqual(machine_cpu_percent(300.0, cpu_count=12), 25.0)
        self.assertEqual(machine_cpu_percent(2400.0, cpu_count=12), 100.0)

    def test_machine_percent_is_clamped_to_100(self) -> None:
        self.assertEqual(machine_cpu_percent(1500.0, cpu_count=2), 100.0)

    def test_returns_none_for_missing_value(self) -> None:
        self.assertIsNone(machine_cpu_percent(None, cpu_count=4))


if __name__ == "__main__":
    unittest.main()
