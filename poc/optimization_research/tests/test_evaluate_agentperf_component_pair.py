"""Non-leakage and pairing checks for the frozen A100 component study."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_agentperf_component_pair as evaluation


class PairContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evaluation.verify_frozen(evaluation.SOURCE, evaluation.PROTOCOL)
        cls.pairs = evaluation.build_pairs(pd.read_parquet(evaluation.SOURCE))

    def test_pinned_bytes_reject_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "changed.parquet"
            bad.write_bytes(b"different bytes")
            with self.assertRaisesRegex(ValueError, "frozen SHA-256 mismatch"):
                evaluation.verify_frozen(bad, evaluation.PROTOCOL)

    def test_exact_pairs_and_sensitivity_support(self):
        pairs = self.pairs
        self.assertEqual(len(pairs), 130)
        self.assertEqual(pairs.model.nunique(), 5)
        self.assertEqual(pairs.profile.nunique(), 7)
        self.assertEqual(set(pairs.engine), {"vllm"})
        self.assertFalse(pairs.duplicated(evaluation.KEYS).any())
        self.assertEqual(int(pairs.output_token_ratio.le(1.05).sum()), 127)
        self.assertEqual(int(pairs.output_token_ratio.le(1.01).sum()), 83)
        self.assertEqual(int((pairs.delta_ttft * pairs.delta_tpot < 0).sum()), 36)

    def test_other_hardware_outcomes_cannot_change_pair_population(self):
        raw = pd.read_parquet(evaluation.SOURCE)
        unrelated = raw.hardware.eq("H100")
        self.assertGreater(int(unrelated.sum()), 0)
        raw.loc[unrelated, "p90_ttft_ms"] = 0.0
        changed = evaluation.build_pairs(raw)
        pd.testing.assert_frame_equal(self.pairs, changed)

    def test_target_outcome_and_output_ratio_are_never_features(self):
        pairs = self.pairs
        forbidden = {
            "model", "run_id", "duration_s", "target_log_ttft", "target_log_tpot",
            "delta_ttft", "delta_tpot", "output_token_ratio",
            "output_token_throughput_b", "p90_ttft_ms_b", "p90_tpot_ms_b",
        }
        self.assertFalse(forbidden.intersection(evaluation.FEATURES))
        train = pairs.loc[pairs.model.ne("Llama-3.1-8B")]
        test = pairs.loc[pairs.model.eq("Llama-3.1-8B")]
        base_train, base_test = evaluation._matrix(train, test, scaled=True)
        changed = test.copy()
        changed["target_log_ttft"] += 10.0
        changed["target_log_tpot"] -= 10.0
        changed["delta_ttft"] += 10.0
        changed["delta_tpot"] -= 10.0
        changed["output_token_ratio"] *= 10.0
        alt_train, alt_test = evaluation._matrix(train, changed, scaled=True)
        np.testing.assert_array_equal(base_train, alt_train)
        np.testing.assert_array_equal(base_test, alt_test)

    def test_model_and_llama_family_folds_are_disjoint(self):
        calls = []

        def inspect_fold(train, test, heldout):
            self.assertFalse(set(train.model) & set(test.model))
            if heldout == "llama_family":
                self.assertFalse(set(train.model_family) & set(test.model_family))
            calls.append((heldout, set(test.model)))
            return test[evaluation.KEYS].copy()

        with patch.object(evaluation, "predict_fold", side_effect=inspect_fold):
            result = evaluation.evaluate_lomo(self.pairs)
            family = evaluation.evaluate_llama_family(self.pairs)
        self.assertEqual(len(result), 130)
        self.assertEqual(len(family), 50)
        self.assertEqual(len(calls), 6)
        self.assertEqual(set.union(*(models for held, models in calls if held != "llama_family")),
                         set(evaluation.MODEL_META))

    def test_test_label_mutation_does_not_change_prediction(self):
        train = self.pairs.loc[self.pairs.model.ne("gpt-oss-20b")]
        test = self.pairs.loc[self.pairs.model.eq("gpt-oss-20b")]
        baseline = evaluation.predict_fold(train, test, "gpt-oss-20b")
        changed = test.copy()
        changed["target_log_ttft"] += 2.0
        changed["target_log_tpot"] -= 2.0
        changed["delta_ttft"] += 2.0
        changed["delta_tpot"] -= 2.0
        changed["output_token_ratio"] *= 2.0
        second = evaluation.predict_fold(train, changed, "gpt-oss-20b")
        columns = [f"pred_{metric}__{method}"
                   for metric in evaluation.METRICS for method in evaluation.METHODS]
        pd.testing.assert_frame_equal(baseline[columns], second[columns])


class ConflictScoringTests(unittest.TestCase):
    def test_macro_male_weights_models_equally(self):
        rows = pd.DataFrame({
            "model": ["small", "large", "large", "large"],
            "target_log_ttft": [0.0] * 4,
            "target_log_tpot": [0.0] * 4,
        })
        for metric in evaluation.METRICS:
            for method in evaluation.METHODS:
                rows[f"pred_{metric}__{method}"] = [1.0, 0.0, 0.0, 0.0]
        per_model, macro = evaluation.male_scores(rows, "synthetic")
        row = macro.loc[macro.method.eq("phase_coupled")].iloc[0]
        self.assertEqual(row.models, 2)
        self.assertEqual(row.rows, 4)
        self.assertAlmostEqual(row.ttft_male, 0.5)
        self.assertAlmostEqual(row.tpot_male, 0.5)
        self.assertEqual(len(per_model), 2 * len(evaluation.METHODS))

    def test_conflict_counts_cover_true_and_false_predictions(self):
        rows = pd.DataFrame([
            {"model": "test", "anchor_log_ttft": 0.0, "anchor_log_tpot": 0.0,
             "delta_ttft": -0.2, "delta_tpot": 0.2},
            {"model": "test", "anchor_log_ttft": 0.0, "anchor_log_tpot": 0.0,
             "delta_ttft": -0.2, "delta_tpot": -0.2},
        ])
        for method in evaluation.METHODS:
            rows[f"pred_ttft__{method}"] = [-0.3, -0.3]
            rows[f"pred_tpot__{method}"] = [0.3, 0.3]
        scores = evaluation.conflict_scores(rows, "synthetic")
        one = scores.loc[scores.model.eq("test") & scores.method.eq("phase_coupled")
                         & scores.conflict_kind.eq("any")].iloc[0]
        self.assertEqual((one.tp, one.fp, one.fn, one.tn), (1, 1, 0, 0))
        self.assertEqual(one.recall, 1.0)
        self.assertEqual(one.false_alarm_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
