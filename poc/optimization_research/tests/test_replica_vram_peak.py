"""Physical pair, unit, and target-leakage tests for GPU2 peak research."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_replica_vram_peak as study


ROOT = Path(__file__).resolve().parents[1]


class ZeroResidualModel:
    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(features))


class ReplicaVramPeakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = pd.read_parquet(ROOT / "data/audit_v1/measurements.parquet")
        main = cls.snapshot.loc[(cls.snapshot.campaign == study.CAMPAIGNS[0]) &
                                cls.snapshot.eligible & cls.snapshot.device.eq("cuda")]
        first_row = main.loc[main.replica_count == 1].iloc[0]
        same = np.ones(len(main), dtype=bool)
        for key in study.PAIR_KEY:
            same &= main[key].eq(first_row[key]).to_numpy()
        cls.two_rows = main.loc[same].copy()
        assert len(cls.two_rows) == 2
        cls.gpu1 = cls.two_rows.loc[cls.two_rows.replica_count == 1].iloc[0].to_dict()
        cls.gpu2 = cls.two_rows.loc[cls.two_rows.replica_count == 2].iloc[0].to_dict()

    def test_all_three_local_campaigns_have_unique_complete_pairs(self) -> None:
        for campaign, expected in study.EXPECTED_PAIRS.items():
            pairs = study.extract_pairs(self.snapshot, campaign, expected)
            self.assertEqual(len(pairs), expected)
            self.assertFalse(pairs.duplicated(list(study.PAIR_KEY)).any())
            self.assertTrue((pairs.gpu2_max_peak_mib >= pairs.gpu2_min_peak_mib).all())

    def test_duplicate_or_missing_partner_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate candidate"):
            study.extract_pairs(pd.concat([self.two_rows, self.two_rows.iloc[[0]]]),
                                study.CAMPAIGNS[0])
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            study.extract_pairs(self.two_rows.iloc[[0]], study.CAMPAIGNS[0])

    def test_gpu2_label_uses_max_per_device_independent_of_result_order(self) -> None:
        unequal = self.snapshot.loc[(self.snapshot.campaign == study.CAMPAIGNS[0]) &
                                    self.snapshot.eligible & self.snapshot.device.eq("cuda") &
                                    self.snapshot.replica_count.eq(2)].copy()
        unequal = unequal.loc[unequal.result_json.map(
            lambda raw: len(set(r["gpu_memory_reserved_bytes"] for r in
                                json.loads(raw)["replica_results"])) == 2)]
        self.assertEqual(len(unequal), 39)
        second = unequal.iloc[0].to_dict()
        main = self.snapshot.loc[(self.snapshot.campaign == study.CAMPAIGNS[0]) &
                                 self.snapshot.eligible & self.snapshot.device.eq("cuda") &
                                 self.snapshot.replica_count.eq(1)]
        first = main
        for key in study.PAIR_KEY:
            first = first.loc[first[key] == second[key]]
        self.assertEqual(len(first), 1)
        first = first.iloc[0].to_dict()
        actual = study._pair_to_row(first, second)
        result = json.loads(second["result_json"])
        peaks = [r["gpu_memory_reserved_bytes"] for r in result["replica_results"]]
        self.assertEqual(actual["gpu2_max_peak_mib"], max(peaks) / study.MIB)
        self.assertNotEqual(actual["gpu2_max_peak_mib"], second["gpu_memory_reserved_bytes"] / 2 / study.MIB)
        result["replica_results"].reverse()
        second["result_json"] = json.dumps(result)
        swapped = study._pair_to_row(first, second)
        self.assertEqual(actual["gpu2_max_peak_mib"], swapped["gpu2_max_peak_mib"])

    def test_bad_units_gpu_indices_and_time_fail(self) -> None:
        row = dict(self.gpu2)
        row["gpu_memory_reserved_bytes"] += int(study.MIB)
        with self.assertRaisesRegex(ValueError, "sum"):
            study._pair_to_row(self.gpu1, row)
        row = dict(self.gpu2)
        result = json.loads(row["result_json"])
        result["replica_results"][1]["gpu_index"] = 0
        row["result_json"] = json.dumps(result)
        with self.assertRaisesRegex(ValueError, "indices"):
            study._pair_to_row(self.gpu1, row)
        row = dict(self.gpu2)
        row["started_at"] = self.gpu1["finished_at"]
        with self.assertRaisesRegex(ValueError, "finish before"):
            study._pair_to_row(self.gpu1, row)

    def test_gpu2_outcome_cannot_change_features_or_predictions(self) -> None:
        pair = study.extract_pairs(self.two_rows, study.CAMPAIGNS[0], 1)
        changed = pair.copy()
        changed["gpu2_max_peak_mib"] *= 1000
        changed["gpu2_min_peak_mib"] *= 1000
        changed["gpu2_execution_id"] = "changed-target-id"
        pd.testing.assert_frame_equal(study.feature_matrix(pair), study.feature_matrix(changed))
        original = study.predict_fold(pair, 0.0, ZeroResidualModel())
        later = study.predict_fold(changed, 0.0, ZeroResidualModel())
        for method in study.METHODS:
            np.testing.assert_array_equal(original[f"pred_{method}_mib"],
                                          later[f"pred_{method}_mib"])
        self.assertTrue(all("gpu2" not in column for column in study.FEATURE_COLUMNS))


if __name__ == "__main__":
    unittest.main()
