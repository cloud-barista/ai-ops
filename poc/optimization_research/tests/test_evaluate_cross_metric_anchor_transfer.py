"""Focused scoring-population tests for the joint-anchor evaluation runner."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_cross_metric_anchor_transfer import (CONFIGS, METHODS,
                                                    primary_unmeasured_error,
                                                    score_decisions,
                                                    summarize_frozen_gate)


def slate(n: int = 9, campaign: str = "khu-hf-local-exhaustive"):
    predicted, measured = [], []
    for group_index in range(n):
        for gpu, cores in CONFIGS:
            execution = f"e{group_index}_{gpu}_{cores}"
            row = {"execution_id": execution, "workload_group": f"g{group_index}",
                   "model_id": "held", "campaign": campaign,
                   "replica_count": gpu, "cpu_core_limit": cores,
                   "latency_slo_ms": 100.0, "target_throughput_rps": 5.0}
            pred = dict(row)
            for method in METHODS:
                pred[f"{method}_latency_log"] = np.log(80.0)
                pred[f"{method}_rps_log"] = np.log(6.0)
            predicted.append(pred)
            measured.append({"execution_id": execution, "latency_p95_ms": 80.0,
                             "examples_per_second": 24.0, "batch_size": 4})
    return pd.DataFrame(predicted), pd.DataFrame(measured)


class CrossMetricEvaluationTests(unittest.TestCase):
    def test_main_primary_excludes_only_eight_paid_anchors(self):
        predicted, measured = slate()
        probes = [f"g{i}" for i in range(8)]
        # A huge error at a measured anchor must disappear; an error at the
        # ninth (unmeasured) anchor must remain in primary but not legacy MALE.
        measured.loc[measured.execution_id.eq("e0_1_2"), "latency_p95_ms"] = 800.0
        measured.loc[measured.execution_id.eq("e8_1_2"), "latency_p95_ms"] = 800.0
        result = primary_unmeasured_error(predicted, measured, probes,
                                          "khu-hf-local-exhaustive",
                                          "joint_cross_metric")
        self.assertEqual(result["unmeasured_candidates"], 64)
        self.assertAlmostEqual(result["unmeasured_latency_male"], np.log(10) / 64)
        self.assertAlmostEqual(result["unmeasured_rps_male"], 0.0)

    def test_external_primary_includes_all_eight_shapes(self):
        predicted, measured = slate(1, "khu-hf-local-novel-matrix")
        measured.loc[measured.execution_id.eq("e0_1_2"), "latency_p95_ms"] = 800.0
        result = primary_unmeasured_error(predicted, measured,
                                          [f"main{i}" for i in range(8)],
                                          "khu-hf-local-novel-matrix",
                                          "joint_cross_metric")
        self.assertEqual(result["unmeasured_candidates"], 8)
        self.assertAlmostEqual(result["unmeasured_latency_male"], np.log(10) / 8)

    def test_decisions_use_eight_candidates_and_oracle_minimum(self):
        predicted, measured = slate(1)
        # 1 GPU / 2 cores fails in reality, but 1 GPU / 4 cores succeeds.
        measured.loc[measured.execution_id.eq("e0_1_2"),
                     "examples_per_second"] = 16.0
        scored = score_decisions(predicted, measured,
                                 [{"scenario": "original"}],
                                 split="loomo", campaign="khu-hf-local-exhaustive",
                                 model_id="held")
        result = scored[scored.method.eq("joint_cross_metric")].iloc[0]
        self.assertEqual(result.workload_queries, 1)
        self.assertEqual(result.partly_feasible, 1)
        self.assertEqual(result.selected, 1)
        self.assertEqual(result.false_approvals, 1)
        self.assertEqual(result.exact_minimal, 0)
        self.assertEqual(result.missed_feasible, 1)

    def test_frozen_gate_uses_shared_eight_primary_rps(self):
        rows = []
        for split, models in (("loomo", [f"encoder{i}" for i in range(8)] +
                               ["Qwen/unsupported"]),
                              ("bert4", ["bert4_target"]),
                              ("bert5", ["bert5_target"])):
            for campaign in ("khu-hf-local-exhaustive",
                             "khu-hf-local-novel-matrix",
                             "khu-hf-local-space-filling"):
                for model in models:
                    for method in ("joint_cross_metric", "joint_no_cross",
                                   "space_kernel_transfer", "space_ridge"):
                        joint = method == "joint_cross_metric"
                        rows.append({
                            "split": split, "campaign": campaign,
                            "heldout_model_id": model, "method": method,
                            "scored_groups": 1, "unmeasured_candidates": 8,
                            "unmeasured_latency_male": 0.2 if joint else 0.3,
                            "unmeasured_rps_male": 0.2 if joint else 0.3,
                            "feasible_groups": 1, "successful_choices": 1,
                            "slo_violations": 0 if joint else 1,
                            "missed_feasible_groups": 0, "exact_minimal": 1,
                        })
        scores = pd.DataFrame(rows)
        encoder_main = (scores.split.eq("loomo") &
                        scores.campaign.eq("khu-hf-local-exhaustive") &
                        scores.heldout_model_id.str.startswith("encoder") &
                        scores.method.eq("joint_cross_metric"))
        scores.loc[encoder_main, "unmeasured_rps_male"] = 0.35
        _, gate = summarize_frozen_gate(scores)
        failures = [check for check in gate["checks"] if not check["passed"]]
        self.assertEqual(gate["decision"], "do_not_adopt")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["metric"], "unmeasured_rps_male")


if __name__ == "__main__":
    unittest.main()
