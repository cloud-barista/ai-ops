"""Time and leakage checks for the repeated-confirmation audit."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_repeat_confirmation import prepare_version, replay_version


def fixture() -> pd.DataFrame:
    records = []
    # Run names are deliberately not in chronological order.
    run_ids = ["r9", "r1", "r6", "r2", "r5"]
    a_latency = [10.0, 20.0, 15.0, 100.0, 15.0]
    a_rps = [100.0, 90.0, 95.0, 10.0, 95.0]
    for t, run_id in enumerate(run_ids):
        for cell, latency, rps in (("a", a_latency[t], a_rps[t]),
                                   ("b", 10.0, 100.0)):
            records.append({
                "collector_version": "v", "run_id": run_id,
                "condition_group": cell, "model_id": cell,
                "started_at": f"2026-01-01T{t:02d}:00:00Z",
                "finished_at": f"2026-01-01T{t:02d}:00:01Z",
                "latency_p95_ms": latency, "examples_per_second": rps,
                "batch_size": 1, "latency_slo_ms": 100.0,
                "target_throughput_rps": 50.0,
            })
    return pd.DataFrame(records)


class RepeatConfirmationTests(unittest.TestCase):
    def test_chronology_and_prefix_target_exclude_future(self):
        rows = fixture().sample(frac=1, random_state=42).reset_index(drop=True)
        prepared = prepare_version(rows, expected_cells=2, calibration_rounds=2)
        self.assertEqual(prepared[prepared.condition_group.eq("a")]
                         .sort_values("round_index").run_id.tolist(),
                         ["r9", "r1", "r6", "r2", "r5"])
        result = replay_version(rows, expected_cells=2, calibration_rounds=2)
        part = result[(result.condition_group == "a") &
                      (result.scenario == "prefix_q90_latency_q10_rps")]
        self.assertEqual(part.round_index.tolist(), [2, 3, 4])
        self.assertTrue((part.target_latency_ms == 19.0).all())
        self.assertTrue((part.target_rps == 91.0).all())
        self.assertEqual(part["pass"].tolist(), [1, 0, 1])
        self.assertEqual(part.accept_last_1.tolist(), [False, True, False])
        self.assertEqual(part.accept_last_2.tolist(), [False, False, False])
        # A changed final outcome cannot alter the already issued decisions.
        changed = rows.copy()
        changed.loc[(changed.run_id == "r5") & (changed.condition_group == "a"),
                    "latency_p95_ms"] = 1000.0
        after = replay_version(changed, expected_cells=2, calibration_rounds=2)
        a = after[(after.condition_group == "a") &
                  (after.scenario == "prefix_q90_latency_q10_rps")]
        self.assertEqual(a.target_latency_ms.tolist(), part.target_latency_ms.tolist())
        self.assertEqual(a.accept_last_1.tolist(), part.accept_last_1.tolist())
        self.assertEqual(a["pass"].tolist(), [1, 0, 0])

    def test_rejects_incomplete_or_overlapping_rounds(self):
        rows = fixture()
        with self.assertRaisesRegex(ValueError, "incomplete run"):
            prepare_version(rows[~((rows.run_id == "r6") & (rows.condition_group == "b"))],
                            expected_cells=2, calibration_rounds=2)
        overlapping = rows.copy()
        overlapping.loc[overlapping.run_id.eq("r1"), "started_at"] = "2026-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "overlap"):
            prepare_version(overlapping, expected_cells=2, calibration_rounds=2)


if __name__ == "__main__":
    unittest.main()
