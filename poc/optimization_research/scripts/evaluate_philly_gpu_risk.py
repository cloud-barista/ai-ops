"""Score the frozen Philly GPU-slot risk protocol after a verified dry-run.

The CLI requires an explicitly reviewed dry-run SHA256. Running this script
before the protocol says FROZEN, or with changed source, candidate, or builder
files, stops before fitting any model. Labels are replayed only in memory from
the normalized source; the dry-run files never contain row-level outcomes.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import brier_score_loss

import prepare_philly_gpu_risk_candidates as builder
import philly_risk_metrics as metric_module
import philly_risk_models as model_module
from philly_risk_metrics import binary_metrics, paired_machine_bootstrap
from philly_risk_models import (
    BASELINE_KEYS, HIGH_THRESHOLD, fit_beta_table, select_beta_baseline,
    select_logistic,
)


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "reports/philly_gpu_risk_protocol_frozen.md"
DRAFT = ROOT / "reports/philly_gpu_risk_protocol_draft.md"
PREPARED = builder.OUTPUT
OUTPUT = ROOT / "artifacts/philly_gpu_risk_v1"
KEYS = ("machine_hash", "gpu_index", "decision_utc_s")
SPLITS = builder.SPLIT_NAMES
COHORTS = builder.COHORTS
STATES = builder.STATES
MODEL_COLUMNS = ("state_quality", "pooled_quality", "state_no_quality")
SCORE_COLUMNS = ("machine_hash", "gpu_index", "decision_utc_s", "utc_day",
                 "split", "cohort", "current_state", "current_util",
                 "older_valid", "middle_valid", "past_gap_s", "y")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash(path: Path) -> str:
    return builder.sha256_file(path)


def require_frozen_protocol(frozen_path: Path, draft_path: Path) -> str:
    """Require an actual FROZEN status and the exact referenced draft bytes."""
    text = frozen_path.read_text(encoding="utf-8")
    if not re.search(r"^\*\*상태:\s*FROZEN\b", text, flags=re.MULTILINE):
        raise ValueError("Philly protocol is not FROZEN")
    if re.search(r"\*\*PENDING\b", text):
        raise ValueError("Frozen Philly protocol still has pending items")
    draft_hash = _hash(draft_path)
    if draft_hash not in text:
        raise ValueError("Frozen Philly protocol does not pin the current draft SHA256")
    return text


def verify_dry_run_manifest(prepared: Path, expected_sha256: str,
                            frozen_text: str, *, source: Path = builder.SOURCE,
                            audit_path: Path = builder.AUDIT,
                            source_audit_path: Path = builder.SOURCE_AUDIT
                            ) -> tuple[dict, dict]:
    """Verify the reviewed aggregate audit and all saved artifacts before replay."""
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("--expected-dry-run-sha256 must be 64 lowercase hex digits")
    dry_path = prepared / "dry_run_audit.json"
    if _hash(dry_path) != expected_sha256:
        raise ValueError("Dry-run audit differs from the explicitly reviewed SHA256")
    dry = json.loads(dry_path.read_text(encoding="utf-8"))
    if dry.get("kind") != "audit_only_dry_run_no_model_fit_no_row_level_outcome":
        raise ValueError("Dry-run audit is not the score-free preparation artifact")
    if dry.get("preparation_script_sha256") != _hash(Path(builder.__file__)):
        raise ValueError("Candidate builder differs from the reviewed dry-run")
    audited = builder.verify_promoted_source(source, audit_path, source_audit_path)
    raw, normalized = audited["raw"], audited["normalized"]
    expected_fields = {
        "source_audit_sha256": _hash(source_audit_path),
        "normalization_audit_sha256": _hash(audit_path),
        "source_sample_sha256": raw["sample_sha256"],
        "normalized_sample_sha256": normalized["output_sha256"],
        "source_selected_rows": raw["gpu_csv"]["selected_rows"],
    }
    for name, expected in expected_fields.items():
        if dry.get(name) != expected:
            raise ValueError(f"Dry-run {name} differs from verified source lineage")
    for name in ("preparation_script_sha256", "normalized_sample_sha256",
                 "source_audit_sha256"):
        if str(dry[name]) not in frozen_text:
            raise ValueError(f"Frozen protocol does not pin dry-run {name}")
    for name, path in (("risk models", Path(model_module.__file__)),
                       ("risk metrics", Path(metric_module.__file__))):
        if _hash(path) not in frozen_text:
            raise ValueError(f"Frozen protocol does not pin current {name} code")
    bounds = builder.split_boundaries(raw["gpu_csv"]["min_time_utc_s"],
                                      raw["gpu_csv"]["max_time_utc_s"])
    if dry.get("source_raw_utc_extrema_only_split") != bounds:
        raise ValueError("Dry-run time split differs from raw UTC extrema")
    outputs = dry.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Dry-run lacks output artifact hashes")
    schemas = {
        "candidates.parquet": builder.CANDIDATE_SCHEMA,
        "future_observability.parquet": builder.FUTURE_OBSERVABILITY_SCHEMA,
        "future_observability_summary.parquet": builder.OBSERVABILITY_SCHEMA,
        "minute_quality.parquet": builder.MINUTE_QUALITY_SCHEMA,
        "window_quality.parquet": builder.WINDOW_QUALITY_SCHEMA,
    }
    if set(outputs) != set(schemas):
        raise ValueError("Dry-run output list differs from the five declared files")
    for name, schema in schemas.items():
        path = prepared / name
        recorded = outputs[name]
        if _hash(path) != recorded.get("sha256"):
            raise ValueError(f"Dry-run output hash mismatch: {name}")
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != recorded.get("rows"):
            raise ValueError(f"Dry-run output row-count mismatch: {name}")
        if parquet.schema_arrow.names != schema.names:
            raise ValueError(f"Dry-run output schema mismatch: {name}")
    if not bounds["duration_gate_pass"]:
        raise ValueError("Philly split duration gate failed")
    return dry, audited


def source_quality_gate(dry: dict, normalized: dict) -> dict:
    """Check first-width and five-minute input quality without using labels."""
    machines = normalized.get("machines", {})
    if not isinstance(machines, dict) or not machines:
        raise ValueError("Normalized lineage lacks per-machine width audit")
    machine_rows = []
    for machine, item in sorted(machines.items()):
        rows = int(item["rows"])
        invalid = int(item["invalid_rows"])
        slots = int(item["first_observed_gpu_slots"])
        if rows <= 0 or invalid < 0 or invalid > rows or slots not in (2, 4, 8):
            raise ValueError("Invalid normalized per-machine width audit")
        rate = invalid / rows
        machine_rows.append({"machine_hash": builder.machine_hash(machine),
                             "expected_gpu_slots": slots, "rows": rows,
                             "width_mismatch_rows": invalid,
                             "width_mismatch_fraction": rate,
                             "passes": rate <= 0.01})
    recorded_width = dry.get("width_quality_by_machine")
    if not isinstance(recorded_width, list) or len(recorded_width) != len(machine_rows):
        raise ValueError("Dry-run width-quality audit is missing machines")
    recorded_by_hash = {row.get("machine_hash"): row for row in recorded_width}
    if len(recorded_by_hash) != len(machine_rows):
        raise ValueError("Dry-run width-quality audit has duplicate machines")
    for row in machine_rows:
        saved = recorded_by_hash.get(row["machine_hash"])
        if saved is None or (int(saved["expected_gpu_slots"]) != row["expected_gpu_slots"]
                             or int(saved["raw_rows"]) != row["rows"]
                             or int(saved["width_mismatch_rows"]) != row["width_mismatch_rows"]
                             or not math.isclose(float(saved["width_mismatch_fraction"]),
                                                 row["width_mismatch_fraction"],
                                                 rel_tol=0, abs_tol=1e-12)
                             or saved.get("passes_at_most_one_percent") is not row["passes"]):
            raise ValueError("Dry-run width-quality gate differs from normalized lineage")
    quality = dry.get("five_minute_input_quality_by_split", {})
    split_rows = {}
    for split in SPLITS:
        item = quality.get(split)
        if not isinstance(item, dict):
            raise ValueError(f"Missing input quality for {split}")
        denominator = str(item.get("denominator", ""))
        if "window start is in split" not in denominator:
            raise ValueError("Input quality must be assigned by five-minute window start")
        observed = int(item["observed_window_slots"])
        valid = int(item["valid_window_slots"])
        if observed <= 0 or not 0 <= valid <= observed:
            raise ValueError(f"Invalid input-quality denominator for {split}")
        fraction = valid / observed
        if not math.isclose(fraction, float(item["valid_fraction"]), rel_tol=0, abs_tol=1e-12):
            raise ValueError("Input-quality fraction differs from dry-run counts")
        if item.get("passes_at_least_half") is not (fraction >= 0.50):
            raise ValueError("Saved input-quality gate flag differs from counts")
        split_rows[split] = {"observed_window_slots": observed,
                             "valid_window_slots": valid,
                             "valid_fraction": fraction,
                             "passes": fraction >= 0.50}
    result = {"per_machine": machine_rows, "by_split": split_rows,
              "passes": all(row["passes"] for row in machine_rows)
                        and all(row["passes"] for row in split_rows.values())}
    if dry.get("source_quality_gates_pass") is not result["passes"]:
        raise ValueError("Saved overall source-quality gate differs from counts")
    return result


def stratum_gate_status(dry: dict) -> dict[tuple[str, str], dict]:
    """Recompute numeric gates independently and compare saved check flags."""
    rows = dry.get("gate_counts_by_split_cohort_state")
    checks = dry.get("draft_gate_checks")
    if not isinstance(rows, list) or not isinstance(checks, list):
        raise ValueError("Dry-run is missing per-stratum counts or gate checks")
    index = {}
    for row in rows:
        key = (row["split"], row["cohort"], row["current_state"])
        if key in index:
            raise ValueError("Duplicate dry-run split/cohort/state gate row")
        index[key] = row
    check_index = {}
    for row in checks:
        key = (row["split"], row["cohort"], row["current_state"])
        if key in check_index:
            raise ValueError("Duplicate dry-run gate-check row")
        check_index[key] = row
    expected_keys = {(s, c, t) for s in SPLITS for c in COHORTS for t in STATES}
    if not set(index).issubset(expected_keys) or set(check_index) != expected_keys:
        raise ValueError("Dry-run gate keys differ from the protocol strata")
    result = {}
    for cohort in COHORTS:
        for state in STATES:
            by_split = {}
            for split in SPLITS:
                key = (split, cohort, state)
                row = index.get(key, {})
                candidate = int(row.get("candidates", 0))
                observed = int(row.get("future_complete", 0))
                pos = int(row.get("positive_gate_count", 0))
                neg = int(row.get("negative_gate_count", 0))
                machines = int(row.get("eligible_machine_count", 0))
                pos_machines = int(row.get("positive_machine_count", 0))
                neg_machines = int(row.get("negative_machine_count", 0))
                pos_days = int(row.get("positive_utc_day_count", 0))
                if (candidate < 0 or observed < 0 or observed > candidate
                        or pos < 0 or neg < 0 or pos + neg != observed
                        or not 0 <= pos_machines <= machines
                        or not 0 <= neg_machines <= machines):
                    raise ValueError(f"Inconsistent dry-run gate counts for {key}")
                if row:
                    recorded_fraction = row.get("future_observation_fraction")
                    if candidate:
                        if (recorded_fraction is None
                                or not math.isclose(float(recorded_fraction), observed / candidate,
                                                    rel_tol=0, abs_tol=1e-12)):
                            raise ValueError(f"Future observation fraction differs from counts for {key}")
                    elif recorded_fraction is not None:
                        raise ValueError(f"Zero-candidate fraction must be null for {key}")
                flags = {
                    "duration_at_least_three_days": dry["source_raw_utc_extrema_only_split"]["spans"][split]["days"] >= 3,
                    "future_observation_at_least_half": candidate > 0 and observed / candidate >= 0.50,
                    "eligible_at_least_2000": observed >= 2000,
                    "positive_at_least_100": pos >= 100,
                    "negative_at_least_100": neg >= 100,
                    "eligible_machines_at_least_10": machines >= 10,
                    "positive_machines_at_least_5": pos_machines >= 5,
                    "negative_machines_at_least_5": neg_machines >= 5,
                    "test_positives_on_five_days": split != "test" or pos_days >= 5,
                }
                passes = all(flags.values())
                recorded = check_index[key]
                for flag, value in flags.items():
                    if recorded.get(flag) is not value:
                        raise ValueError(f"Saved dry-run gate flag changed: {key} {flag}")
                if recorded.get("passes") is not passes:
                    raise ValueError(f"Saved dry-run pass flag changed: {key}")
                by_split[split] = {"counts": row, "checks": flags, "passes": passes}
            result[(cohort, state)] = {"by_split": by_split,
                                       "passes": all(x["passes"] for x in by_split.values())}
    return result


class ParquetSliceReader:
    """Consume saved dry-run rows in exact replay order without loading a full file."""

    def __init__(self, path: Path, columns: list[str]):
        self.columns = columns
        self.iter_batches = iter(pq.ParquetFile(path).iter_batches(
            batch_size=builder.READ_BATCH_ROWS, columns=columns))
        self.buffer = pd.DataFrame(columns=columns)

    def take(self, count: int) -> pd.DataFrame:
        if count < 0:
            raise ValueError("Negative replay row count")
        while len(self.buffer) < count:
            try:
                batch = next(self.iter_batches).to_pandas()
            except StopIteration as exc:
                raise ValueError("Saved dry-run Parquet ended before replay") from exc
            if self.buffer.empty:
                self.buffer = batch.reset_index(drop=True)
            else:
                self.buffer = pd.concat([self.buffer, batch], ignore_index=True)
        selected = self.buffer.iloc[:count].reset_index(drop=True)
        self.buffer = self.buffer.iloc[count:].reset_index(drop=True)
        return selected

    def finish(self) -> None:
        if not self.buffer.empty:
            raise ValueError("Saved dry-run Parquet has extra rows after replay")
        try:
            next(self.iter_batches)
        except StopIteration:
            return
        raise ValueError("Saved dry-run Parquet has extra rows after replay")


def validate_replayed_chunk(candidate: pd.DataFrame, future: pd.DataFrame,
                            labels: pd.DataFrame, bounds: dict) -> pd.DataFrame:
    """Check feature causality, time/cohort/state, and nullable-label alignment."""
    if list(candidate.columns) != builder.CANDIDATE_SCHEMA.names:
        raise ValueError("Candidate schema contains missing or future feature columns")
    if list(future.columns) != builder.FUTURE_OBSERVABILITY_SCHEMA.names:
        raise ValueError("Future-observability schema changed")
    if list(labels.columns) != [*KEYS, "y"]:
        raise ValueError("Replay labels must contain only the candidate key and y")
    if not (len(candidate) == len(future) == len(labels)):
        raise ValueError("Candidate, observability, and label lengths differ")
    if not candidate.loc[:, list(KEYS)].equals(future.loc[:, list(KEYS)]):
        raise ValueError("Future observability does not align with candidate keys")
    if not candidate.loc[:, list(KEYS)].equals(labels.loc[:, list(KEYS)]):
        raise ValueError("Replay labels do not align with candidate keys")
    if candidate.duplicated(list(KEYS)).any():
        raise ValueError("Duplicate candidate decision keys")
    n = len(candidate)
    if n == 0:
        return candidate.assign(y=pd.Series(dtype="Int8"))
    hashes = candidate.machine_hash.astype(str)
    if not hashes.str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("Candidate machine_hash is not lowercase SHA256 hex")
    slot = candidate.gpu_index.to_numpy(dtype=int)
    slots = candidate.expected_gpu_slots.to_numpy(dtype=int)
    if not np.isin(slots, (2, 4, 8)).all() or not ((0 <= slot) & (slot < slots)).all():
        raise ValueError("Candidate GPU slot is outside its first-row expected width")
    t = candidate.window_start_utc_s.to_numpy(dtype=np.int64)
    d = candidate.decision_utc_s.to_numpy(dtype=np.int64)
    if not np.array_equal(d, t + 600) or not np.array_equal(candidate.utc_day.to_numpy(dtype=np.int64), d // 86400):
        raise ValueError("Candidate decision time or UTC day disagrees with window start")
    split_ids = builder.split_for_decisions(d, bounds)
    if (split_ids < 0).any() or not np.array_equal(
            candidate.split.to_numpy(), np.asarray(SPLITS, dtype=object)[split_ids]):
        raise ValueError("Candidate split or label availability boundary changed")
    current = candidate.current_util.to_numpy(dtype=float)
    if not np.isfinite(current).all() or not ((0 <= current) & (current <= 100)).all():
        raise ValueError("Current GPU utilization outside [0,100]")
    old = candidate.older_valid.to_numpy(dtype=bool)
    middle = candidate.middle_valid.to_numpy(dtype=bool)
    cohort = np.where(old & middle, "complete", "partial_past")
    high = current >= HIGH_THRESHOLD
    state = np.where(high, "at_least_80", "below_80")
    if (not np.array_equal(candidate.cohort.to_numpy(), cohort)
            or not np.array_equal(candidate.current_state.to_numpy(), state)
            or not np.array_equal(candidate.current_high.to_numpy(bool), high)):
        raise ValueError("Candidate cohort or current-state definition changed")
    gap = np.where(middle, 300, np.where(old, 600, 900))
    if not np.array_equal(candidate.past_gap_s.to_numpy(int), gap):
        raise ValueError("Candidate past_gap_s disagrees with valid past windows")
    old_util = candidate.older_util.to_numpy(dtype=float)
    middle_util = candidate.middle_util.to_numpy(dtype=float)
    if (np.isfinite(old_util[~old]).any() or np.isfinite(middle_util[~middle]).any()
            or not np.isfinite(old_util[old]).all()
            or not np.isfinite(middle_util[middle]).all()):
        raise ValueError("Past utilization does not match observed validity flags")
    recent = high | (old & (old_util >= HIGH_THRESHOLD)) | (middle & (middle_util >= HIGH_THRESHOLD))
    if not np.array_equal(candidate.any_recent_high.to_numpy(bool), recent):
        raise ValueError("Candidate recent-high flag uses invalid or future data")
    flags = future[[f"future_valid_{i}" for i in range(6)]].to_numpy(dtype=bool)
    complete = flags.all(axis=1)
    if (not np.array_equal(future.future_valid_count.to_numpy(int), flags.sum(axis=1))
            or not np.array_equal(future.future_complete.to_numpy(bool), complete)):
        raise ValueError("Future-observability flags disagree with completeness")
    if not np.array_equal(labels.y.notna().to_numpy(), complete):
        raise ValueError("Replay label presence disagrees with future completeness")
    if not np.isin(labels.loc[complete, "y"].to_numpy(dtype=int), (0, 1)).all():
        raise ValueError("Observed Philly labels are not binary")
    result = candidate.copy()
    result["y"] = labels.y.to_numpy(dtype=np.int8, na_value=-1)
    return result.loc[complete].copy()


def _compare_saved(reader: ParquetSliceReader, replay: pd.DataFrame,
                   columns: list[str], description: str) -> None:
    saved = reader.take(len(replay))
    pd.testing.assert_frame_equal(saved[columns].reset_index(drop=True),
                                  replay[columns].reset_index(drop=True),
                                  check_dtype=False, check_exact=True,
                                  obj=f"Philly {description} replay")


def replay_labels(source: Path, dry: dict, prepared: Path,
                  *, workdir: Path) -> tuple[pd.DataFrame, dict]:
    """Replay exact saved features/observability, then retain observed labels."""
    partitioning = dry["partitioning"]
    partitions = int(partitioning["machine_hash_buckets"])
    max_rows = int(partitioning["max_partition_rows"])
    bounds = dry["source_raw_utc_extrema_only_split"]
    bucket_dir = workdir / "buckets"
    bucket_dir.mkdir()
    buckets = builder.partition_sample(source, bucket_dir, partitions, max_rows)
    if len(buckets) != partitioning["bucket_count_used"]:
        raise ValueError("Replay partition count differs from the dry-run")
    candidate_reader = ParquetSliceReader(prepared / "candidates.parquet",
                                          builder.CANDIDATE_SCHEMA.names)
    future_reader = ParquetSliceReader(prepared / "future_observability.parquet",
                                       builder.FUTURE_OBSERVABILITY_SCHEMA.names)
    summary_reader = ParquetSliceReader(prepared / "future_observability_summary.parquet",
                                        builder.OBSERVABILITY_SCHEMA.names)
    gates = builder.GateAccumulator()
    observed_frames = []
    totals: Counter[str] = Counter()
    input_quality = {name: Counter() for name in SPLITS}
    seen_machines: set[str] = set()
    for bucket, (path, expected_rows) in buckets.items():
        raw = pd.read_parquet(path)
        if len(raw) != expected_rows or len(raw) > max_rows:
            raise ValueError(f"Replay bucket {bucket} row count differs")
        windows, _, window_summary = builder.minute_to_windows(raw)
        del raw
        totals.update(window_summary)
        hashes = set(windows.machine_hash.unique()) if not windows.empty else set()
        if seen_machines & hashes:
            raise ValueError("One machine appeared in multiple replay buckets")
        seen_machines.update(hashes)
        window_quality = builder._window_quality(windows)
        for split, counts in builder.split_input_window_counts(window_quality, bounds).items():
            input_quality[split].update(counts)
        candidates, futures, labels, candidate_summary = builder.construct_candidates(
            windows, bounds, gates, include_scoring_labels=True)
        if labels is None or not (len(candidates) == len(futures) == len(labels)):
            raise AssertionError("Scoring replay did not return aligned labels")
        totals.update(candidate_summary)
        for candidate, future, label in zip(candidates, futures, labels):
            _compare_saved(candidate_reader, candidate,
                           builder.CANDIDATE_SCHEMA.names, "candidate")
            _compare_saved(future_reader, future,
                           builder.FUTURE_OBSERVABILITY_SCHEMA.names,
                           "future observability")
            summary = builder._observability(candidate, future)
            _compare_saved(summary_reader, summary,
                           builder.OBSERVABILITY_SCHEMA.names,
                           "observability summary")
            observed = validate_replayed_chunk(candidate, future, label, bounds)
            if not observed.empty:
                observed_frames.append(observed)
    for reader in (candidate_reader, future_reader, summary_reader):
        reader.finish()
    if gates.result() != dry["gate_counts_by_split_cohort_state"]:
        raise ValueError("Scoring replay changed the dry-run aggregate labels or cohorts")
    for name in ("raw_rows", "candidate_rows", "future_complete_rows"):
        if totals[name] != int(dry["totals"].get(name, -1)):
            raise ValueError(f"Scoring replay changed dry-run total {name}")
    for split in SPLITS:
        saved = dry["five_minute_input_quality_by_split"][split]
        for name in ("observed_window_slots", "valid_window_slots"):
            if int(input_quality[split][name]) != int(saved[name]):
                raise ValueError(f"Scoring replay changed input quality: {split} {name}")
    frame = pd.concat(observed_frames, ignore_index=True) if observed_frames else pd.DataFrame()
    if not frame.empty and frame.duplicated(list(KEYS)).any():
        raise ValueError("Same Philly decision appeared twice in scoring replay")
    return frame, {"replayed_bucket_count": len(buckets),
                   "observed_labeled_rows": len(frame),
                   "candidate_rows": int(totals["candidate_rows"])}


def fixed_half_brier(frame: pd.DataFrame, first: str, second: str,
                     test_span: dict) -> list[dict]:
    """Use the predeclared full UTC test calendar, including empty dates."""
    start = int(test_span["start_utc_day"])
    end = int(test_span["end_utc_day_exclusive"])
    midpoint = start + (end - start) // 2
    result = []
    for index, (lo, hi) in enumerate(((start, midpoint), (midpoint, end)), start=1):
        part = frame.loc[frame.utc_day.ge(lo) & frame.utc_day.lt(hi)]
        y = part.y.to_numpy(dtype=int)
        positives = int(y.sum())
        result.append({"half": index, "start_utc_day": lo,
                       "end_utc_day_exclusive": hi,
                       "rows": len(part), "positives": positives,
                       "negatives": len(part) - positives,
                       "first_brier": float(brier_score_loss(y, part[first])) if len(part) else None,
                       "second_brier": float(brier_score_loss(y, part[second])) if len(part) else None})
    return result


def grouped_brier(frame: pd.DataFrame, first: str, second: str,
                  group_key: str) -> list[dict]:
    """Expose date and machine support for the same paired test rows."""
    result = []
    for value, part in frame.groupby(group_key, sort=True):
        y = part.y.to_numpy(dtype=int)
        result.append({group_key: int(value) if group_key == "utc_day" else str(value),
                       "rows": len(part), "positives": int(y.sum()),
                       "negatives": int(len(y) - y.sum()),
                       "state_quality_brier": float(brier_score_loss(y, part[first])),
                       "selected_beta_brier": float(brier_score_loss(y, part[second]))})
    return result


def primary_criteria(state: dict, baseline: dict,
                     halves: list[dict]) -> dict:
    """Evaluate only the declared complete/current-normal success conditions."""
    if baseline["brier"] <= 0:
        raise ValueError("Baseline Brier must be positive for relative improvement")
    relative = (baseline["brier"] - state["brier"]) / baseline["brier"]
    half_evaluable = all(x["positives"] > 0 and x["negatives"] > 0 for x in halves)
    conditions = {
        "relative_brier_improvement_at_least_5pct": relative >= 0.05,
        "both_calendar_halves_lower_brier": (half_evaluable and all(
            x["first_brier"] < x["second_brier"] for x in halves)),
        "average_precision_not_lower": (state["average_precision"] is not None
                                         and baseline["average_precision"] is not None
                                         and state["average_precision"] >= baseline["average_precision"]),
        "daily_top5_recall_not_lower": (
            state["daily_top_5pct"]["recall"] is not None
            and baseline["daily_top_5pct"]["recall"] is not None
            and state["daily_top_5pct"]["recall"] >= baseline["daily_top_5pct"]["recall"]),
    }
    return {"relative_brier_improvement": relative,
            "half_period_evaluable": half_evaluable,
            "conditions": conditions, "passes": half_evaluable and all(conditions.values())}


def score_eligible_strata(examples: pd.DataFrame,
                          statuses: dict[tuple[str, str], dict],
                          bounds: dict) -> tuple[pd.DataFrame, dict]:
    """Fit only on train, select only on validation, score gated test strata."""
    passing = [key for key, entry in statuses.items() if entry["passes"]]
    results = {f"{cohort}/{state}": {"gate_pass": statuses[(cohort, state)]["passes"],
                                     "by_split": statuses[(cohort, state)]["by_split"]}
               for cohort in COHORTS for state in STATES}
    if not passing:
        return pd.DataFrame(), {"scoring_performed": False, "strata": results,
                                "primary": {"evaluable": False, "reason": "gate_failed"}}
    if examples.empty:
        raise ValueError("At least one stratum passed but replay has no labeled examples")
    train = examples.loc[examples.split.eq("train")]
    validation = examples.loc[examples.split.eq("validation")]
    test = examples.loc[examples.split.eq("test")]
    pooled = select_logistic(train, validation, include_quality=True)
    fitted_state = {}
    fitted_ablation = {}
    for state in {state for _, state in passing}:
        state_train = train.loc[train.current_state.eq(state)]
        state_val = validation.loc[validation.current_state.eq(state)]
        fitted_state[state] = select_logistic(state_train, state_val,
                                             include_quality=True)
        fitted_ablation[state] = select_logistic(state_train, state_val,
                                                include_quality=False)
    outputs = []
    for cohort, state in passing:
        key = f"{cohort}/{state}"
        train_cohort = train.loc[train.cohort.eq(cohort)]
        val_stratum = validation.loc[validation.cohort.eq(cohort)
                                     & validation.current_state.eq(state)]
        test_stratum = test.loc[test.cohort.eq(cohort)
                                & test.current_state.eq(state)].copy()
        selected, val_scores = select_beta_baseline(train_cohort, val_stratum)
        tables = {name: fit_beta_table(train_cohort, name, cols)
                  for name, cols in BASELINE_KEYS}
        p = test_stratum[list(SCORE_COLUMNS)].copy()
        p["state_quality"] = fitted_state[state].predict(test_stratum)
        p["pooled_quality"] = pooled.predict(test_stratum)
        p["state_no_quality"] = fitted_ablation[state].predict(test_stratum)
        for name, table in tables.items():
            p[f"beta_{name}"] = table.predict(test_stratum)
        p["selected_beta"] = p[f"beta_{selected.name}"]
        metrics = {name: binary_metrics(p, name)
                   for name in (*MODEL_COLUMNS, *(f"beta_{name}" for name, _ in BASELINE_KEYS),
                                "selected_beta")}
        comparisons = {}
        for comparator in ("selected_beta", "pooled_quality", "state_no_quality"):
            comparisons[f"state_quality_minus_{comparator}"] = paired_machine_bootstrap(
                p, "state_quality", comparator, draws=2000, seed=42)
        results[key].update({
            "selected_beta": selected.name,
            "validation_beta_brier": val_scores,
            "selected_model_c": {"state_quality": fitted_state[state].c,
                                 "pooled_quality": pooled.c,
                                 "state_no_quality": fitted_ablation[state].c},
            "validation_model_brier": {
                "state_quality": fitted_state[state].validation_brier,
                "pooled_quality": pooled.validation_brier,
                "state_no_quality": fitted_ablation[state].validation_brier},
            "training_medians": {
                "state_quality": [fitted_state[state].transform.older_median,
                                  fitted_state[state].transform.middle_median],
                "pooled_quality": [pooled.transform.older_median,
                                   pooled.transform.middle_median],
                "state_no_quality": [fitted_ablation[state].transform.older_median,
                                     fitted_ablation[state].transform.middle_median]},
            "metrics": metrics, "machine_bootstrap": comparisons,
            "brier_by_utc_day": grouped_brier(
                p, "state_quality", "selected_beta", "utc_day"),
            "brier_by_machine": grouped_brier(
                p, "state_quality", "selected_beta", "machine_hash"),
            "state_split_brier_lower_than_pooled": (
                metrics["state_quality"]["brier"] < metrics["pooled_quality"]["brier"]),
            "quality_mask_brier_lower_than_ablation": (
                metrics["state_quality"]["brier"] < metrics["state_no_quality"]["brier"]),
        })
        outputs.append(p)
    predictions = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
    main = results["complete/below_80"]
    if main["gate_pass"]:
        primary_frame = predictions.loc[predictions.cohort.eq("complete")
                                        & predictions.current_state.eq("below_80")]
        halves = fixed_half_brier(primary_frame, "state_quality", "selected_beta",
                                  bounds["spans"]["test"])
        verdict = primary_criteria(main["metrics"]["state_quality"],
                                   main["metrics"]["selected_beta"], halves)
        verdict.update({"evaluable": verdict["half_period_evaluable"],
                        "calendar_halves": halves,
                        "state_split_component_pass": main["state_split_brier_lower_than_pooled"]})
    else:
        verdict = {"evaluable": False, "reason": "complete_below80_gate_failed"}
    partial = results["partial_past/below_80"]
    if partial["gate_pass"]:
        verdict["partial_quality_component_pass"] = partial[
            "quality_mask_brier_lower_than_ablation"]
    else:
        verdict["partial_quality_component_pass"] = None
    return predictions, {"scoring_performed": True, "strata": results,
                         "primary": verdict}


def score(*, expected_dry_run_sha256: str, frozen_path: Path = FROZEN,
          draft_path: Path = DRAFT, prepared: Path = PREPARED,
          source: Path = builder.SOURCE, audit_path: Path = builder.AUDIT,
          source_audit_path: Path = builder.SOURCE_AUDIT,
          output: Path = OUTPUT) -> dict:
    """Verify, replay, gate, fit, and write a new immutable result directory."""
    frozen_text = require_frozen_protocol(frozen_path, draft_path)
    dry, audited = verify_dry_run_manifest(
        prepared, expected_dry_run_sha256, frozen_text,
        source=source, audit_path=audit_path, source_audit_path=source_audit_path)
    quality = source_quality_gate(dry, audited["normalized"])
    if not quality["passes"]:
        raise ValueError("Philly source-quality gate failed; no model may be fitted")
    statuses = stratum_gate_status(dry)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite Philly score output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".philly_score_", dir=output.parent) as tmp:
        tmpdir = Path(tmp)
        examples, replay = replay_labels(source, dry, prepared, workdir=tmpdir)
        predictions, scored = score_eligible_strata(
            examples, statuses, dry["source_raw_utc_extrema_only_split"])
        result = {
            "protocol_status": "FROZEN", "scoring_performed": scored["scoring_performed"],
            "frozen_protocol_sha256": _hash(frozen_path),
            "draft_protocol_sha256": _hash(draft_path),
            "dry_run_audit_sha256": expected_dry_run_sha256,
            "candidate_builder_sha256": dry["preparation_script_sha256"],
            "risk_models_sha256": _hash(Path(model_module.__file__)),
            "risk_metrics_sha256": _hash(Path(metric_module.__file__)),
            "scorer_sha256": _hash(Path(__file__)),
            "source_quality": quality, "replay": replay,
            "gate_status": {f"{cohort}/{state}": status
                            for (cohort, state), status in statuses.items()},
            "strata": scored["strata"], "primary": scored["primary"],
        }
        final = tmpdir / "result"
        final.mkdir()
        if not predictions.empty:
            prediction_path = final / "test_predictions.parquet"
            predictions.to_parquet(prediction_path, index=False)
            result["test_predictions_sha256"] = _hash(prediction_path)
            result["test_prediction_rows"] = len(predictions)
        (final / "metrics.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8")
        os.replace(final, output)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-dry-run-sha256", required=True)
    parser.add_argument("--frozen-protocol", type=Path, default=FROZEN)
    parser.add_argument("--draft-protocol", type=Path, default=DRAFT)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--source", type=Path, default=builder.SOURCE)
    parser.add_argument("--normalization-audit", type=Path, default=builder.AUDIT)
    parser.add_argument("--source-audit", type=Path, default=builder.SOURCE_AUDIT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = score(expected_dry_run_sha256=args.expected_dry_run_sha256,
                   frozen_path=args.frozen_protocol, draft_path=args.draft_protocol,
                   prepared=args.prepared, source=args.source,
                   audit_path=args.normalization_audit,
                   source_audit_path=args.source_audit, output=args.output)
    print(json.dumps({"output": str(args.output),
                      "scoring_performed": result["scoring_performed"],
                      "test_prediction_rows": result.get("test_prediction_rows", 0)},
                     ensure_ascii=False, sort_keys=True), flush=True)
