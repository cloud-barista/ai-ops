"""Behavioral checks for the unseen-model loading-time support gate."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_load_support import (
    CAMPAIGNS, evaluate, fit_support_bundle, predict_support, select_panels,
    summarize,
)


ROOT = Path(__file__).resolve().parents[1]


class LoadSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panels = select_panels(pd.read_parquet(
            ROOT / 'data/audit_v1/measurements.parquet'))
        cls.main = cls.panels[CAMPAIGNS[0]]

    def test_one_causal_donor_is_not_inflated_by_63_runs(self):
        donor = self.main
        bundle = fit_support_bundle(donor)
        causal = donor.loc[donor.workload_task == 'causal_lm_forward'].head(1)
        support = predict_support(bundle, causal)
        self.assertEqual(bundle['task_support']['causal_lm_forward']['model_count'], 1)
        self.assertEqual(support.same_task_donor_models.iloc[0], 1)
        self.assertFalse(bool(support.answered.iloc[0]))
        self.assertTrue(np.isnan(support.predicted_load_seconds.iloc[0]))

    def test_new_task_abstains_and_outside_size_range_is_advisory(self):
        bundle = fit_support_bundle(self.main)
        supported = self.main.loc[self.main.workload_task == 'inference'].head(1).copy()
        supported['parameter_gib'] = 1000.0
        predicted = predict_support(bundle, supported)
        self.assertTrue(bool(predicted.answered.iloc[0]))
        self.assertTrue(bool(predicted.outside_same_task_size_range.iloc[0]))
        self.assertGreater(predicted.predicted_load_seconds.iloc[0], 0)
        unknown = supported.copy()
        unknown['workload_task'] = 'unseen_task'
        predicted = predict_support(bundle, unknown)
        self.assertFalse(bool(predicted.answered.iloc[0]))
        self.assertEqual(predicted.same_task_donor_models.iloc[0], 0)

    def test_held_outcomes_do_not_change_support_or_forecast(self):
        held = 'google/bert_uncased_L-4_H-512_A-8'
        donor = self.main.loc[self.main.model_id != held]
        test = self.panels[CAMPAIGNS[2]].loc[
            self.panels[CAMPAIGNS[2]].model_id == held].copy()
        bundle = fit_support_bundle(donor)
        expected = predict_support(bundle, test)
        mutated = test.copy()
        mutated['load_seconds'] *= 1000
        mutated['wall_seconds'] *= 1000
        mutated['latency_p95_ms'] *= 1000
        actual = predict_support(bundle, mutated)
        np.testing.assert_array_equal(expected.answered, actual.answered)
        np.testing.assert_allclose(expected.predicted_load_seconds,
                                   actual.predicted_load_seconds, rtol=0, atol=1e-12)

    def test_model_isolated_folds_and_matched_answered_population(self):
        predictions, splits = evaluate(self.panels)
        self.assertEqual(len(splits), 11)
        main_ids = self.main.set_index('execution_id').model_id.to_dict()
        for split in splits:
            held = set(split['held_model_ids'])
            self.assertFalse(held & set(split['donor_model_ids']))
            self.assertTrue(all(main_ids[execution_id] not in held
                                for execution_id in split['train_execution_ids']))
        _, summary = summarize(predictions)
        selected = summary.loc[(summary.evaluation_group == 'loomo') &
                               (summary.campaign == CAMPAIGNS[0])]
        self.assertEqual(set(selected.method), {
            'donor_model_median', 'size_log_ridge',
            'size_only_extra_trees', 'static_log_ridge',
            'support_aware_size_tree',
        })
        self.assertTrue((selected.answered_models == 8).all())
        self.assertTrue((selected.eligible_models == 9).all())
        self.assertTrue((selected.answered_runs == 512).all())
        self.assertTrue((selected.eligible_runs == 575).all())
        self.assertEqual(selected.abstained_models.nunique(), 1)
        self.assertIn('Qwen', selected.abstained_models.iloc[0])
        self.assertAlmostEqual(float(selected.loc[
            selected.method == 'size_only_extra_trees', 'male_macro'].iloc[0]),
            float(selected.loc[
                selected.method == 'support_aware_size_tree', 'male_macro'].iloc[0]),
            places=12)
        for _name, block in predictions.groupby(['fold', 'campaign', 'execution_id']):
            self.assertEqual(block.answered.nunique(), 1)
            self.assertTrue(block.predicted_load_seconds.isna().all()
                            if not block.answered.iloc[0]
                            else block.predicted_load_seconds.notna().all())

    def test_final_bundle_round_trip(self):
        bundle = fit_support_bundle(self.main)
        example = self.main.head(4)
        original = predict_support(bundle, example)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'support.joblib'
            joblib.dump(bundle, path)
            reloaded = joblib.load(path)
        after = predict_support(reloaded, example)
        self.assertEqual(reloaded['min_same_task_donors'], 2)
        self.assertEqual(len(reloaded['training_model_ids']), 9)
        np.testing.assert_allclose(original.predicted_load_seconds,
                                   after.predicted_load_seconds,
                                   rtol=0, atol=1e-12, equal_nan=True)


if __name__ == '__main__':
    unittest.main()
