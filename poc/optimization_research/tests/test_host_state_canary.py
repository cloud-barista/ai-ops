"""Tests for data timing and label isolation in the live canary experiment."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import host_state_canary as hsc  # noqa: E402


class CanaryDataTest(unittest.TestCase):
    def test_live_pairs_observe_both_probes_before_target(self):
        if not hsc.DEFAULT_DB.exists():
            self.skipTest('live SQLite campaign is unavailable')
        pairs = hsc.load_pairs(hsc.DEFAULT_DB)
        self.assertEqual(len(pairs), 550)
        self.assertEqual(pairs.round_index.nunique(), 110)
        self.assertEqual(pairs.model_id.nunique(), 5)
        self.assertTrue((pairs.anchor_ordinal < pairs.target_ordinal).all())
        self.assertTrue((pairs.canary_ordinal < pairs.target_ordinal).all())
        for scenario, (train_rounds, test_rounds) in hsc.scenarios(pairs).items():
            self.assertLess(max(train_rounds), min(test_rounds), scenario)
            if scenario.startswith(('v1_forward', 'v2_forward')):
                versions = pairs[pairs.round_index.isin(train_rounds + test_rounds)].collector_version
                self.assertEqual(versions.nunique(), 1)

    def test_hidden_test_outcomes_cannot_change_cold_start_prediction(self):
        if not hsc.DEFAULT_DB.exists():
            self.skipTest('live SQLite campaign is unavailable')
        pairs = hsc.load_pairs(hsc.DEFAULT_DB)
        held = sorted(pairs.model_id.unique())[0]
        train = pairs[(pairs.round_index < 43) & (pairs.model_id != held)]
        test = pairs[(pairs.round_index >= 43) & (pairs.round_index < 63) &
                     (pairs.model_id == held)].copy()
        altered = test.copy()
        altered['target_log_latency'] = -123.0
        altered['delta_log_latency'] = 987.0
        for method in ['anchor_et', 'anchor_canary_et', 'anchor_version_et',
                       'ratio_canary_linear']:
            original = hsc.predict_method(method, train, test, 'delta_log_latency', 42)
            mutated = hsc.predict_method(method, train, altered, 'delta_log_latency', 42)
            np.testing.assert_array_equal(original, mutated, err_msg=method)

    def test_rolling_calibration_ignores_current_and_future_labels(self):
        feature = {name: [1.0, 2.0, 3.0, 4.0] for name in hsc.BASE_FEATURES}
        train = pd.DataFrame({**feature, 'delta_log_latency': [0.1, 0.2, 0.3, 0.4]})
        test = pd.DataFrame({name: [2.5] for name in hsc.BASE_FEATURES})
        test['round_index'] = 4
        ref = pd.DataFrame({**feature, 'round_index': [1, 2, 4, 5],
                            'delta_log_latency': [0.2, 0.3, 0.4, 0.5]})
        original = hsc.predict_rolling_calibration(train, test, ref, 'delta_log_latency', 10, 42)
        ref.loc[ref.round_index >= 4, 'delta_log_latency'] = 1e9
        mutated = hsc.predict_rolling_calibration(train, test, ref, 'delta_log_latency', 10, 42)
        np.testing.assert_array_equal(original, mutated)


if __name__ == '__main__':
    unittest.main()
