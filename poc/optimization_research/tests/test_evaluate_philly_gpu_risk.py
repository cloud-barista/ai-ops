"""Synthetic-only guards for the frozen Philly scoring wrapper."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_philly_gpu_risk as scorer  # noqa: E402


def candidate_row(*, machine: str = "m1", slot: int = 0, day: int = 100,
                  current: float = 40.0, old: float = 20.0,
                  middle: float = 30.0, old_valid: bool = True,
                  middle_valid: bool = True) -> dict:
    t = day * 86400
    high = current >= 80
    return {
        "machine_hash": hashlib.sha256(machine.encode()).hexdigest(),
        "gpu_index": slot, "window_start_utc_s": t,
        "decision_utc_s": t + 600, "utc_day": day, "split": "train",
        "cohort": "complete" if old_valid and middle_valid else "partial_past",
        "current_state": "at_least_80" if high else "below_80",
        "expected_gpu_slots": 2, "older_util": old if old_valid else np.nan,
        "middle_util": middle if middle_valid else np.nan,
        "current_util": current, "older_valid": old_valid,
        "middle_valid": middle_valid,
        "past_gap_s": 300 if middle_valid else 600 if old_valid else 900,
        "current_high": high,
        "any_recent_high": high or (old_valid and old >= 80)
                           or (middle_valid and middle >= 80),
    }


def future_row(candidate: dict, *, complete: bool = True) -> dict:
    row = {key: candidate[key] for key in scorer.KEYS}
    row.update({f"future_valid_{i}": complete or i != 0 for i in range(6)})
    row["future_valid_count"] = 6 if complete else 5
    row["future_complete"] = complete
    return row


def compact_frames(*, complete: bool = True, y: int = 1,
                   **kwargs) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    candidate = candidate_row(**kwargs)
    features = pd.DataFrame([candidate])[scorer.builder.CANDIDATE_SCHEMA.names]
    future = pd.DataFrame([future_row(candidate, complete=complete)])[
        scorer.builder.FUTURE_OBSERVABILITY_SCHEMA.names]
    labels = pd.DataFrame([{**{key: candidate[key] for key in scorer.KEYS},
                            "y": y if complete else pd.NA}])
    labels["y"] = labels.y.astype("Int8")
    return features, future, labels


def bounds() -> dict:
    return scorer.builder.split_boundaries(95 * 86400, 130 * 86400)


class PhillyRiskScorerTest(unittest.TestCase):
    def test_frozen_status_and_draft_hash_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft = root / "draft.md"
            frozen = root / "frozen.md"
            draft.write_text("synthetic protocol\n")
            digest = scorer._hash(draft)
            frozen.write_text(f"**상태: PRE-FREEZE.**\nSHA256 `{digest}`\n")
            with self.assertRaisesRegex(ValueError, "not FROZEN"):
                scorer.require_frozen_protocol(frozen, draft)
            frozen.write_text(f"**상태: FROZEN — synthetic.**\nSHA256 `{'0'*64}`\n")
            with self.assertRaisesRegex(ValueError, "draft SHA256"):
                scorer.require_frozen_protocol(frozen, draft)
            frozen.write_text(f"**상태: FROZEN — synthetic.**\nSHA256 `{digest}`\n**PENDING**\n")
            with self.assertRaisesRegex(ValueError, "pending"):
                scorer.require_frozen_protocol(frozen, draft)
            frozen.write_text(f"**상태: FROZEN — synthetic.**\nSHA256 `{digest}`\n")
            self.assertIn(digest, scorer.require_frozen_protocol(frozen, draft))

    def test_reviewed_dry_run_hash_required_before_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dry_run_audit.json").write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "reviewed SHA256"):
                scorer.verify_dry_run_manifest(root, "0" * 64, "synthetic")

    def test_candidate_future_label_keys_and_state_must_replay(self):
        features, future, labels = compact_frames()
        observed = scorer.validate_replayed_chunk(features, future, labels, bounds())
        self.assertEqual(len(observed), 1)
        self.assertEqual(int(observed.iloc[0].y), 1)
        broken = future.copy()
        broken.loc[0, "gpu_index"] = 1
        with self.assertRaisesRegex(ValueError, "candidate keys"):
            scorer.validate_replayed_chunk(features, broken, labels, bounds())
        broken = labels.copy()
        broken.loc[0, "decision_utc_s"] += 300
        with self.assertRaisesRegex(ValueError, "candidate keys"):
            scorer.validate_replayed_chunk(features, future, broken, bounds())
        broken = features.copy()
        broken.loc[0, "cohort"] = "partial_past"
        with self.assertRaisesRegex(ValueError, "cohort"):
            scorer.validate_replayed_chunk(broken, future, labels, bounds())
        broken = features.copy()
        broken.loc[0, "current_state"] = "at_least_80"
        with self.assertRaisesRegex(ValueError, "current-state"):
            scorer.validate_replayed_chunk(broken, future, labels, bounds())
        broken = features.copy()
        broken.loc[0, "split"] = "validation"
        with self.assertRaisesRegex(ValueError, "split"):
            scorer.validate_replayed_chunk(broken, future, labels, bounds())
        broken = features.assign(future_util=95.0)
        with self.assertRaisesRegex(ValueError, "future feature"):
            scorer.validate_replayed_chunk(broken, future, labels, bounds())

    def test_unobserved_future_cannot_receive_label(self):
        features, future, labels = compact_frames(complete=False)
        self.assertTrue(scorer.validate_replayed_chunk(features, future, labels,
                                                       bounds()).empty)
        labels.loc[0, "y"] = 1
        with self.assertRaisesRegex(ValueError, "label presence"):
            scorer.validate_replayed_chunk(features, future, labels, bounds())

    def test_saved_candidate_and_observability_values_must_match_replay(self):
        features, future, _ = compact_frames()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features.to_parquet(root / "features.parquet", index=False)
            future.to_parquet(root / "future.parquet", index=False)
            reader = scorer.ParquetSliceReader(root / "features.parquet", list(features.columns))
            scorer._compare_saved(reader, features, list(features.columns), "candidate")
            reader.finish()
            reader = scorer.ParquetSliceReader(root / "future.parquet", list(future.columns))
            broken = future.copy()
            broken.loc[0, "future_valid_0"] = False
            with self.assertRaises(AssertionError):
                scorer._compare_saved(reader, broken, list(future.columns), "future")

    def test_source_quality_recomputed_against_saved_flags(self):
        machines = {"a": {"rows": 1000, "invalid_rows": 5,
                          "first_observed_gpu_slots": 2}}
        h = scorer.builder.machine_hash("a")
        dry = {
            "width_quality_by_machine": [{"machine_hash": h,
                                          "expected_gpu_slots": 2,
                                          "raw_rows": 1000,
                                          "width_mismatch_rows": 5,
                                          "width_mismatch_fraction": 0.005,
                                          "passes_at_most_one_percent": True}],
            "five_minute_input_quality_by_split": {
                split: {"denominator": "raw-row-observed window start is in split",
                        "observed_window_slots": 100, "valid_window_slots": 60,
                        "valid_fraction": 0.60, "passes_at_least_half": True}
                for split in scorer.SPLITS},
            "source_quality_gates_pass": True,
        }
        self.assertTrue(scorer.source_quality_gate(dry, {"machines": machines})["passes"])
        dry["source_quality_gates_pass"] = False
        with self.assertRaisesRegex(ValueError, "overall source-quality"):
            scorer.source_quality_gate(dry, {"machines": machines})

    def test_zero_candidate_partial_gate_remains_unscored(self):
        split_bounds = bounds()
        rows = []
        for split in scorer.SPLITS:
            rows.append({"split": split, "cohort": "complete",
                         "current_state": "below_80", "candidates": 4000,
                         "future_complete": 2000, "future_observation_fraction": 0.5,
                         "positive_gate_count": 100, "negative_gate_count": 1900,
                         "eligible_machine_count": 10,
                         "positive_machine_count": 5,
                         "negative_machine_count": 10,
                         "positive_utc_day_count": 5})
        dry = {"source_raw_utc_extrema_only_split": split_bounds,
               "gate_counts_by_split_cohort_state": rows,
               "draft_gate_checks": scorer.builder._gate_checks(rows, split_bounds)}
        statuses = scorer.stratum_gate_status(dry)
        self.assertTrue(statuses[("complete", "below_80")]["passes"])
        self.assertFalse(statuses[("partial_past", "below_80")]["passes"])
        self.assertFalse(statuses[("partial_past", "at_least_80")]["passes"])
        checks = dry["draft_gate_checks"]
        next(row for row in checks if row["cohort"] == "partial_past"
             and row["split"] == "test")["passes"] = True
        with self.assertRaisesRegex(ValueError, "Saved dry-run pass flag"):
            scorer.stratum_gate_status(dry)

    def test_scoring_only_the_synthetic_passed_cohort_and_calendar_halves(self):
        statuses = {(cohort, state): {"passes": False, "by_split": {}}
                    for cohort in scorer.COHORTS for state in scorer.STATES}
        statuses[("complete", "below_80")]["passes"] = True
        rows = []
        for split, day in (("train", 97), ("validation", 98),
                           ("test", 100), ("test", 101)):
            for i in range(12):
                r = candidate_row(machine=f"m{i % 4}", day=day,
                                  old=90.0 if i % 2 else 10.0,
                                  middle=20.0 + i, current=30.0 + i)
                r["split"] = split
                r["decision_utc_s"] += i * 300
                r["window_start_utc_s"] += i * 300
                r["y"] = int(i % 2)
                rows.append(r)
        examples = pd.DataFrame(rows)
        span = {"spans": {"test": {"start_utc_day": 100,
                                    "end_utc_day_exclusive": 102}}}
        predictions, metrics = scorer.score_eligible_strata(examples, statuses, span)
        self.assertEqual(len(predictions), 24)
        self.assertTrue(predictions.cohort.eq("complete").all())
        self.assertTrue(predictions.current_state.eq("below_80").all())
        self.assertNotIn("metrics", metrics["strata"]["partial_past/below_80"])
        self.assertIn("conditions", metrics["primary"])
        halves = metrics["primary"]["calendar_halves"]
        self.assertEqual([x["rows"] for x in halves], [12, 12])
        self.assertTrue(all(x["positives"] and x["negatives"] for x in halves))
        item = metrics["strata"]["complete/below_80"]
        self.assertEqual(len(item["brier_by_utc_day"]), 2)
        self.assertEqual(len(item["brier_by_machine"]), 4)


if __name__ == "__main__":
    unittest.main()
