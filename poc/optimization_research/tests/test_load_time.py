"""Checks for model-isolated local loading-time prediction."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_load_time import (CAMPAIGNS, FEATURES, evaluate, final_features,
                                fit_final_main_model, fit_predict, select_panels,
                                static_features)


ROOT = Path(__file__).resolve().parents[1]


class LoadTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panels = select_panels(pd.read_parquet(
            ROOT / 'data/audit_v1/measurements.parquet'))

    def test_comparable_panel_and_positive_measurements(self):
        self.assertEqual([len(self.panels[c]) for c in CAMPAIGNS], [575, 18, 44])
        main = self.panels[CAMPAIGNS[0]]
        self.assertEqual(main.model_id.nunique(), 9)
        self.assertEqual(main.collector_version.nunique(), 1)
        self.assertTrue((main.load_seconds < main.wall_seconds).all())
        self.assertEqual(static_features(main).shape, (575, len(FEATURES)))
        main_conditions = set(zip(main.model_id, main.workload_group))
        for campaign in CAMPAIGNS[1:]:
            external = self.panels[campaign]
            self.assertFalse(main_conditions & set(zip(
                external.model_id, external.workload_group)))

    def test_held_model_and_revision_cannot_enter_donor(self):
        main = self.panels[CAMPAIGNS[0]]
        held = 'google/bert_uncased_L-2_H-128_A-2'
        test = main.loc[main.model_id == held].head(2)
        with self.assertRaisesRegex(ValueError, 'Held-out model'):
            fit_predict(main, test, {held})
        with self.assertRaisesRegex(ValueError, 'unheld model'):
            fit_predict(main.loc[main.model_id != held],
                        main.loc[main.model_id != held].head(2), {held})

    def test_held_outcomes_and_result_payload_cannot_change_prediction(self):
        main = self.panels[CAMPAIGNS[0]]
        held = 'google/bert_uncased_L-2_H-128_A-2'
        train = main.loc[main.model_id != held]
        test = self.panels[CAMPAIGNS[2]].loc[
            self.panels[CAMPAIGNS[2]].model_id == held].copy()
        original = fit_predict(train, test, {held})
        changed = test.copy()
        changed['load_seconds'] *= 1000.0
        changed['wall_seconds'] *= 1000.0
        changed['latency_p95_ms'] *= 1000.0
        changed['examples_per_second'] /= 1000.0
        changed['result_json'] = changed.result_json.map(lambda raw: json.dumps({
            **json.loads(raw), 'load_seconds': 1e6,
            'compute_seconds': 1e6, 'wall_seconds': 1e6,
        }))
        np.testing.assert_allclose(static_features(test), static_features(changed))
        mutated = fit_predict(train, changed, {held})
        self.assertEqual(set(original), set(mutated))
        for method in original:
            np.testing.assert_allclose(original[method], mutated[method],
                                       rtol=0.0, atol=1e-12)

    def test_fold_training_uses_main_donors_only(self):
        predictions, splits = evaluate(self.panels)
        self.assertEqual(len(splits), 11)
        main = self.panels[CAMPAIGNS[0]]
        source_by_id = main.set_index('execution_id').model_id.to_dict()
        for split in splits:
            held = set(split['held_model_ids'])
            self.assertFalse(held & set(split['train_model_ids']))
            self.assertTrue(all(source_by_id[eid] not in held
                                for eid in split['train_execution_ids']))
        self.assertTrue((predictions.actual_load_seconds > 0).all())
        self.assertTrue((predictions.predicted_load_seconds > 0).all())

    def test_final_fitted_estimator_round_trip(self):
        main = self.panels[CAMPAIGNS[0]]
        for method in ('size_only_extra_trees', 'static_log_ridge',
                       'static_log_extra_trees'):
            bundle = fit_final_main_model(main, method)
            features = final_features(main.head(3), method)
            original = np.exp(bundle['estimator'].predict(features))
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'model.joblib'
                joblib.dump(bundle, target)
                loaded = joblib.load(target)
                after = np.exp(loaded['estimator'].predict(features))
            self.assertEqual(tuple(loaded['feature_names']),
                             ('log_parameter_gib',) if method == 'size_only_extra_trees'
                             else FEATURES)
            self.assertEqual(loaded['method'], method)
            self.assertEqual(len(loaded['training_execution_ids']), 575)
            self.assertEqual(loaded['training_examples'],
                             9 if method == 'size_only_extra_trees' else 575)
            np.testing.assert_allclose(original, after, rtol=0.0, atol=1e-12)


if __name__ == '__main__':
    unittest.main()
