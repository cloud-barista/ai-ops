"""Synthetic checks for the frozen RAGPulse transfer decision boundary."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_ragpulse_input_demand_transfer as study


class RAGPulseInputDemandTests(unittest.TestCase):
    def test_parser_preserves_duplicate_requests_and_zero_length(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "trace.jsonl"
            records = [
                {"timestamp": 0, "input_length": 10, "output_length": 999},
                {"timestamp": 0, "input_length": 20, "output_length": 999},
                {"timestamp": 300, "input_length": 0, "output_length": 999},
            ]
            source.write_text("\n".join(json.dumps(row) for row in records)+"\n")
            with patch.object(study, "EXPECTED_ROWS", 3):
                y, counts, rows = study.aggregate_jsonl(source, 4)
            self.assertEqual(rows, 3)
            self.assertEqual(y.tolist(), [30, 0, 0, 0])
            self.assertEqual(counts.tolist(), [2, 1, 0, 0])

    def test_parser_rejects_invalid_or_future_rows(self):
        bad_records = [
            [{"timestamp": 300, "input_length": 1},
             {"timestamp": 299, "input_length": 1}],
            [{"timestamp": 1200, "input_length": 1}],
            [{"timestamp": 0, "input_length": -1}],
            [{"timestamp": 0, "input_length": 1.5}],
            [{"timestamp": 0, "input_length": True}],
            [{"timestamp": "0", "input_length": 1}],
        ]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "trace.jsonl"
            for records in bad_records:
                source.write_text("\n".join(json.dumps(row) for row in records)+"\n")
                with patch.object(study, "EXPECTED_ROWS", len(records)):
                    with self.assertRaises(ValueError):
                        study.aggregate_jsonl(source, 4)

    def test_current_and_future_outcomes_do_not_change_current_features(self):
        y = np.full(2 * study.SLOTS_PER_DAY, 2000., dtype=float)
        original = study.design(y)
        y[350] = 200_000.
        changed = study.design(y)
        np.testing.assert_allclose(
            original.loc[:350, list(study.FEATURES)].to_numpy(float),
            changed.loc[:350, list(study.FEATURES)].to_numpy(float),
            equal_nan=True)
        self.assertEqual(original.loc[350, "scale"], changed.loc[350, "scale"])
        self.assertNotEqual(original.loc[350, "ratio"], changed.loc[350, "ratio"])
        self.assertNotEqual(original.loc[351, "lag1_over_scale"],
                            changed.loc[351, "lag1_over_scale"])

    def test_previous_day_multiplier_uses_only_completed_day(self):
        y = np.full(study.N_SLOTS, 2000., dtype=float)
        raw = np.full(study.N_SLOTS, 1000., dtype=float)
        scale = np.full(study.N_SLOTS, 2000., dtype=float)
        first = study.FIRST_TEST_DAY
        self.assertEqual(study.daily_multiplier(y, raw, scale, first-1), 2.)
        y[first*study.SLOTS_PER_DAY:] = 1_000_000.
        self.assertEqual(study.daily_multiplier(y, raw, scale, first-1), 2.)
        y[(first-1)*study.SLOTS_PER_DAY] = 3000.
        self.assertEqual(study.daily_multiplier(y, raw, scale, first-1), 2.)
        y[(first-1)*study.SLOTS_PER_DAY:] = 3000.
        self.assertEqual(study.daily_multiplier(y, raw, scale, first-1), 3.)

    def test_all_baselines_get_same_calibration_and_equal_predictions_do_not_win(self):
        frame = study.design(np.full(study.N_SLOTS, 2000., dtype=float))
        raw = {name: np.full(study.N_SLOTS, 1800., dtype=float)
               for name in study.RAW_METHODS}
        assessment = study.score(frame, raw)
        self.assertFalse(assessment["gate"]["all_passed"])
        self.assertEqual(assessment["gate"]["daily_wins"], 0)
        for entry in assessment["per_day"].values():
            self.assertEqual(set(entry["previous_day_multiplier"]),
                             set(study.RAW_METHODS))
            self.assertEqual(entry["eligible_bins"], 288)


if __name__ == "__main__":
    unittest.main()
