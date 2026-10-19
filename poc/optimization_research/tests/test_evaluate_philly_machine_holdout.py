"""Synthetic guards for original-only fitting and disjoint-machine scoring."""

from __future__ import annotations

from collections import Counter
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_philly_machine_holdout as holdout  # noqa: E402


def candidate(machine: str, day: int, offset: int, *, current: float,
              y: int, split: str = "test") -> dict:
    t = day * 86400 + offset * 300
    h = hashlib.sha256(machine.encode("utf-8")).hexdigest()
    return {
        "machine_hash": h, "gpu_index": 0,
        "window_start_utc_s": t, "decision_utc_s": t + 600,
        "utc_day": day, "split": split, "cohort": "complete",
        "current_state": "below_80", "expected_gpu_slots": 2,
        "older_util": 20.0, "middle_util": 20.0,
        "current_util": current, "older_valid": True,
        "middle_valid": True, "past_gap_s": 300,
        "current_high": False, "any_recent_high": False, "y": y,
    }


def synthetic_spec() -> dict:
    n = len(holdout.risk_models.FULL_FEATURES)
    coefficient = [0.0] * n
    coefficient[2] = 0.1
    return {
        "kind": "philly_original_machines_only_frozen_holdout_model_spec",
        "pooled_quality": {
            "selected_c": 10.0,
            "feature_columns": list(holdout.risk_models.FULL_FEATURES),
            "older_median": 20.0, "middle_median": 20.0,
            "scaler_mean": [0.0] * n,
            "scaler_variance": [1.0] * n,
            "scaler_scale": [1.0] * n,
            "coefficient": coefficient,
            "intercept": -5.0,
        },
        "selected_beta": {
            "name": "state_recent",
            "keys": ["current_high", "any_recent_high"],
            "probabilities": [
                {"key": [False, False], "probability": 0.2},
                {"key": [False, True], "probability": 0.8},
                {"key": [True, True], "probability": 0.9},
            ],
        },
    }


def synthetic_bounds() -> dict:
    return holdout.builder.split_boundaries(90 * 86400, 110 * 86400)


