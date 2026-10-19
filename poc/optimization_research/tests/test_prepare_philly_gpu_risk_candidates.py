"""Synthetic checks for Philly per-slot windows, cohorts, and audit gating."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_philly_gpu_risk_candidates.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("prepare_philly_gpu_risk_candidates", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def minute(machine: str, time_s: int, row_id: int, *,
           values: tuple[float | None, ...] | None = None,
           expected_slots: int = 8, observed_slots: int | None = None) -> dict:
    observed_slots = expected_slots if observed_slots is None else observed_slots
    if values is None:
        values = (10.0,) * expected_slots + (None,) * (8 - expected_slots)
    row_usable = observed_slots == expected_slots
    if not row_usable:
        values = (None,) * 8
    return {
        "machine_id": machine, "time_utc_s": time_s,
        "source_row_index": row_id, "expected_gpu_slots": expected_slots,
        "first_observed_gpu_slots": expected_slots,
        "observed_gpu_slots": observed_slots,
        "width_matches_first": row_usable, "row_usable": row_usable,
        "row_invalid_reason": None if row_usable else "width_mismatch_first",
        **{f"gpu{slot}_util": values[slot] for slot in range(8)},
    }


def dense_minutes(machine: str, first_window: int, windows: int) -> pd.DataFrame:
    rows = []
    for window in range(windows):
        for minute_index in range(5):
            rows.append(minute(machine, first_window + window * 300 + minute_index * 60,
                               len(rows) + 1))
    return pd.DataFrame(rows)


def candidate_at(frames: list[pd.DataFrame], time_s: int, slot: int = 0) -> pd.Series:
    frame = pd.concat(frames, ignore_index=True)
    rows = frame.loc[frame.window_start_utc_s.eq(time_s) & frame.gpu_index.eq(slot)]
    if len(rows) != 1:
        raise AssertionError(f"Expected one slot candidate at {time_s}; got {len(rows)}")
    return rows.iloc[0]


class PhillyCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.day = 100 * 86400
        self.bounds = MODULE.split_boundaries(95 * 86400, 130 * 86400)

    def test_five_distinct_aligned_valid_minutes_per_slot(self):
        values = [(float(10 * (i + 1)), 20.0) + (0.0,) * 6 for i in range(5)]
        values[2] = (30.0, None) + (0.0,) * 6
        rows = [minute("m1", self.day + i * 60, i + 1, values=values[i]) for i in range(5)]
        rows.extend(minute("m1", self.day + 300 + i * 60, 10 + i,
                           observed_slots=16) for i in range(5))
        rows.extend(minute("m1", self.day + 600 + i * 60, 20 + i) for i in range(5))
        rows.append(minute("m1", self.day + 660, 99))  # Identical duplicate still invalid.
        rows.extend(minute("m1", self.day + 900 + i * 60, 30 + i) for i in range(4))
        rows.append(minute("m1", self.day + 1200 + 30, 55))  # Off-minute does not fill a gap.
        windows, quality, summary = MODULE.minute_to_windows(pd.DataFrame(rows))
        first = windows.loc[windows.window_start_utc_s.eq(self.day)].iloc[0]
        self.assertTrue(first.valid_0)
        self.assertAlmostEqual(first.util_0, 30.0)
        self.assertFalse(first.valid_1)
        self.assertTrue(np.isnan(first.util_1))
        irregular = windows.loc[windows.window_start_utc_s.eq(self.day + 300)].iloc[0]
        self.assertFalse(irregular.valid_0)
        duplicated = windows.loc[windows.window_start_utc_s.eq(self.day + 600)].iloc[0]
        self.assertFalse(duplicated.valid_0)
        missing = windows.loc[windows.window_start_utc_s.eq(self.day + 900)].iloc[0]
        self.assertFalse(missing.valid_0)
        self.assertEqual(summary["duplicate_minute_keys"], 1)
        self.assertEqual(summary["off_minute_rows"], 1)
        reasons = set(quality.reason)
        self.assertTrue({"valid", "null_na_or_empty", "width_mismatch",
                         "duplicate_minute", "off_minute"} <= reasons)

    def test_cohort_fixed_before_future_observation_or_outcome(self):
        raw = dense_minutes("m1", self.day, 13)
        raw.loc[raw.time_utc_s.eq(self.day + 60), "gpu0_util"] = np.nan
        target = self.day + 2 * 300
        windows, _, _ = MODULE.minute_to_windows(raw)
        gate_a = MODULE.GateAccumulator()
        frame_a, obs_a, labels_a, _ = MODULE.construct_candidates(windows, self.bounds, gate_a)
        a = candidate_at(frame_a, target)
        a_obs = pd.concat(obs_a, ignore_index=True).loc[
            lambda x: x.decision_utc_s.eq(target + 600) & x.gpu_index.eq(0)].iloc[0]
        self.assertEqual(a.cohort, "partial_past")
        self.assertFalse(a.older_valid)
        self.assertTrue(a.middle_valid)
        self.assertEqual(a.past_gap_s, 300)
        self.assertTrue(a_obs.future_complete)
        self.assertIsNone(labels_a)
        changed_value = raw.copy()
        changed_value.loc[changed_value.time_utc_s.between(target + 600, target + 840),
                          "gpu0_util"] = 95.0
        windows, _, _ = MODULE.minute_to_windows(changed_value)
        gate_b = MODULE.GateAccumulator()
        frame_b, obs_b, _, _ = MODULE.construct_candidates(windows, self.bounds, gate_b)
        b = candidate_at(frame_b, target)
        for name in ("cohort", "current_state", "older_util", "middle_util",
                     "current_util", "older_valid", "middle_valid", "past_gap_s"):
            left, right = a[name], b[name]
            self.assertTrue((pd.isna(left) and pd.isna(right)) or left == right, name)
        b_obs = pd.concat(obs_b, ignore_index=True).loc[
            lambda x: x.decision_utc_s.eq(target + 600) & x.gpu_index.eq(0)].iloc[0]
        self.assertTrue(b_obs.future_complete)
        self.assertFalse(any("future_util" in name or name == "y" for name in b.index))
        self.assertFalse(any(name.startswith("future_") for name in b.index))
        a_positive = sum(r["positive_gate_count"] for r in gate_a.result())
        b_positive = sum(r["positive_gate_count"] for r in gate_b.result())
        self.assertGreater(b_positive, a_positive)
        missing_future = raw.copy()
        missing_future.loc[missing_future.time_utc_s.eq(target + 600 + 60), "gpu0_util"] = np.nan
        windows, _, _ = MODULE.minute_to_windows(missing_future)
        frame_c, obs_c, _, _ = MODULE.construct_candidates(windows, self.bounds)
        c = candidate_at(frame_c, target)
        self.assertEqual(c.cohort, a.cohort)
        c_obs = pd.concat(obs_c, ignore_index=True).loc[
            lambda x: x.decision_utc_s.eq(target + 600) & x.gpu_index.eq(0)].iloc[0]
        self.assertFalse(c_obs.future_complete)
        self.assertEqual(c_obs.future_valid_count, 5)

    def test_utc_date_crossing_and_label_boundary(self):
        edge = 116 * 86400
        # Full 5-minute windows cross the UTC date and train/validation boundary.
        raw = dense_minutes("m1", edge - 10 * 300, 28)
        windows, _, _ = MODULE.minute_to_windows(raw)
        frames, future, _, summary = MODULE.construct_candidates(windows, self.bounds)
        validation = candidate_at(frames, edge, 0)
        validation_future = pd.concat(future, ignore_index=True).loc[
            lambda x: x.decision_utc_s.eq(edge + 600) & x.gpu_index.eq(0)].iloc[0]
        self.assertEqual(validation.split, "validation")
        self.assertEqual(validation.utc_day, 116)
        self.assertEqual(validation.cohort, "complete")
        self.assertTrue(validation_future.future_complete)
        all_candidates = pd.concat(frames, ignore_index=True)
        self.assertFalse(all_candidates.decision_utc_s.eq(edge - 300).any())
        self.assertGreater(summary["excluded_by_split_boundary"], 0)
        self.assertTrue((all_candidates.decision_utc_s + 2100 <=
                         np.where(all_candidates.split.eq("train"), edge,
                                  self.bounds["spans"]["validation"]["end_utc_s"])).all())

    def test_split_from_raw_utc_extrema_only(self):
        bounds = MODULE.split_boundaries(95 * 86400 + 9 * 3600,
                                         130 * 86400 + 17 * 3600)
        self.assertEqual(bounds["first_full_utc_day"], 96)
        self.assertEqual(bounds["last_full_utc_day_exclusive"], 130)
        self.assertEqual([bounds["spans"][s]["days"] for s in MODULE.SPLIT_NAMES],
                         [20, 7, 7])
        self.assertTrue(bounds["duration_gate_pass"])

    def test_input_quality_denominator_uses_observed_window_start_and_existing_slots(self):
        bounds = self.bounds  # Train starts day 96, validation starts day 116.
        rows = [minute("two", 95 * 86400, 1, expected_slots=2)]
        for i in range(5):
            rows.append(minute("two", 96 * 86400 + i * 60, 2 + i,
                               expected_slots=2))
        rows.append(minute("two", 96 * 86400 + 330, 10,
                           expected_slots=2))  # Off-minute-only observed block.
        for i in range(5):
            rows.append(minute("two", 116 * 86400 + i * 60, 11 + i,
                               expected_slots=2))
        windows, quality, _ = MODULE.minute_to_windows(pd.DataFrame(rows))
        off_only = windows.loc[windows.window_start_utc_s.eq(96 * 86400 + 300)].iloc[0]
        self.assertFalse(off_only.valid_0)
        self.assertEqual(off_only.observed_minute_keys, 0)
        self.assertIn("off_minute", set(quality.reason))
        split = MODULE.split_input_window_counts(MODULE._window_quality(windows), bounds)
        self.assertEqual(split["train"]["observed_window_slots"], 4)
        self.assertEqual(split["train"]["valid_window_slots"], 2)
        self.assertEqual(split["validation"]["observed_window_slots"], 2)
        self.assertEqual(split["validation"]["valid_window_slots"], 2)
        self.assertEqual(split["test"]["observed_window_slots"], 0)

    def test_same_machine_stays_in_one_disk_partition(self):
        ids = []
        n = 0
        while len(ids) < 2:
            mid = f"chosen-{n}"
            if MODULE.machine_selected(mid):
                ids.append(mid)
            n += 1
        rows = []
        for minute_index in range(10):
            for mid in ids:  # Interleaved source order.
                rows.append(minute(mid, self.day + minute_index * 60, len(rows) + 1))
        frame = pd.DataFrame(rows)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.parquet"
            frame.to_parquet(source, index=False)
            buckets = MODULE.partition_sample(source, Path(directory), partitions=8,
                                              max_partition_rows=100)
            self.assertEqual(sum(n for _, n in buckets.values()), len(rows))
            for mid in ids:
                belonging = [p for p, _ in buckets.values()
                             if mid in set(pd.read_parquet(p).machine_id)]
                self.assertEqual(len(belonging), 1)
            for path, _ in buckets.values():
                windows, _, _ = MODULE.minute_to_windows(pd.read_parquet(path))
                self.assertEqual(len(windows), 2 * pd.read_parquet(path).machine_id.nunique())

    def test_two_and_four_slot_normalization_and_wrong_width_hour(self):
        first = self.day
        rows = []
        for machine, expected in (("two", 2), ("four", 4)):
            for minute_index in range(70):
                observed = expected * 2 if 5 <= minute_index < 65 else expected
                rows.append(minute(machine, first + minute_index * 60, len(rows) + 1,
                                   expected_slots=expected, observed_slots=observed))
        windows, quality, summary = MODULE.minute_to_windows(pd.DataFrame(rows))
        self.assertEqual(summary["width_mismatch_rows"], 120)
        self.assertEqual(summary["observed_window_slots"], 14 * (2 + 4))
        self.assertEqual(summary["valid_window_slots"], 2 + 4 + 2 + 4)
        self.assertEqual(quality.loc[quality.reason.eq("width_mismatch"), "minutes"].sum(),
                         60 * (2 + 4))
        self.assertEqual(quality.gpu_index.max(), 3)
        self.assertFalse(windows.loc[windows.expected_gpu_slots.eq(2), "valid_2"].any())
        frames, _, _, _ = MODULE.construct_candidates(windows, self.bounds)
        all_candidates = pd.concat(frames, ignore_index=True)
        self.assertFalse(all_candidates.gpu_index.ge(all_candidates.expected_gpu_slots).any())

    def test_scoring_labels_are_explicit_opt_in_and_memory_only(self):
        raw = dense_minutes("m1", self.day, 12)
        target = self.day + 2 * 300
        raw.loc[raw.time_utc_s.between(target + 600, target + 840), "gpu0_util"] = 90.0
        windows, _, _ = MODULE.minute_to_windows(raw)
        feature, future, labels, _ = MODULE.construct_candidates(windows, self.bounds)
        self.assertIsNone(labels)
        self.assertFalse(any(name.startswith("future_") or name == "y"
                             for name in pd.concat(feature).columns))
        self.assertFalse("y" in pd.concat(future).columns)
        replay_feature, replay_future, replay_labels, _ = MODULE.construct_candidates(
            windows, self.bounds, include_scoring_labels=True)
        self.assertIsNotNone(replay_labels)
        self.assertTrue(pd.concat(replay_feature, ignore_index=True).equals(
            pd.concat(feature, ignore_index=True)))
        self.assertTrue(pd.concat(replay_future, ignore_index=True).equals(
            pd.concat(future, ignore_index=True)))
        selected = pd.concat(replay_labels, ignore_index=True)
        row = selected.loc[selected.decision_utc_s.eq(target + 600)
                           & selected.gpu_index.eq(0)].iloc[0]
        self.assertEqual(int(row.y), 1)

    def test_incomplete_sample_rejected_before_read(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            MODULE.verify_promoted_source(Path("gpu_util_machine_hash5pct.parquet.incomplete"),
                                          Path("does_not_exist.json"))

    def test_synthetic_dry_run_writes_no_row_outcomes(self):
        chosen = next(f"chosen-{n}" for n in range(1000)
                      if MODULE.machine_selected(f"chosen-{n}"))
        frame = dense_minutes(chosen, self.day, 13)
        frame.loc[20, "observed_gpu_slots"] = 16
        frame.loc[20, "width_matches_first"] = False
        frame.loc[20, "row_usable"] = False
        frame.loc[20, "row_invalid_reason"] = "width_changed_after_first_observation"
        frame.loc[20, list(MODULE.GPU_COLUMNS)] = np.nan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / MODULE.SOURCE.name
            derived_audit = root / MODULE.AUDIT.name
            raw_audit = root / MODULE.SOURCE_AUDIT.name
            output = root / "prepared"
            raw_sample = root / "gpu_util_machine_hash5pct.parquet"
            frame.to_parquet(source, index=False)
            raw_sample.write_bytes(b"synthetic-original-sample-lineage")
            original_hash = MODULE.sha256_file(raw_sample)
            raw = {
                "complete": True, "integrity_pass": True,
                "compressed_sha256": MODULE.EXPECTED_SHA256,
                "compressed_bytes_read": MODULE.EXPECTED_SIZE,
                "http_content_length": MODULE.EXPECTED_SIZE,
                "sample_parquet": str(raw_sample), "sample_sha256": original_hash,
                "gpu_csv": {"selected_rows": len(frame),
                            "selected_machine_ids": [chosen],
                            "min_time_utc_s": 95 * 86400,
                            "max_time_utc_s": 130 * 86400},
                "tar_members": [{"name": name, "is_file": True}
                                for name in MODULE.EXPECTED_FILES],
            }
            raw_audit.write_text(json.dumps(raw))
            derived_audit.write_text(json.dumps({
                "integrity_pass": True, "source_sha256": original_hash,
                "source_archive_sha256": MODULE.EXPECTED_SHA256,
                "rows": len(frame), "invalid_rows": 1,
                "machines": {chosen: {"first_observed_gpu_slots": 8,
                                      "rows": len(frame), "invalid_rows": 1}},
                "source_parquet": str(raw_sample),
                "source_audit": str(raw_audit), "output_parquet": str(source),
                "output_sha256": MODULE.sha256_file(source),
                "normalization_script_sha256": MODULE.sha256_file(MODULE.NORMALIZER),
            }))
            result = MODULE.prepare(source, derived_audit, raw_audit, output,
                                    partitions=4, max_partition_rows=1000)
            features = pd.read_parquet(output / "candidates.parquet")
            future = pd.read_parquet(output / "future_observability.parquet")
            self.assertEqual(len(features), len(future))
            self.assertGreater(len(features), 0)
            self.assertFalse(any(name.startswith("future_") or name == "y"
                                 for name in features.columns))
            self.assertFalse("y" in future.columns)
            self.assertFalse((output / "labels.parquet").exists())
            self.assertEqual(result["outputs"]["candidates.parquet"]["rows"], len(features))
            self.assertEqual(result["source_selected_rows"], len(frame))
            self.assertFalse(result["width_quality_by_machine"][0]["passes_at_most_one_percent"])
            self.assertTrue(result["five_minute_input_quality_by_split"]["train"]["passes_at_least_half"])
            self.assertFalse(result["source_quality_gates_pass"])


if __name__ == "__main__":
    unittest.main()
