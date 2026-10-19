"""Frozen scale-normalized SwissAI follow-up timing and feature contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_swissai_scale_adaptive", SCRIPTS / "evaluate_swissai_scale_adaptive.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ScaleAdaptiveTests(unittest.TestCase):
    def test_fixed_scale_and_per_window_normalization(self) -> None:
        vals = {name: np.array([0.0]) for name in MODULE.prior.ARRIVAL_FEATURES}
        vals.update({"m5": np.array([2.0]), "m10": np.array([2.0]),
                     "m30": np.array([9.0]), "m60": np.array([12.0]),
                     "m120": np.array([24.0]), "g5": np.array([1.0]),
                     "g10": np.array([1.0]), "g30": np.array([6.0]),
                     "g60": np.array([12.0]), "g120": np.array([24.0])})
        x, scale = MODULE.normalized_features(vals)
        self.assertEqual(scale.tolist(), [3.0])  # max(1,2,9/3,12/6)
        self.assertAlmostEqual(x[0, 1], 2 / (3 * 0.5))
        self.assertAlmostEqual(x[0, 3], 9 / (3 * 3))
        self.assertAlmostEqual(x[0, 6], 1 / (2 * 0.5))
        self.assertAlmostEqual(x[0, 8], 6 / (2 * 3))
        self.assertTrue(np.isfinite(x).all())

    def test_target_transform_inverse_recovers_count(self) -> None:
        for scale, target in [(1.0, 0), (3.0, 5), (2500.0, 19000)]:
            z = math.log1p(target / scale)
            self.assertAlmostEqual(scale * math.expm1(z), target)

    def test_scale_uses_thirty_minute_history_when_last_ten_empty(self) -> None:
        vals = {name: np.array([0.0]) for name in MODULE.prior.ARRIVAL_FEATURES}
        vals.update({"m30": np.array([9.0]), "m60": np.array([12.0]),
                     "m120": np.array([12.0]), "g30": np.array([9.0]),
                     "g60": np.array([12.0]), "g120": np.array([12.0])})
        x, scale = MODULE.normalized_features(vals)
        self.assertEqual(scale.tolist(), [3.0])
        self.assertEqual(x[0, 2], 0.0)  # m10 / s_m
        self.assertEqual(x[0, 3], 1.0)  # m30 / (3*s_m)

    def test_holdout_target_includes_t_and_next_bin_but_not_previous(self) -> None:
        global_bins = np.zeros(MODULE.N_BINS, dtype=np.int64)
        own = {a: np.zeros(MODULE.N_BINS, dtype=np.int64) for a in MODULE.ALIASES}
        alias = MODULE.ALIASES[0]
        start = (MODULE.START - MODULE.prior.T0) // MODULE.prior.BIN_US
        global_bins[start - 1] = own[alias][start - 1] = 1
        global_bins[start] = own[alias][start] = 2
        global_bins[start + 1] = own[alias][start + 1] = 3
        decisions, _ = MODULE.holdout_decisions(global_bins, own, [])
        ix = int(np.flatnonzero(decisions[alias]["idx"] == start)[0])
        self.assertEqual(int(decisions[alias]["y"][ix]), 5)

    def test_last_target_ends_at_october_boundary_and_gap_is_causal(self) -> None:
        global_bins = np.zeros(MODULE.N_BINS, dtype=np.int64)
        own = {a: np.zeros(MODULE.N_BINS, dtype=np.int64) for a in MODULE.ALIASES}
        alias = MODULE.ALIASES[0]
        end = (MODULE.END - MODULE.prior.T0) // MODULE.prior.BIN_US
        global_bins[end - 3] = own[alias][end - 3] = 1
        global_bins[end - 1] = own[alias][end - 1] = 1
        decisions, _ = MODULE.holdout_decisions(global_bins, own, [])
        self.assertIn(end - 2, decisions[alias]["idx"].tolist())
        self.assertNotIn(end - 1, decisions[alias]["idx"].tolist())
        b = MODULE.START + 5 * MODULE.prior.HOUR_US
        blocked = MODULE.prior.causal_restart_mask([b], MODULE.N_BINS)
        before = (b - MODULE.prior.T0) // MODULE.prior.BIN_US - 1
        after = (b - MODULE.prior.T0) // MODULE.prior.BIN_US
        self.assertFalse(blocked[before])
        self.assertTrue(blocked[after])

    def test_ingestion_stops_before_october_sealed_period(self) -> None:
        old_aliases = MODULE.ALIASES
        try:
            MODULE.ALIASES = tuple(sorted(hashlib.sha256(x.encode()).hexdigest()[:10] for x in ("A", "B")))
            table = pa.table({
                "created_utc_us": pa.array([
                    MODULE.prior.T0 + MODULE.prior.BIN_US,
                    MODULE.START + MODULE.prior.BIN_US,
                    MODULE.END - MODULE.prior.BIN_US,
                    MODULE.END,
                    MODULE.END + MODULE.prior.BIN_US,
                ], type=pa.int64()),
                "model": pa.array(["A", "B", "A", "A", "B"]),
            })
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "compact.parquet"
                pq.write_table(table, path)
                global_bins, model_bins, _, info = MODULE.ingest_arrivals_until_end(path, ("A", "B"))
            self.assertEqual(info["rows_read_before_2025_10_16"], 3)
            self.assertEqual(int(global_bins.sum()), 3)
            self.assertEqual(sum(int(x.sum()) for x in model_bins.values()), 3)
        finally:
            MODULE.ALIASES = old_aliases

    def test_duplicate_alias_decision_is_rejected_before_score(self) -> None:
        audit = {"grid_decisions": 1000, "causal_global_eligible": 900,
                 "by_model": {a: {"active_decisions": 600, "eligible_decisions": 550,
                                  "positive_targets": 250, "zero_targets": 300} for a in MODULE.ALIASES}}
        dev = pa.table({"model_alias": [MODULE.ALIASES[0]], "t_us": [MODULE.prior.T0]})
        test = pa.table({"model_alias": [MODULE.ALIASES[0], MODULE.ALIASES[0]],
                         "t_us": [MODULE.START, MODULE.START]})
        gate = MODULE.quality_gate(audit, dev, test)
        self.assertFalse(gate["passes"])
        self.assertTrue(any("Duplicate" in reason for reason in gate["reasons"]))


if __name__ == "__main__":
    unittest.main()
