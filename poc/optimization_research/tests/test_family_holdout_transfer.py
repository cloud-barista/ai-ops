"""Check the exact-family exclusion and fair sparse-probe comparison."""
import json
import sys
from pathlib import Path
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from family_holdout_transfer import FAMILY, FAMILY_WITH_DISTILBERT, family_partition
from sparse_anchor_transfer import CAMPAIGN


class FamilyHoldoutTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = ROOT / 'data/audit_v1/measurements.parquet'
        cls.main = pd.read_parquet(snapshot)
        cls.main = cls.main[(cls.main.campaign == CAMPAIGN) &
                            cls.main.eligible & (cls.main.device == 'cuda')]

    def test_exact_bert_family_never_enters_any_donor_partition(self):
        donors, targets = family_partition(self.main)
        self.assertEqual(set(targets.model_id), set(FAMILY))
        self.assertEqual(len(set(donors.model_id)), 5)
        self.assertTrue(set(donors.model_id).isdisjoint(FAMILY))
        self.assertTrue(set(donors.workload_group).isdisjoint(targets.workload_group))
        # Four independent non-BERT encoders retain the aligned 64-condition grid.
        self.assertEqual(len(set(donors[donors.workload_task == 'inference'].model_id)), 4)
        self.assertEqual(targets.groupby('model_id').workload_group.nunique().to_dict(),
                         {name: 64 for name in FAMILY})

    def test_missing_family_member_rejected(self):
        truncated = self.main[self.main.model_id != FAMILY[0]]
        with self.assertRaisesRegex(ValueError, 'Missing exact BERT family members'):
            family_partition(truncated)

    def test_distilbert_sensitivity_split_excludes_direct_derivative(self):
        donors, targets = family_partition(self.main, FAMILY_WITH_DISTILBERT)
        self.assertEqual(set(targets.model_id), set(FAMILY_WITH_DISTILBERT))
        self.assertEqual(len(set(donors.model_id)), 4)
        self.assertTrue(set(donors.model_id).isdisjoint(FAMILY_WITH_DISTILBERT))
        self.assertEqual(len(set(donors[donors.workload_task == 'inference'].model_id)), 3)

    def test_family_and_standard_holdout_spend_identical_probes(self):
        new_path = ROOT / 'artifacts/family_holdout_transfer_v1/split_manifest.csv'
        original_path = ROOT / 'artifacts/cross_model_covariance_v1/split_manifest.csv'
        if not new_path.exists() or not original_path.exists():
            self.skipTest('Saved evaluation artifacts not present')
        new = pd.read_csv(new_path).set_index('heldout_model_id').sort_index()
        original = pd.read_csv(original_path)
        original = original[(original.budget == 8) &
                            (original.selector == 'space_filling') &
                            original.heldout_model_id.isin(FAMILY)]
        original = original.set_index('heldout_model_id').sort_index()
        self.assertEqual(list(new.index), list(original.index))
        self.assertEqual([json.loads(x) for x in new.probe_groups_json],
                         [json.loads(x) for x in original.probe_groups_json])
        pd.testing.assert_series_equal(new.gpu_wall_probe_seconds,
                                       original.gpu_wall_probe_seconds,
                                       check_names=False)
        for raw in new.training_model_ids_json:
            self.assertTrue(set(json.loads(raw)).isdisjoint(FAMILY))

    def test_distilbert_sensitivity_uses_same_probes_and_costs(self):
        new_path = ROOT / 'artifacts/family_holdout_plus_distilbert_v1/split_manifest.csv'
        original_path = ROOT / 'artifacts/cross_model_covariance_v1/split_manifest.csv'
        if not new_path.exists() or not original_path.exists():
            self.skipTest('Saved evaluation artifacts not present')
        new = pd.read_csv(new_path).set_index('heldout_model_id').sort_index()
        original = pd.read_csv(original_path)
        original = original[(original.budget == 8) &
                            (original.selector == 'space_filling') &
                            original.heldout_model_id.isin(FAMILY_WITH_DISTILBERT)]
        original = original.set_index('heldout_model_id').sort_index()
        self.assertEqual(list(new.index), list(original.index))
        self.assertEqual([json.loads(x) for x in new.probe_groups_json],
                         [json.loads(x) for x in original.probe_groups_json])
        pd.testing.assert_series_equal(new.gpu_wall_probe_seconds,
                                       original.gpu_wall_probe_seconds,
                                       check_names=False)
        for raw in new.training_model_ids_json:
            self.assertTrue(set(json.loads(raw)).isdisjoint(FAMILY_WITH_DISTILBERT))


if __name__ == '__main__':
    unittest.main()
