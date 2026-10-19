"""Causal initialization and delayed-feedback contracts for SwissAI baseline."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_swissai_tail_baselines", SCRIPTS / "evaluate_swissai_tail_baselines.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SwissAITailBaselineTests(unittest.TestCase):
    def test_warmup_and_test_labels_arrive_only_after_feedback_time(self) -> None:
        hour = MODULE.HOUR_US
        replay = MODULE.EmpiricalDurationP90(
            20.0,
            [(5 * hour, 1, 10.0), (4 * hour, 2, 2.0)],
            capacity=3, delay_us=hour,
        )
        self.assertEqual(replay.predict(3 * hour), (20.0, 0))
        self.assertEqual(replay.predict(4 * hour), (2.0, 1))
        self.assertAlmostEqual(replay.predict(5 * hour)[0], 9.2)
        replay.schedule(5 * hour, 3.0)
        # A request at the same timestamp sees the same completed history.
        self.assertAlmostEqual(replay.predict(5 * hour)[0], 9.2)
        self.assertAlmostEqual(replay.predict(6 * hour)[0], 9.2)
        self.assertAlmostEqual(replay.predict(6 * hour + 3 * MODULE.US)[0], 8.6)

    def test_expiry_uses_completion_order_and_rejects_reverse_arrivals(self) -> None:
        hour = MODULE.HOUR_US
        replay = MODULE.EmpiricalDurationP90(
            1.0,
            [(2 * hour, 1, 20.0), (1 * hour, 2, 2.0), (3 * hour, 3, 10.0)],
            capacity=2,
        )
        self.assertAlmostEqual(replay.predict(3 * hour)[0], 19.0)
        self.assertEqual(len(replay.buffer), 2)
        with self.assertRaises(ValueError):
            replay.predict(2 * hour)
        with self.assertRaises(ValueError):
            replay.schedule(3 * hour, -1.0)

    def test_frozen_fallback_and_warmup_are_disjoint_from_test(self) -> None:
        day0 = MODULE.DAY0_US
        day = MODULE.DAY_US
        us = MODULE.US
        compact = pa.table({
            "created_utc_us": pa.array([
                day0 + day, day0 + 2 * day, day0 + 29 * day,
                day0 + 35 * day, day0 + 39 * day,
                day0 + 40 * day, day0 + 41 * day,
            ], type=pa.int64()),
            "finished_utc_us": pa.array([
                day0 + day + 10 * us,
                day0 + 2 * day + 20 * us,
                day0 + 32 * day,  # Not known by the frozen day-30 boundary.
                day0 + 35 * day + 5 * us,
                day0 + 42 * day,  # Warmup request, future completion.
                day0 + 40 * day + 20 * us,
                day0 + 41 * day + 10 * us,
            ], type=pa.int64()),
        })
        predictions = pa.table({
            "created_utc_us": pa.array([day0 + 40 * day, day0 + 41 * day], type=pa.int64()),
            "duration_seconds": pa.array([20.0, 10.0], type=pa.float64()),
            "model_code": pa.array([0, 0], type=pa.int32()),
            "status_code": pa.array([0, 0], type=pa.int32()),
            "day": pa.array([40, 41], type=pa.int32()),
            "base_p90_seconds": pa.array([10.0, 10.0], type=pa.float64()),
            "global_p90_seconds": pa.array([12.0, 12.0], type=pa.float64()),
            "model_p90_seconds": pa.array([12.0, 12.0], type=pa.float64()),
            "guard92_p90_seconds": pa.array([None, 15.0], type=pa.float64()),
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compact_path = root / "compact.parquet"
            prediction_path = root / "predictions.parquet"
            pq.write_table(compact, compact_path)
            pq.write_table(predictions, prediction_path)
            fallback, warmup, initial = MODULE.frozen_prior_and_warmup(
                compact_path, verify_pinned_sha=False)
            self.assertAlmostEqual(fallback, 19.0)
            self.assertEqual(initial["eligible_training_labels"], 2)
            self.assertEqual(initial["day30_to_day40_positive_warmup_labels"], 2)
            self.assertEqual(len(warmup), 2)
            result = MODULE.evaluate(prediction_path, compact_path, root / "result",
                                     verify_pinned_sha=False)
            self.assertEqual(result["test_rows"], 2)
            self.assertEqual(result["fallback_uses"], 0)
            self.assertEqual(result["scores_all"]["guard92"]["n"], 1)
            output = pq.read_table(root / "result/direct_recent_p90_test_predictions.parquet")
            self.assertTrue(np.allclose(
                output["direct_recent_p90_seconds"].to_numpy(), [5.0, 18.5]
            ))
            self.assertEqual(output["completed_feedback_support"].to_pylist(), [1, 2])


if __name__ == "__main__":
    unittest.main()
