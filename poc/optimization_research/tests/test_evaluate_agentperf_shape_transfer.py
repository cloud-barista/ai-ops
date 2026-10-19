"""Contract tests for the frozen AgentPerfBench shape-transfer evaluation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_agentperf_shape_transfer as evaluation


class SourceAndPairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evaluation.verify_source(evaluation.DEFAULT_SOURCE)
        raw = pd.read_parquet(evaluation.DEFAULT_SOURCE)
        cls.clean, cls.clean_stats = evaluation.clean_source(raw)
        cls.selected, cls.selection = evaluation.select_comparable_groups(cls.clean, 1.00001, 1.05)
        cls.pairs = evaluation.build_pairs(cls.selected)

    def test_pinned_source_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.parquet"
            path.write_bytes(b"changed source")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                evaluation.verify_source(path)

    def test_strict_population_and_expected_models(self):
        self.assertEqual(self.clean_stats["source_rows"], 2932)
        self.assertEqual(self.clean_stats["all_success_positive_rows"], 2172)
        self.assertEqual(self.selection["groups_with_two_or_more_shapes"], 551)
        self.assertEqual(self.selection["selected_groups"], 485)
        self.assertEqual(self.selection["selected_rows"], 1526)
        self.assertEqual(len(self.pairs), 1041)
        self.assertEqual(self.pairs.model.nunique(), 9)
        self.assertEqual(self.pairs.model_family.nunique(), 4)

    def test_anchor_is_deterministic_and_never_target(self):
        for _, group in self.pairs.groupby("group_id"):
            observed = {group.iloc[0].anchor_hardware} | set(group.target_hardware)
            self.assertEqual(group.iloc[0].anchor_hardware,
                             min(observed, key=evaluation.hardware_order))
            self.assertFalse((group.anchor_hardware == group.target_hardware).any())
        self.assertEqual(evaluation.nominal_size_b("Mixtral-8x7B"), 56.0)
        self.assertEqual(evaluation.nominal_size_b("gpt-oss-120b"), 120.0)

    def test_target_outcome_change_only_changes_label(self):
        selected = self.selected.copy()
        baseline = evaluation.build_pairs(selected)
        key = json.loads(baseline.iloc[0].group_id)
        mask = pd.Series(True, index=selected.index)
        for column, value in zip(evaluation.GROUP_KEYS, key):
            mask &= selected[column].astype(str).eq(value)
        mask &= selected.hardware.eq(baseline.iloc[0].target_hardware)
        self.assertEqual(int(mask.sum()), 1)
        selected.loc[mask, "p90_e2el_ms"] *= 1.3
        selected.loc[mask, "request_throughput"] *= 1.2
        changed = evaluation.build_pairs(selected)
        pd.testing.assert_frame_equal(baseline[evaluation.FEATURE_COLUMNS],
                                      changed[evaluation.FEATURE_COLUMNS])
        self.assertNotEqual(baseline.iloc[0].target_log_e2el, changed.iloc[0].target_log_e2el)
        self.assertNotEqual(baseline.iloc[0].target_log_rps, changed.iloc[0].target_log_rps)
        self.assertFalse(set(evaluation.FEATURE_COLUMNS) & {
            "model", "run_id", "duration_s", "input_token_throughput",
            "output_token_throughput", "target_log_e2el", "target_log_rps",
        })

    def test_every_model_and_family_split_excludes_test_from_train(self):
        for kind in ("model", "family"):
            seen = []

            def inspect_fold(train, test):
                self.assertFalse(set(train.model) & set(test.model))
                if kind == "family":
                    self.assertFalse(set(train.model_family) & set(test.model_family))
                seen.append(set(test.model))
                return test[["group_id", "model", "model_family"]].copy()

            with patch.object(evaluation, "predict_fold", side_effect=inspect_fold):
                result = evaluation.evaluate_folds(self.pairs, kind)
            self.assertEqual(len(result), len(self.pairs))
            self.assertEqual(len(seen), 9 if kind == "model" else 4)
            self.assertEqual(set.union(*seen), set(self.pairs.model))


class ScoringTests(unittest.TestCase):
    def test_donor_never_reads_test_labels(self):
        train = pd.DataFrame([
            {"anchor_hardware": "3090", "target_hardware": "3090x2",
             "anchor_gpu_count": 1, "target_gpu_count": 2,
             "anchor_log_rps": 0.0, "target_log_rps": 1.0},
            {"anchor_hardware": "3090", "target_hardware": "3090x2",
             "anchor_gpu_count": 1, "target_gpu_count": 2,
             "anchor_log_rps": 0.0, "target_log_rps": 2.0},
        ])
        test = train.iloc[:1].copy()
        expected = evaluation.donor_predict(train, test, "rps")
        test.loc[:, "target_log_rps"] = 1000.0
        np.testing.assert_array_equal(expected, evaluation.donor_predict(train, test, "rps"))

    def test_synthetic_choice_counts_false_approval_under_log_median(self):
        row = {
            "fold_kind": "model", "group_id": "group", "model": "test",
            "anchor_hardware": "H100", "target_hardware": "H100x2",
            "anchor_gpu_type": "H100", "target_gpu_type": "H100",
            "anchor_gpu_count": 1, "target_gpu_count": 2,
            "anchor_log_e2el": np.log(100.0), "target_log_e2el": np.log(50.0),
            "anchor_log_rps": np.log(20.0), "target_log_rps": np.log(10.0),
            "anchor_raw_e2el": 100.0, "target_raw_e2el": 50.0,
            "anchor_raw_rps": 20.0, "target_raw_rps": 10.0,
        }
        for method in evaluation.METHODS:
            row[f"pred_{method}_e2el"] = np.log(40.0)
            row[f"pred_{method}_rps"] = np.log(30.0)
        outcome = evaluation.synthetic_choices(pd.DataFrame([row]))
        self.assertEqual(len(outcome["scenarios"]), 1)
        self.assertEqual(outcome["summary"]["direct_et"]["false_approval"], 1)
        self.assertEqual(outcome["summary"]["direct_et"]["predicted_approval"], 1)
        self.assertAlmostEqual(outcome["scenarios"][0]["latency_log_limit"],
                               np.log(np.sqrt(5000.0)))
        self.assertFalse(outcome["scenarios"][0]["actual_any_feasible"])
        raw = evaluation.synthetic_choices(pd.DataFrame([row]), threshold_scale="raw")
        self.assertAlmostEqual(raw["scenarios"][0]["latency_log_limit"], np.log(75.0))


if __name__ == "__main__":
    unittest.main()
