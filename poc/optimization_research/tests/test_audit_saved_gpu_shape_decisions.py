"""Contract checks for the frozen-prediction GPU shape decision audit."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_saved_gpu_shape_decisions import (CONFIGS, PRIMARY, score_slate,
                                             summarize, validated_gpu_rows)


def fixture():
    measured = []
    saved = []
    manifest = []
    for gpu, cores in CONFIGS:
        execution = f"e{gpu}_{cores}"
        # The one-GPU/two-core candidate fails 5 requests/s because
        # 16 examples/s at batch 4 is only 4 requests/s.
        examples = 16.0 if (gpu, cores) == (1, 2) else 24.0
        original = {
            "host": "h", "execution_id": execution,
            "campaign": PRIMARY, "model_id": "app", "device": "cuda",
            "workload_group": "g", "latency_p95_ms": 80.0,
            "examples_per_second": examples,
        }
        measured.append({**original, "eligible": True, "replica_count": gpu,
                         "cpu_core_limit": cores, "batch_size": 4,
                         "latency_slo_ms": 100.0, "target_throughput_rps": 5.0})
        saved.append({**original, "evaluation": "test",
                      "predicted_latency_p95_ms": 80.0,
                      "predicted_examples_per_second": examples,
                      "baseline_latency_p95_ms": 80.0,
                      "baseline_examples_per_second": 24.0})
        manifest.append({"host": "h", "execution_id": execution, "split": "test"})
    return pd.DataFrame(measured), pd.DataFrame(saved), pd.DataFrame(manifest)


class SavedDecisionAuditTests(unittest.TestCase):
    def test_join_and_request_per_second_selection(self):
        physical, predictions, manifest = fixture()
        joined = validated_gpu_rows(physical, predictions, manifest)
        row = score_slate(joined, "stored_model", "original", 1.0, 1.0)
        self.assertEqual((row["oracle_gpu"], row["oracle_cores"]), (1, 4))
        self.assertEqual((row["selected_gpu"], row["selected_cores"]), (1, 4))
        self.assertEqual(row["oracle_feasible_configs"], 7)
        self.assertTrue(row["correct_min_shape"])
        self.assertAlmostEqual(row["minimum_predicted_relative_margin"], 0.2)
        self.assertEqual(row["margin_band"], "0.10_to_lt_0.25")

    def test_resource_order_abstention_and_wrong_approval(self):
        physical, predictions, manifest = fixture()
        predictions.loc[predictions.execution_id.eq("e1_2"),
                        "predicted_examples_per_second"] = 24.0
        slate = validated_gpu_rows(physical, predictions, manifest)
        wrong = score_slate(slate, "stored_model", "original", 1.0, 1.0)
        self.assertTrue(wrong["selected"])
        self.assertTrue(wrong["false_approval"])
        self.assertTrue(wrong["missed_feasible"])
        self.assertFalse(wrong["correct_min_shape"])
        self.assertEqual((wrong["selected_gpu"], wrong["selected_cores"]), (1, 2))
        predictions.loc[:, "predicted_examples_per_second"] = 1.0
        slate = validated_gpu_rows(physical, predictions, manifest)
        abstain = score_slate(slate, "stored_model", "original", 1.0, 1.0)
        self.assertFalse(abstain["selected"])
        self.assertTrue(abstain["missed_feasible"])
        self.assertIsNone(abstain["minimum_predicted_relative_margin"])
        # Prefer a one-GPU high-core cell over a two-GPU low-core cell.
        predictions.loc[:, "predicted_examples_per_second"] = 1.0
        predictions.loc[predictions.execution_id.isin(["e1_16", "e2_2"]),
                        "predicted_examples_per_second"] = 24.0
        slate = validated_gpu_rows(physical, predictions, manifest)
        chosen = score_slate(slate, "stored_model", "original", 1.0, 1.0)
        self.assertEqual((chosen["selected_gpu"], chosen["selected_cores"]), (1, 16))

    def test_sensitivity_is_a_new_query_on_the_same_slate(self):
        physical, predictions, manifest = fixture()
        slate = validated_gpu_rows(physical, predictions, manifest)
        original = score_slate(slate, "stored_model", "original", 1.0, 1.0)
        strict = score_slate(slate, "stored_model", "strict", 0.75, 1.25)
        self.assertEqual(original["oracle_feasible_configs"], 7)
        self.assertEqual(strict["oracle_feasible_configs"], 0)
        self.assertFalse(strict["selected"])
        self.assertEqual(strict["latency_target_ms"], 75.0)
        self.assertEqual(strict["target_rps"], 6.25)

    def test_rejects_missing_cell_inconsistent_slo_bad_join_and_non_test(self):
        physical, predictions, manifest = fixture()
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            validated_gpu_rows(physical, predictions.iloc[:-1], manifest)
        with self.assertRaisesRegex(ValueError, "incomplete or duplicated"):
            validated_gpu_rows(physical.iloc[:-1], predictions.iloc[:-1],
                               manifest.iloc[:-1])
        with self.assertRaisesRegex(ValueError, "disagree"):
            altered = predictions.copy()
            altered.loc[0, "examples_per_second"] += 1
            validated_gpu_rows(physical, altered, manifest)
        with self.assertRaisesRegex(ValueError, "changes within"):
            altered = physical.copy()
            altered.loc[0, "target_throughput_rps"] = 6.0
            validated_gpu_rows(altered, predictions, manifest)
        with self.assertRaisesRegex(ValueError, "non-test"):
            altered = manifest.copy()
            altered.loc[0, "split"] = "train"
            validated_gpu_rows(physical, predictions, altered)
        with self.assertRaisesRegex(ValueError, "positive finite"):
            altered = predictions.copy()
            altered.loc[0, "predicted_latency_p95_ms"] = 0
            validated_gpu_rows(physical, altered, manifest)

    def test_summary_accounting_and_margin_partition(self):
        physical, predictions, manifest = fixture()
        slate = validated_gpu_rows(physical, predictions, manifest)
        rows = []
        for method in ("stored_model", "model_device_median"):
            rows.append(score_slate(slate, method, "original", 1.0, 1.0))
            rows.append(score_slate(slate, method, "sensitivity_l1.00_r1.00", 1.0, 1.0))
        summary = summarize(pd.DataFrame(rows))
        self.assertEqual(len([r for r in summary["original"] if r["scope"] == "all"]), 2)
        self.assertEqual(len([r for r in summary["synthetic_by_target"] if r["scope"] == "all"]), 2)
        self.assertEqual(sum(r["groups"] for r in summary["selected_margin_bands"]
                             if r["scope"] == "all"), 2)


if __name__ == "__main__":
    unittest.main()