class PhillyMachineHoldoutTest(unittest.TestCase):
    def test_model_spec_predicts_without_fitting_and_rejects_changed_choices(self):
        spec = synthetic_spec()
        model = holdout.load_frozen_model_spec(spec)
        day = synthetic_bounds()["spans"]["test"]["start_utc_day"]
        rows = pd.DataFrame([
            candidate("m1", day, 0, current=10.0, y=0),
            candidate("m2", day, 1, current=60.0, y=1),
        ])
        pooled = model.predict_pooled(rows)
        np.testing.assert_allclose(pooled, [1 / (1 + np.exp(4)),
                                             1 / (1 + np.exp(-1))])
        np.testing.assert_allclose(model.predict_beta(rows), [0.2, 0.2])
        changed = copy.deepcopy(spec)
        changed["pooled_quality"]["selected_c"] = 1.0
        with self.assertRaisesRegex(ValueError, "choices"):
            holdout.load_frozen_model_spec(changed)
        changed = copy.deepcopy(spec)
        changed["selected_beta"]["probabilities"].append(
            {"key": [False, False], "probability": 0.3})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            holdout.load_frozen_model_spec(changed)

    def test_refit_receives_only_original_train_validation_rows(self):
        examples = pd.DataFrame([
            {"split": "train", "cohort": "complete", "current_state": "below_80", "y": 0},
            {"split": "train", "cohort": "complete", "current_state": "below_80", "y": 1},
            {"split": "validation", "cohort": "complete", "current_state": "below_80", "y": 0},
            {"split": "validation", "cohort": "complete", "current_state": "below_80", "y": 1},
            {"split": "test", "cohort": "complete", "current_state": "below_80", "y": 999},
        ])
        transform = type("T", (), {"older_median": 20.0, "middle_median": 30.0})()
        fitted = type("F", (), {"c": 10.0, "validation_brier": 0.1,
                                 "transform": transform})()
        beta = holdout.risk_models.BetaTable(
            "state_recent", ("current_high", "any_recent_high"),
            {(False, False): .2})
        metrics = {"strata": {"complete/below_80": {
            "selected_beta": "state_recent",
            "validation_beta_brier": {"state_recent": .2},
            "selected_model_c": {"pooled_quality": 10.0},
            "validation_model_brier": {"pooled_quality": .1},
            "training_medians": {"pooled_quality": [20.0, 30.0]},
        }}}
        statuses = {("complete", "below_80"): {"passes": True}}
        with (mock.patch.object(holdout.risk_models, "select_logistic",
                                return_value=fitted) as logistic,
              mock.patch.object(holdout.risk_models, "select_beta_baseline",
                                return_value=(beta, {"state_recent": .2})) as baseline):
            result = holdout.refit_original_train_validation(examples, metrics, statuses)
        self.assertEqual(result.train_rows, 2)
        self.assertEqual(result.validation_rows, 2)
        self.assertEqual(logistic.call_args.args[0].y.tolist(), [0, 1])
        self.assertEqual(logistic.call_args.args[1].y.tolist(), [0, 1])
        self.assertEqual(baseline.call_args.args[0].y.tolist(), [0, 1])
        self.assertEqual(baseline.call_args.args[1].y.tolist(), [0, 1])

    def test_original_test_predictions_must_replay_by_exact_keys_and_labels(self):
        model = holdout.load_frozen_model_spec(synthetic_spec())
        day = synthetic_bounds()["spans"]["test"]["start_utc_day"]
        examples = pd.DataFrame([
            candidate("m1", day, 0, current=10.0, y=0),
            candidate("m2", day, 1, current=60.0, y=1),
        ])
        saved = examples[[*holdout.original.KEYS, "cohort", "current_state", "y"]].copy()
        saved["pooled_quality"] = model.predict_pooled(examples)
        saved["selected_beta"] = model.predict_beta(examples)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old_predictions.parquet"
            saved.to_parquet(path, index=False)
            metrics = {"test_predictions_sha256": holdout.file_sha256(path)}
            check = holdout.verify_original_test_replay(examples, model, path, metrics)
            self.assertEqual(check["rows"], 2)
            self.assertLess(check["max_abs_pooled_quality_error"], 1e-14)
            changed = saved.copy()
            changed.loc[0, "y"] = 1
            changed.to_parquet(path, index=False)
            metrics["test_predictions_sha256"] = holdout.file_sha256(path)
            with self.assertRaisesRegex(ValueError, "keys or labels"):
                holdout.verify_original_test_replay(examples, model, path, metrics)

    def test_holdout_gate_requires_all_four_rows_and_consistent_flags(self):
        rows = holdout.holdout_prepare.test_gate_rows(holdout.builder.GateAccumulator())
        bounds = holdout.holdout_prepare.test_only_bounds(
            {"source_raw_utc_extrema_only_split": synthetic_bounds()})
        status = holdout.holdout_test_gate_status(rows, bounds)
        self.assertFalse(status[("complete", "below_80")]["passes"])
        with self.assertRaisesRegex(ValueError, "all four"):
            holdout.holdout_test_gate_status(rows[:-1], bounds)
        changed = copy.deepcopy(rows)
        changed[0]["gate_pass"] = True
        with self.assertRaisesRegex(ValueError, "saved gate"):
            holdout.holdout_test_gate_status(changed, bounds)

    def test_primary_verdict_uses_pooled_model_machine_ci_and_both_halves(self):
        metrics = {
            "pooled_quality": {"brier": .05, "average_precision": .8,
                               "daily_top_5pct": {"recall": .4}},
            "selected_beta": {"brier": .10, "average_precision": .5,
                              "daily_top_5pct": {"recall": .2}},
        }
        bootstrap = {"percentile_95": [-.07, -.01]}
        halves = [{"positives": 1, "negatives": 1,
                   "first_brier": .05, "second_brier": .10},
                  {"positives": 1, "negatives": 1,
                   "first_brier": .06, "second_brier": .11}]
        by_machine = [{"pooled_minus_beta_brier": -.02},
                      {"pooled_minus_beta_brier": -.01}]
        verdict = holdout.holdout_primary_criteria(
            metrics, bootstrap, halves, by_machine)
        self.assertTrue(verdict["passes"])
        changed = copy.deepcopy(halves)
        changed[1]["negatives"] = 0
        self.assertFalse(holdout.holdout_primary_criteria(
            metrics, bootstrap, changed, by_machine)["evaluable"])
        changed = copy.deepcopy(by_machine)
        changed[1]["pooled_minus_beta_brier"] = .03
        self.assertFalse(holdout.holdout_primary_criteria(
            metrics, bootstrap, halves, changed)["passes"])

    def test_scoring_uses_pooled_and_fixed_beta_on_test_rows_only(self):
        model = holdout.load_frozen_model_spec(synthetic_spec())
        bounds = synthetic_bounds()
        start = bounds["spans"]["test"]["start_utc_day"]
        rows = []
        for machine in ("m1", "m2"):
            for day in (start, start + 2):
                for index in range(6):
                    y = index % 2
                    rows.append(candidate(machine, day, index,
                                          current=60.0 if y else 10.0, y=y))
        examples = pd.DataFrame(rows)
        statuses = {(cohort, state): {"passes": False, "counts": {}}
                    for cohort, state in holdout.STRATA}
        statuses[("complete", "below_80")] = {
            "passes": True, "counts": {"future_complete": len(examples)}}
        old = {key: {"passes": key == ("complete", "below_80")}
               for key in holdout.STRATA}
        predictions, scored = holdout.score_new_machine_test(
            examples, model, statuses, old, bounds)
        self.assertEqual(list(predictions.columns), list(holdout.NEW_SCORE_COLUMNS))
        self.assertEqual(len(predictions), 24)
        self.assertTrue(scored["primary"]["passes"])
        self.assertEqual(scored["strata"]["complete/below_80"]
                         ["machine_bootstrap"]["seed"], 43)
        with self.assertRaisesRegex(ValueError, "non-test"):
            holdout.score_new_machine_test(
                examples.assign(split="train"), model, statuses, old, bounds)

    def test_replay_rejects_a_tampered_future_row(self):
        machine = next(f"new{i}" for i in range(1000)
                       if holdout.holdout_prepare.holdout_selected(f"new{i}"))
        full = synthetic_bounds()
        old_dry = {"source_raw_utc_extrema_only_split": full}
        bounds = holdout.holdout_prepare.test_only_bounds(old_dry)
        test_span = full["spans"]["test"]
        base = test_span["start_utc_s"]
        raw_rows = []
        for index in range(80):
            raw_rows.append({
                "source_row_index": index + 1, "time_utc_s": base + 60 * index,
                "machine_id": machine, "expected_gpu_slots": 2,
                "observed_gpu_slots": 2, "first_observed_gpu_slots": 2,
                "width_matches_first": True, "row_usable": True,
                "row_invalid_reason": None,
                "gpu0_util": 90.0 if index >= 40 else 10.0,
                "gpu1_util": 10.0,
                **{f"gpu{i}_util": np.nan for i in range(2, 8)},
            })
        raw = pd.DataFrame(raw_rows)
        windows, _, summary = holdout.builder.minute_to_windows(raw)
        gates = holdout.holdout_prepare.HoldoutGateAccumulator(test_span)
        candidate_frames, future_frames, _, candidate_summary = (
            holdout.builder.construct_candidates(
                windows, bounds, gates, include_scoring_labels=False))
        candidates = pd.concat(candidate_frames, ignore_index=True)
        futures = pd.concat(future_frames, ignore_index=True)
        summaries = pd.concat([
            holdout.builder._observability(c, f)
            for c, f in zip(candidate_frames, future_frames)], ignore_index=True)
        window_quality = holdout.builder._window_quality(windows)
        quality_counts = holdout.builder.split_input_window_counts(window_quality, full)
        dry = {
            "partitioning": {"machine_hash_buckets": 1,
                             "max_partition_rows": 1000,
                             "bucket_count_used": 1,
                             "max_bucket_rows_observed": len(raw)},
            "test_span": test_span, "test_only_bounds": bounds,
            "gate_counts_by_split_cohort_state":
                holdout.holdout_prepare.test_gate_rows(gates),
            "primary_half_gate_counts": gates.half_gate(),
            "totals": dict(Counter(summary) + Counter(candidate_summary)),
            "source_quality": {
                "excluded_rows": 0, "all_test_active_machines": 1,
                "retained_test_active_machines": 1,
                "excluded_test_active_machines": 0,
                "input_quality_by_original_split": {
                    split: {"observed_window_slots":
                            int(quality_counts[split]["observed_window_slots"]),
                            "valid_window_slots":
                            int(quality_counts[split]["valid_window_slots"])}
                    for split in holdout.builder.SPLIT_NAMES},
            },
        }
        normalized = {"machines": {machine: {"first_observed_gpu_slots": 2,
                                              "rows": 80, "invalid_rows": 0}}}
        partition_audit = {"excluded_rows": 0, "all_test_active_machines": 1,
                           "retained_test_active_machines": 1,
                           "excluded_test_active_machines": 0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared"
            prepared.mkdir()
            raw_path = root / "bucket.parquet"
            raw.to_parquet(raw_path, index=False)
            candidates.to_parquet(prepared / "candidates.parquet", index=False)
            futures.to_parquet(prepared / "future_observability.parquet", index=False)
            summaries.to_parquet(prepared / "future_observability_summary.parquet", index=False)
            with mock.patch.object(holdout.holdout_prepare, "partition_holdout",
                                   return_value=({0: (raw_path, len(raw))}, partition_audit)):
                work = root / "work"
                work.mkdir()
                observed, replay = holdout.replay_holdout_labels(
                    raw_path, prepared, dry, normalized, full, workdir=work)
                self.assertGreater(len(observed), 0)
                self.assertEqual(replay["candidate_rows"], len(candidates))
                changed = futures.copy()
                changed.loc[0, "future_valid_0"] = not bool(changed.loc[0, "future_valid_0"])
                changed.to_parquet(prepared / "future_observability.parquet", index=False)
                work2 = root / "work2"
                work2.mkdir()
                with self.assertRaises(AssertionError):
                    holdout.replay_holdout_labels(
                        raw_path, prepared, dry, normalized, full, workdir=work2)


if __name__ == "__main__":
    unittest.main()
