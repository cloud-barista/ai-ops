"""Leakage and physical-domain checks for observed-VRAM peak research."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_vram_peak import (CAMPAIGNS, fit_predict, selected_measurements,
                                static_features)


ROOT = Path(__file__).resolve().parents[1]


class VramPeakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = pd.read_parquet(ROOT / 'data/audit_v1/measurements.parquet')
        cls.campaigns = selected_measurements(source)

    def test_selected_panel_and_registration_metadata(self):
        self.assertEqual([len(self.campaigns[c]) for c in CAMPAIGNS],
                         [575, 18, 44])
        main = self.campaigns[CAMPAIGNS[0]]
        self.assertEqual(main.model_id.nunique(), 9)
        self.assertTrue((main.reserved_peak_gib > main.parameter_gib).all())
        self.assertTrue((main.parameter_gib > 0).all())
        all_sizes = pd.concat([frame[['model_id', 'parameter_gib']]
                               for frame in self.campaigns.values()])
        self.assertTrue((all_sizes.groupby('model_id').parameter_gib.nunique() == 1).all())

    def test_target_peak_and_external_outcome_cannot_change_predictions(self):
        main = self.campaigns[CAMPAIGNS[0]]
        held = 'google/bert_uncased_L-2_H-128_A-2'
        train = main.loc[main.model_id != held]
        external = self.campaigns[CAMPAIGNS[2]].loc[
            self.campaigns[CAMPAIGNS[2]].model_id == held].copy()
        original = fit_predict(train, external, {held})
        changed = external.copy()
        changed['reserved_peak_gib'] *= 1000
        changed['gpu_memory_reserved_bytes'] *= 1000
        changed['result_json'] = changed.result_json.map(
            lambda raw: json.dumps({**json.loads(raw),
                                    'gpu_memory_reserved_bytes': 10 ** 14}))
        pd.testing.assert_frame_equal(pd.DataFrame(static_features(external)),
                                      pd.DataFrame(static_features(changed)))
        after = fit_predict(train, changed, {held})
        for method in original:
            np.testing.assert_allclose(original[method], after[method], rtol=1e-12, atol=1e-12)

    def test_held_model_cannot_enter_donor_training(self):
        main = self.campaigns[CAMPAIGNS[0]]
        held = 'google/bert_uncased_L-2_H-128_A-2'
        test = main.loc[main.model_id == held].head(2)
        with self.assertRaisesRegex(ValueError, 'Held model included'):
            fit_predict(main, test, {held})

    def test_nonpositive_donor_overhead_is_rejected(self):
        main = self.campaigns[CAMPAIGNS[0]]
        held = 'google/bert_uncased_L-2_H-128_A-2'
        train = main.loc[main.model_id != held].copy()
        test = main.loc[main.model_id == held].head(2)
        train.loc[train.index[0], 'reserved_peak_gib'] = 0.0
        with self.assertRaisesRegex(ValueError, 'positive'):
            fit_predict(train, test, {held})


if __name__ == '__main__':
    unittest.main()
