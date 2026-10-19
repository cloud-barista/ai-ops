"""Decision-time, gap, split-boundary, and feedback tests for SwissAI demand."""

from __future__ import annotations

import hashlib
import importlib.util
import math
from pathlib import Path
import sys
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("evaluate_swissai_demand", SCRIPTS / "evaluate_swissai_demand.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SwissAIDemandTests(unittest.TestCase):
    def test_model_selection_uses_only_train_and_validation_arrivals(self) -> None:
        original = MODULE.EXPECTED_ALIASES
        try:
            MODULE.EXPECTED_ALIASES = tuple(hashlib.sha256(x.encode()).hexdigest()[:10] for x in ("A", "B"))
            audit = {"by_model_day": {
                "2025-02-01 | A": {"rows": 5_000}, "2025-06-20 | A": {"rows": 20_000},
                "2025-02-01 | B": {"rows": 6_000}, "2025-06-20 | B": {"rows": 10_000},
                "2025-02-01 | C": {"rows": 4_999}, "2025-06-20 | C": {"rows": 1_000_000},
                "2025-07-20 | C": {"rows": 9_000_000},
            }}
            self.assertEqual(MODULE.select_models(audit), ("A", "B"))
        finally:
            MODULE.EXPECTED_ALIASES = original

    def test_known_gap_only_blocks_after_its_ending_arrival(self) -> None:
        end = MODULE.T0 + 5 * MODULE.HOUR_US
        blocked = MODULE.causal_restart_mask([end], len(np.zeros(120)))
        before = (end - MODULE.T0) // MODULE.BIN_US - 1
        after = (end - MODULE.T0) // MODULE.BIN_US
        self.assertFalse(blocked[before])
        self.assertTrue(blocked[after])
        self.assertTrue(blocked[after + 23])
        self.assertFalse(blocked[after + 24])

    def test_active_decision_and_future_window_stay_on_correct_side_of_t(self) -> None:
        bins = np.zeros(MODULE.N_BINS, dtype=np.int64)
        own = {alias: np.zeros(MODULE.N_BINS, dtype=np.int64) for alias in MODULE.ALIASES}
        alias = MODULE.ALIASES[0]
        # Decision idx 24 sees idx 23 as history, idx 24--25 as target,
        # and idx 26 only at the next decision's target.
        bins[23] = own[alias][23] = 1
        bins[24] = own[alias][24] = 1
        bins[26] = own[alias][26] = 1
        decisions, audit = MODULE.split_decisions(bins, own, [])
        group = decisions[alias]
        at24 = int(np.flatnonzero(group["idx"] == 24)[0])
        at25 = int(np.flatnonzero(group["idx"] == 25)[0])
        self.assertEqual(int(group["y"][at24]), 1)
        self.assertEqual(int(group["y"][at25]), 1)
        self.assertEqual(audit["by_model_split"][f"{alias}:fit"]["eligible_decisions"], 5)

    def test_feedback_is_only_available_after_finish_plus_hour(self) -> None:
        t = MODULE.T0 + 3 * MODULE.HOUR_US
        fb = {
            "available_us": np.array([t - 120 * MODULE.MIN_US, t - 30 * MODULE.MIN_US, t], dtype=np.int64),
            "duration_us": np.array([1, 3, 100], dtype=np.int64) * MODULE.US,
            "is_error": np.array([0, 1, 1], dtype=np.int8),
        }
        result = MODULE.feedback_features(np.array([t, t + 1], dtype=np.int64), fb)
        self.assertEqual(result["c120"].tolist(), [2.0, 2.0])
        self.assertEqual(result["c30"].tolist(), [1.0, 1.0])
        self.assertAlmostEqual(result["duration_p90_logsec"][0], math.log1p(3))
        self.assertAlmostEqual(result["duration_p90_logsec"][1], math.log1p(100))
        self.assertAlmostEqual(result["error_rate120"][0], 0.5)
        self.assertAlmostEqual(result["error_rate120"][1], 1.0)

    def test_nearest_rank_quantile_removes_expired_feedback(self) -> None:
        unique = np.array([10, 20, 30, 40], dtype=np.int64)
        ranks = np.array([0, 2, 1, 3], dtype=np.int32)
        starts = np.array([0, 1, 2, 4], dtype=np.int64)
        ends = np.array([2, 3, 4, 4], dtype=np.int64)
        q = MODULE._rolling_nearest_rank_q90(starts, ends, ranks, unique)
        self.assertEqual(q.tolist(), [30, 30, 40, 0])

    def test_rolling_quantile_matches_direct_sorted_windows(self) -> None:
        rng = np.random.default_rng(42)
        durations = rng.integers(1, 20, size=200, dtype=np.int64)
        unique, ranks = np.unique(durations, return_inverse=True)
        ends = np.sort(rng.integers(0, 201, size=60, dtype=np.int64))
        starts = np.array([rng.integers(0, end + 1) for end in ends], dtype=np.int64)
        starts.sort()
        # Keep each rolling left boundary no farther than the right boundary.
        starts = np.minimum(starts, ends)
        actual = MODULE._rolling_nearest_rank_q90(starts, ends, ranks.astype(np.int32), unique)
        expected = []
        for left, right in zip(starts, ends):
            values = np.sort(durations[left:right])
            expected.append(int(values[math.ceil(0.9 * len(values)) - 1]) if len(values) else 0)
        self.assertEqual(actual.tolist(), expected)

    def test_last_target_ends_within_split(self) -> None:
        bins = np.zeros(MODULE.N_BINS, dtype=np.int64)
        own = {alias: np.zeros(MODULE.N_BINS, dtype=np.int64) for alias in MODULE.ALIASES}
        boundary = (MODULE.T_FIT - MODULE.T0) // MODULE.BIN_US
        alias = MODULE.ALIASES[0]
        bins[boundary - 3] = own[alias][boundary - 3] = 1
        bins[boundary - 1] = own[alias][boundary - 1] = 1
        decisions, _ = MODULE.split_decisions(bins, own, [])
        fit_idx = decisions[alias]["idx"][decisions[alias]["split"] == 0]
        self.assertIn(boundary - 2, fit_idx.tolist())
        self.assertNotIn(boundary - 1, fit_idx.tolist())


if __name__ == "__main__":
    unittest.main()
