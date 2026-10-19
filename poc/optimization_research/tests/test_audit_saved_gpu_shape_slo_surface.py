"""Analytic boundary checks for the retrospective GPU SLO surface."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_saved_gpu_shape_decisions import CONFIGS
from audit_saved_gpu_shape_slo_surface import (
    PRIOR_BOOLEAN_FIELDS, _check_prior_bool_types, _point_record,
    _slate_arrays, boundary_grid, score_surface,
)


def paired_candidate_fixture() -> pd.DataFrame:
    """Two viable shapes separate wrong approval from feasible abstention."""
    rows = []
    for gpu, cores in CONFIGS:
        first = (gpu, cores) == (1, 2)
        second = (gpu, cores) == (1, 4)
        rows.append({
            "campaign": "test", "host": "h", "model_id": "m", "workload_group": "g",
            "replica_count": gpu, "cpu_core_limit": cores, "batch_size": 4,
            "latency_slo_ms": 100.0, "target_throughput_rps": 10.0,
            "latency_p95_ms": 100.0 if first or second else 200.0,
            "examples_per_second": 40.0 if first else 48.0 if second else 4.0,
            "predicted_latency_p95_ms": 100.0 if first or second else 200.0,
            "predicted_examples_per_second": 48.0 if first or second else 4.0,
            "baseline_latency_p95_ms": 100.0 if first or second else 200.0,
            "baseline_examples_per_second": 40.0 if first else 48.0 if second else 4.0,
        })
    return pd.DataFrame(rows)


class ContinuousSurfaceTests(unittest.TestCase):
    def test_exact_log_area_distinguishes_wrong_approval_and_abstain(self):
        slate = paired_candidate_fixture()
        full = math.log(1.25 / 0.75) ** 2
        disagreement = math.log(1.25) * math.log(1.2) / full
        actual_possible = math.log(1.25) * math.log(1.2 / 0.75) / full
        stored = score_surface(slate, "stored_model")
        median = score_surface(slate, "model_device_median")
        fixed = score_surface(slate, "fixed_gpu1_cpu2_median")
        self.assertAlmostEqual(stored["area_feasible_wrong_approval"], disagreement)
        self.assertAlmostEqual(stored["area_partial_wrong_approval"], disagreement)
        self.assertAlmostEqual(stored["area_false_approval"], disagreement)
        self.assertAlmostEqual(stored["area_feasible_abstain"], 0.0)
        self.assertAlmostEqual(median["area_false_approval"], 0.0)
        self.assertAlmostEqual(median["area_success"], actual_possible)
        self.assertAlmostEqual(fixed["area_feasible_abstain"], disagreement)
        self.assertAlmostEqual(fixed["area_partial_abstain"], disagreement)
        self.assertAlmostEqual(fixed["area_feasible_wrong_approval"], 0.0)
        self.assertAlmostEqual(stored["area_oracle_feasible"], actual_possible)
        self.assertAlmostEqual(stored["area_partial_feasible"], actual_possible)
        self.assertAlmostEqual(stored["area_zero_feasible"], 1.0 - actual_possible)
        self.assertAlmostEqual(stored["area_all_eight_feasible"], 0.0)

    def test_inclusive_nine_points_and_out_of_domain_boundaries(self):
        arrays = _slate_arrays(paired_candidate_fixture())
        x, y = boundary_grid(arrays)
        self.assertEqual(x.tolist(), [0.75, 1.0, 1.25])
        self.assertEqual(y.tolist(), [0.75, 1.0, 1.2, 1.25])
        at_exact_target = _point_record(arrays, "stored_model", 1.0, 1.0)
        at_wrong_boundary = _point_record(arrays, "stored_model", 1.0, 1.2)
        just_before_latency = _point_record(arrays, "stored_model", 0.999, 1.0)
        self.assertTrue(at_exact_target["true_success"])
        self.assertEqual((at_exact_target["selected_gpu"], at_exact_target["selected_cores"]),
                         (1, 2))
        self.assertTrue(at_wrong_boundary["false_approval"])
        self.assertTrue(at_wrong_boundary["oracle_feasible"])
        self.assertFalse(just_before_latency["selected"])

    def test_rejects_incomplete_or_bad_measurement_input(self):
        slate = paired_candidate_fixture()
        with self.assertRaisesRegex(ValueError, "eight_candidate"):
            score_surface(slate.iloc[:-1], "stored_model")
        bad = slate.copy()
        bad.loc[0, "batch_size"] = 0
        with self.assertRaisesRegex(ValueError, "nonpositive"):
            score_surface(bad, "stored_model")

    def test_prior_false_string_cannot_pass_as_truthy_boolean(self):
        prior = pd.DataFrame({field: [False] for field in PRIOR_BOOLEAN_FIELDS})
        _check_prior_bool_types(prior)
        prior["selected"] = prior["selected"].astype(object)
        prior.loc[0, "selected"] = "False"
        with self.assertRaisesRegex(ValueError, "prior_boolean_column_not_boolean"):
            _check_prior_bool_types(prior)


if __name__ == "__main__":
    unittest.main()
