"""Synthetic checks for Philly machine-history calibration boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_philly_machine_calibration as calibration  # noqa: E402
import fetch_philly_machine_calibration as collector  # noqa: E402


class FixedModel:
    def predict_pooled(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), 0.2)

    def predict_beta(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), 0.25)


def decision(machine: str, day: int, hour: int, split: str, y: int) -> dict:
    sec = day * 86400 + hour * 3600
    return {"machine_hash": hashlib.sha256(machine.encode()).hexdigest(),
            "gpu_index": 0, "window_start_utc_s": sec - 600,
            "decision_utc_s": sec, "utc_day": day, "split": split,
            "cohort": "complete", "current_state": "below_80",
            "expected_gpu_slots": 2, "older_util": 20.0,
            "middle_util": 20.0, "current_util": 20.0,
            "older_valid": True, "middle_valid": True,
            "past_gap_s": 300, "current_high": False,
            "any_recent_high": False, "future_complete": True, "y": y}


class CalibrationTest(unittest.TestCase):
    def test_hash_interval_is_disjoint_from_previous_samples(self):
        sample = [f"synthetic-{i}" for i in range(1000)]
        for mid in sample:
            self.assertEqual(collector.machine_selected(mid),
                             15 <= calibration.residue(mid) < 25)
            if collector.machine_selected(mid):
                self.assertFalse(calibration.residue(mid) < 15)

    def test_offset_uses_only_hourly_completed_history(self):
        frame = pd.DataFrame([decision("m", d, 0, "train", 1) for d in range(6)])
        p = np.full(len(frame), 0.2)
        delta = calibration.offset_fit(frame, p, sigma=1.0)
        self.assertGreater(delta, 0)
        extra = frame.iloc[[0]].copy()
        extra["decision_utc_s"] += 300
        extra["y"] = 0
        extended = pd.concat([frame, extra], ignore_index=True)
        self.assertAlmostEqual(calibration.offset_fit(
            extended, np.full(len(extended), 0.2), sigma=1.0), delta)

    def test_new_machine_december_labels_cannot_change_probabilities(self):
        rows = []
        for day in range(6):
            rows.extend(decision("m", day, hour, "train", int(hour % 2 == 0))
                        for hour in range(4))
        for day in range(6, 10):
            rows.extend(decision("m", day, hour, "validation", int(hour % 2 == 0))
                        for hour in range(4))
        rows.extend(decision("m", 10, hour, "test", 0) for hour in range(4))
        frame = pd.DataFrame(rows)
        model = FixedModel()
        first, first_decisions = calibration.make_predictions(
            frame, model, sigma=1.0, gate_threshold=0.0,
            global_delta=0.0, global_prior=0.2)
        changed = frame.copy()
        changed.loc[changed.split.eq("test"), "y"] = 1
        second, second_decisions = calibration.make_predictions(
            changed, model, sigma=1.0, gate_threshold=0.0,
            global_delta=0.0, global_prior=0.2)
        for method in ("pooled", "beta", "global_offset", "global_fitted", "machine_rate",
                       "machine_offset", "validated_offset"):
            np.testing.assert_allclose(first[method], second[method])
        self.assertEqual(first_decisions, second_decisions)
        self.assertTrue(first_decisions[0]["supported"])
        self.assertTrue(first_decisions[0]["gate_supported"])

    def test_sparse_new_machine_falls_back_to_selected_base(self):
        frame = pd.DataFrame([decision("m", 10, i, "test", 1) for i in range(3)])
        scored, choices = calibration.make_predictions(
            frame, FixedModel(), sigma=1.0, gate_threshold=0.0,
            global_delta=0.0, global_prior=0.2,
            fitted_global_delta=0.25)
        self.assertFalse(choices[0]["supported"])
        self.assertFalse(choices[0]["gate_on"])
        np.testing.assert_allclose(scored.validated_offset, scored.pooled)
        self.assertGreater(float(scored.global_fitted.mean()), float(scored.pooled.mean()))

    def test_fingerprint_changes_with_feature_or_observability(self):
        rows = pd.DataFrame([decision("m", 1, 0, "test", 0),
                             decision("m", 1, 1, "test", 0)])
        original = calibration._candidate_fingerprint(rows)
        changed = rows.copy()
        changed.loc[0, "current_util"] = 21.0
        self.assertNotEqual(original, calibration._candidate_fingerprint(changed))
        changed = rows.copy()
        changed.loc[0, "future_complete"] = False
        self.assertNotEqual(original, calibration._candidate_fingerprint(changed))

    def test_strong_simple_baseline_chooses_its_own_sigma(self):
        methods = ("pooled", "beta", "global_fitted", "machine_rate",
                   "machine_offset", "validated_offset")
        rows = []
        for sigma, proposed, simple in ((0.5, 0.08, 0.11),
                                        (1.0, 0.07, 0.09),
                                        (2.0, 0.075, 0.06)):
            scores = {name: {"machine_equal_brier": 0.10} for name in methods}
            scores["validated_offset"]["machine_equal_brier"] = proposed
            scores["machine_offset"]["machine_equal_brier"] = simple
            rows.append({"base_mode": "pooled", "sigma": sigma,
                         "gate_threshold": 0.0, "metrics": scores})
        selected, baseline = calibration.choose_development(rows)
        self.assertEqual(selected["sigma"], 1.0)
        self.assertEqual(baseline, ("machine_offset", 2.0, "pooled"))

    def test_machine_offset_baseline_selects_its_own_base_mode(self):
        methods = ("pooled", "beta", "global_fitted", "machine_rate",
                   "machine_offset", "validated_offset")
        rows = []
        for mode, simple in (("pooled", 0.08), ("fitted_global", 0.06)):
            scores = {name: {"machine_equal_brier": 0.12} for name in methods}
            scores["validated_offset"]["machine_equal_brier"] = (0.07 if mode == "pooled"
                                                                   else 0.09)
            scores["machine_offset"]["machine_equal_brier"] = simple
            rows.append({"base_mode": mode, "sigma": 1.0,
                         "gate_threshold": 0.0, "metrics": scores})
        selected, baseline = calibration.choose_development(rows)
        self.assertEqual(selected["base_mode"], "pooled")
        self.assertEqual(baseline, ("machine_offset", 1.0, "fitted_global"))

    def test_aggregate_gate_rejects_sparse_or_one_class_test(self):
        primary = {"positive_gate_count": 200, "negative_gate_count": 2000,
                   "positive_machine_count": 7, "negative_machine_count": 12,
                   "positive_utc_day_count": 10}
        halves = [{"positive": 100, "negative": 1000},
                  {"positive": 100, "negative": 1000}]
        args = {"candidates": 3000, "observed": 2200, "machines": 12,
                "valid_windows": 6000, "observed_windows": 6500,
                "retained_machines": 14}
        self.assertTrue(calibration.test_gate_pass(primary, halves, **args))
        one_class = [dict(halves[0]), dict(halves[1], positive=0)]
        self.assertFalse(calibration.test_gate_pass(primary, one_class, **args))
        self.assertFalse(calibration.test_gate_pass(primary, halves,
                                                    **dict(args, observed=1000)))

    def test_prefetch_refuses_unfrozen_or_changed_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol = root / "protocol.md"
            selection = root / "development_selection.json"
            selection.write_text(json.dumps({
                "kind": "philly_calibration_development_selection",
                "source_sha256": collector.EXPECTED_DEVELOPMENT_SHA256,
                "model_spec_sha256": collector.EXPECTED_MODEL_SHA256,
                "script_sha256": collector.sha256_file(
                    calibration.ROOT / "scripts/evaluate_philly_machine_calibration.py"),
                "builder_sha256": collector.sha256_file(
                    calibration.ROOT / "scripts/prepare_philly_gpu_risk_candidates.py"),
                "bounds_sha256": collector.sha256_file(
                    calibration.ROOT / "artifacts/philly_gpu_risk_candidates_v1/dry_run_audit.json")}))
            selection_sha = collector.sha256_file(selection)
            collector_sha = collector.sha256_file(Path(collector.__file__))
            lines = ["**상태: DRAFT**",
                     f"development_selection.json {selection_sha}",
                     f"fetch_philly_machine_calibration.py {collector_sha}"]
            for path in (calibration.ROOT / "scripts/evaluate_philly_machine_calibration.py",
                         calibration.ROOT / "scripts/prepare_philly_gpu_risk_candidates.py",
                         calibration.ROOT / "scripts/normalize_philly_gpu_sample.py",
                         calibration.ROOT / "artifacts/philly_machine_holdout_model_v1/model_spec.json"):
                lines.append(f"{path.name} {collector.sha256_file(path)}")
            protocol.write_text("\n".join(lines) + "\n")
            with self.assertRaisesRegex(ValueError, "not FROZEN"):
                collector.verify_pre_fetch_freeze(
                    protocol, collector.sha256_file(protocol), selection, selection_sha)
            protocol.write_text(protocol.read_text().replace("DRAFT", "FROZEN"))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                collector.verify_pre_fetch_freeze(
                    protocol, collector.sha256_file(protocol), selection, "0"*64)
            self.assertEqual(collector.verify_pre_fetch_freeze(
                protocol, collector.sha256_file(protocol), selection, selection_sha)[
                    "development_selection_sha256"], selection_sha)

    def test_score_refuses_changed_dry_run_source_before_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.json"
            norm = root / "norm.json"
            source = root / "source.parquet"
            audit = root / "dry.json"
            raw.write_text("raw")
            norm.write_text("norm")
            source.write_text("source")
            audit.write_text(json.dumps({"passes": True,
                                         "raw_audit_sha256": "0"*64,
                                         "normalized_audit_sha256": calibration.sha256(norm),
                                         "normalized_source_sha256": calibration.sha256(source)}))
            with (mock.patch.object(calibration, "TEST_AUDIT", audit),
                  mock.patch.object(calibration, "NEW_RAW_AUDIT", raw),
                  mock.patch.object(calibration, "NEW_AUDIT", norm),
                  mock.patch.object(calibration, "NEW", source),
                  mock.patch.object(calibration, "_verify_freeze", return_value={})):
                with self.assertRaisesRegex(ValueError, "differs from the score-free dry run"):
                    calibration.score_test("1"*64, "2"*64,
                                           calibration.sha256(audit),
                                           root / "test_metrics.json")


if __name__ == "__main__":
    unittest.main()
