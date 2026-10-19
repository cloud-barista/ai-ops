"""Exact rolling quantiles agree with NumPy after feedback arrives."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rolling_quantile_buffer.py"
SPEC = importlib.util.spec_from_file_location("rolling_quantile_buffer", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RollingQuantileBufferTest(unittest.TestCase):
    def test_matches_numpy_with_duplicate_and_expiring_feedback(self) -> None:
        rng = np.random.default_rng(20260928)
        values = [0.0, -2.0, 0.0, 5.0, 5.0] + rng.integers(-3, 4, size=100).tolist()
        for capacity in (1, 2, 5, 31):
            for quantile in (0, 0.5, 0.9, 1):
                buffer = MODULE.RollingQuantileBuffer(capacity, quantile)
                self.assertIsNone(buffer.value())
                for index, value in enumerate(values):
                    buffer.add(value)
                    current = values[max(0, index + 1 - capacity):index + 1]
                    self.assertEqual(len(buffer), len(current))
                    self.assertAlmostEqual(buffer.value(), float(np.quantile(current, quantile)))

    def test_rejects_bad_values_and_capacity(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.RollingQuantileBuffer(0)
        with self.assertRaises(ValueError):
            MODULE.RollingQuantileBuffer(2, 1.1)
        buffer = MODULE.RollingQuantileBuffer(2)
        with self.assertRaises(ValueError):
            buffer.add(float("nan"))
        self.assertEqual(len(buffer), 0)


if __name__ == "__main__":
    unittest.main()
