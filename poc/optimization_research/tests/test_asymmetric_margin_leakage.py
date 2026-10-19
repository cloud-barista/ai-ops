"""Integration check: changing outer-target outcomes cannot alter donor calibration."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluate_asymmetric_margin import freeze_reference, nested_donor_predictions


class NestedCalibrationLeakageTest(unittest.TestCase):
    @unittest.skipUnless(
        (ROOT / 'artifacts/asymmetric_margin_v1/nested_calibration_predictions.parquet').exists(),
        'Full nested calibration artifact is unavailable')
    def test_outer_outcome_corruption_does_not_change_nested_donor_predictions(self):
        snapshot = ROOT / 'data/audit_v1/measurements.parquet'
        predictions = ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet'
        main, frozen = freeze_reference(snapshot, predictions)
        held = sorted(main.model_id.unique())[0]
        expected = pd.read_parquet(
            ROOT / 'artifacts/asymmetric_margin_v1/nested_calibration_predictions.parquet')
        expected = expected[expected.outer_model_id == held].sort_values('execution_id')
        corrupted = main.copy()
        mask = corrupted.model_id == held
        corrupted.loc[mask, 'latency_p95_ms'] *= 1000.0
        corrupted.loc[mask, 'examples_per_second'] *= 0.001
        corrupted.loc[mask, 'wall_seconds'] *= 100.0
        actual, provenance = nested_donor_predictions(corrupted, held, frozen)
        actual = actual.sort_values('execution_id')
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(actual.execution_id.tolist(), expected.execution_id.tolist())
        for column in (
            'space_cov_transfer_latency_log', 'space_cov_transfer_rps_log',
            'latency_p95_ms', 'examples_per_second',
        ):
            np.testing.assert_allclose(actual[column], expected[column], rtol=0, atol=1e-12)
        self.assertTrue(all(held not in row['fit_model_ids'] for row in provenance))


if __name__ == '__main__':
    unittest.main()
