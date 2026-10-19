"""Small behavioral checks for nested margin learning and matched coverage."""
import unittest
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_asymmetric_margin import (
    learned_margins, matched_indices, policy_margins, validate_manifest,
)


class MarginStudyTests(unittest.TestCase):
    def test_split_manifest_rejects_leaked_outer_or_pseudo(self):
        models = {str(i) for i in range(9)}
        rows = []
        for held in models:
            donors = sorted(models - {held})
            for fold in (0, 1):
                fit = donors[1 - fold::2]
                for pseudo in donors[fold::2]:
                    rows.append({'outer_model_id': held, 'pseudo_model_id': pseudo,
                                 'fit_model_ids': fit, 'probe_groups': list(range(8))})
        validate_manifest(rows, models)
        contaminated = [dict(row) for row in rows]
        contaminated[0]['fit_model_ids'] = [
            contaminated[0]['outer_model_id'], *contaminated[0]['fit_model_ids'][1:]]
        with self.assertRaisesRegex(ValueError, 'leaked'):
            validate_manifest(contaminated, models)
        contaminated = [dict(row) for row in rows]
        contaminated[0]['fit_model_ids'] = [
            contaminated[0]['pseudo_model_id'], *contaminated[0]['fit_model_ids'][1:]]
        with self.assertRaisesRegex(ValueError, 'leaked'):
            validate_manifest(contaminated, models)

    def test_margins_are_donor_only_and_probe_exact(self):
        rows = []
        for model in range(8):
            for high in (False, True):
                for gpu in (1, 2):
                    for i in range(5):
                        rows.append({
                            'model_id': str(model), 'batch_size': 12 if high else 2,
                            'max_length': 512 if high else 128, 'replica_count': gpu,
                            'is_probe': False,
                            'latency_p95_ms': float(np.exp(0.2 + high * .1 + gpu * .02)),
                            'examples_per_second': float(np.exp(2.0 - gpu * .03)),
                            'space_cov_transfer_latency_log': 0.0,
                            'space_cov_transfer_rps_log': 2.0,
                        })
        calibration = pd.DataFrame(rows)
        first = learned_margins(calibration)
        # The unused target row is excluded from the function input entirely.
        target = calibration.iloc[[0]].copy()
        target.loc[:, 'model_id'] = 'target'
        target.loc[:, 'latency_p95_ms'] = 1e12
        with self.assertRaisesRegex(ValueError, 'eight donor'):
            learned_margins(pd.concat([calibration, target],
                                       ignore_index=True))
        slate = pd.DataFrame({
            'batch_size': [2, 12], 'max_length': [128, 512],
            'replica_count': [1, 2], 'is_probe': [True, False]})
        for policy in ('conditional_asymmetric', 'global_asymmetric',
                       'fixed_scalar', 'unadjusted_k8'):
            latency, rps = policy_margins(slate, first, policy)
            self.assertEqual((latency[0], rps[0]), (0.0, 0.0))
            self.assertGreaterEqual(latency[1], 0.0)
            self.assertGreaterEqual(rps[1], 0.0)

    def test_matched_selection_count_uses_only_slack_and_is_deterministic(self):
        slack = np.full((2, 3, 8), -10.0)
        best = [0.3, 0.2, 0.2, 0.1, -0.1, -0.2]
        for i, score in enumerate(best):
            slack.reshape(-1, 8)[i, 4] = score
        chosen, cutoff, ties = matched_indices(slack, 3)
        self.assertEqual(int((chosen >= 0).sum()), 3)
        self.assertEqual(chosen.reshape(-1).tolist(), [4, 4, 4, -1, -1, -1])
        self.assertAlmostEqual(cutoff, 0.2)
        self.assertEqual(ties, 2)
        self.assertTrue(np.array_equal(chosen, matched_indices(slack, 3)[0]))
        with self.assertRaises(ValueError):
            matched_indices(slack, 7)


if __name__ == '__main__':
    unittest.main()
