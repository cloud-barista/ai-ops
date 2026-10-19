"""Build Philly five-minute GPU-slot candidates and a score-free dry-run audit.

The promoted source sample and its complete source audit are mandatory. The
candidate Parquet contains only decision-time features; future observability
is kept in a separate Parquet. Neither contains future utilization, row-level
label, fitted probability, or score. Gate counts are aggregated in
dry_run_audit.json. No model is fitted here.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fetch_philly_gpu_sample import (
    EXPECTED_FILES, EXPECTED_SHA256, EXPECTED_SIZE, machine_selected,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/msr_philly_2017/gpu_util_normalized_first_width.parquet"
AUDIT = ROOT / "data/external/msr_philly_2017/gpu_util_normalized_first_width_audit.json"
SOURCE_AUDIT = ROOT / "data/external/msr_philly_2017/gpu_util_audit.json"
NORMALIZER = ROOT / "scripts/normalize_philly_gpu_sample.py"
OUTPUT = ROOT / "artifacts/philly_gpu_risk_candidates_v1"
GPU_COLUMNS = tuple(f"gpu{i}_util" for i in range(8))
INPUT_COLUMNS = ("source_row_index", "time_utc_s", "machine_id",
                 "expected_gpu_slots", "observed_gpu_slots", "first_observed_gpu_slots",
                 "width_matches_first", "row_usable", "row_invalid_reason", *GPU_COLUMNS)
WINDOW_SECONDS = 300
DECISION_DELAY_SECONDS = 600
LABEL_AVAILABLE_AFTER_DECISION_SECONDS = 2100
PAST_OFFSETS = (-600, -300)
FUTURE_OFFSETS = (600, 900, 1200, 1500, 1800, 2100)
HIGH_THRESHOLD = 80.0
DEFAULT_PARTITIONS = 64
DEFAULT_MAX_PARTITION_ROWS = 500_000
READ_BATCH_ROWS = 50_000
MIN_FREE_BYTES = 2_000_000_000
REASONS = ("valid", "duplicate_minute", "width_mismatch", "row_unusable", "null_na_or_empty",
           "below_zero", "above_100", "off_minute")
SPLIT_NAMES = ("train", "validation", "test")
COHORTS = ("complete", "partial_past")
STATES = ("below_80", "at_least_80")

CANDIDATE_SCHEMA = pa.schema([
    ("machine_hash", pa.string()), ("gpu_index", pa.int8()),
    ("window_start_utc_s", pa.int64()), ("decision_utc_s", pa.int64()),
    ("utc_day", pa.int64()), ("split", pa.string()),
    ("cohort", pa.string()), ("current_state", pa.string()),
    ("expected_gpu_slots", pa.int8()),
    ("older_util", pa.float64()), ("middle_util", pa.float64()),
    ("current_util", pa.float64()), ("older_valid", pa.bool_()),
    ("middle_valid", pa.bool_()), ("past_gap_s", pa.int16()),
    ("current_high", pa.bool_()), ("any_recent_high", pa.bool_()),
])
FUTURE_OBSERVABILITY_SCHEMA = pa.schema([
    ("machine_hash", pa.string()), ("gpu_index", pa.int8()),
    ("decision_utc_s", pa.int64()),
    *[(f"future_valid_{i}", pa.bool_()) for i in range(6)],
    ("future_valid_count", pa.int8()), ("future_complete", pa.bool_()),
])
MINUTE_QUALITY_SCHEMA = pa.schema([
    ("machine_hash", pa.string()), ("utc_day", pa.int64()),
    ("gpu_index", pa.int8()), ("reason", pa.string()), ("minutes", pa.int64()),
])
WINDOW_QUALITY_SCHEMA = pa.schema([
    ("machine_hash", pa.string()), ("utc_day", pa.int64()),
    ("gpu_index", pa.int8()), ("observed_windows", pa.int64()),
    ("valid_windows", pa.int64()),
])
OBSERVABILITY_SCHEMA = pa.schema([
    ("machine_hash", pa.string()), ("utc_day", pa.int64()),
    ("gpu_index", pa.int8()), ("split", pa.string()),
    ("cohort", pa.string()), ("current_state", pa.string()),
    ("candidates", pa.int64()), ("future_complete", pa.int64()),
])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def machine_hash(machine_id: str) -> str:
    return hashlib.sha256(machine_id.encode("utf-8")).hexdigest()


def bucket_for_machine(machine_id: str, partitions: int) -> int:
    """Keep all minutes of one machine in one bucket across all boundaries."""
    return int(machine_hash(machine_id)[:16], 16) % partitions


def require_free_space(path: Path) -> None:
    stat = os.statvfs(path)
    free = stat.f_bavail * stat.f_frsize
    if free < MIN_FREE_BYTES:
        raise RuntimeError(f"Less than 2 GB free for bounded Philly preparation: {free}")


def verify_promoted_source(source: Path, audit_path: Path,
                           source_audit_path: Path = SOURCE_AUDIT) -> dict:
    """Refuse partial or unverified derived samples before opening rows."""
    if source.suffix == ".incomplete" or ".incomplete" in source.name:
        raise ValueError("Incomplete Philly sample must never be used")
    if (source.name != SOURCE.name or audit_path.name != AUDIT.name
            or source_audit_path.name != SOURCE_AUDIT.name):
        raise ValueError("Expected promoted normalized source and both complete audit filenames")
    raw_audit = json.loads(source_audit_path.read_text(encoding="utf-8"))
    derived = json.loads(audit_path.read_text(encoding="utf-8"))
    if raw_audit.get("complete") is not True or raw_audit.get("integrity_pass") is not True:
        raise ValueError("Full Philly source audit has not passed")
    if (raw_audit.get("compressed_sha256") != EXPECTED_SHA256
            or raw_audit.get("compressed_bytes_read") != EXPECTED_SIZE
            or raw_audit.get("http_content_length") != EXPECTED_SIZE):
        raise ValueError("Pinned compressed source identity differs")
    members = {m.get("name") for m in raw_audit.get("tar_members", []) if m.get("is_file")}
    if members != EXPECTED_FILES:
        raise ValueError("Complete tar member inventory differs")
    gpu = raw_audit.get("gpu_csv", {})
    if not isinstance(gpu.get("selected_rows"), int) or gpu["selected_rows"] <= 0:
        raise ValueError("Complete audit has no selected-row count")
    if not isinstance(gpu.get("min_time_utc_s"), int) or not isinstance(gpu.get("max_time_utc_s"), int):
        raise ValueError("Complete audit lacks raw GPU timestamp limits")
    expected = raw_audit.get("sample_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("Complete source audit lacks promoted sample SHA256")
    raw_sample = Path(raw_audit.get("sample_parquet", ""))
    if (raw_sample.name != "gpu_util_machine_hash5pct.parquet"
            or ".incomplete" in raw_sample.name
            or not raw_sample.is_file()
            or sha256_file(raw_sample) != expected):
        raise ValueError("Promoted raw sample differs from its complete audit")
    if derived.get("integrity_pass") is not True:
        raise ValueError("Normalized source lineage audit has not passed")
    if (derived.get("source_sha256") != expected
            or derived.get("source_archive_sha256") != EXPECTED_SHA256
            or derived.get("rows") != gpu["selected_rows"]):
        raise ValueError("Normalized source does not match the pinned raw sample")
    if (derived.get("source_parquet") != raw_audit.get("sample_parquet")
            or derived.get("source_audit") != str(source_audit_path)
            or derived.get("output_parquet") != str(source)
            or derived.get("normalization_script_sha256") != sha256_file(NORMALIZER)):
        raise ValueError("Normalized source path or normalization code lineage differs")
    expected = derived.get("output_sha256")
    if not isinstance(expected, str) or len(expected) != 64 or sha256_file(source) != expected:
        raise ValueError("Normalized sample SHA256 differs from lineage audit")
    parquet = pq.ParquetFile(source)
    if parquet.metadata.num_rows != gpu["selected_rows"] or parquet.metadata.num_rows != derived["rows"]:
        raise ValueError("Normalized sample row count differs from source and lineage audits")
    if not set(INPUT_COLUMNS).issubset(parquet.schema_arrow.names):
        raise ValueError("Normalized sample lacks required slot and first-width fields")
    machines = derived.get("machines")
    if not isinstance(machines, dict) or set(machines) != set(gpu.get("selected_machine_ids", [])):
        raise ValueError("Normalized machine inventory differs from selected source machines")
    if (sum(info.get("rows", 0) for info in machines.values()) != derived["rows"]
            or sum(info.get("invalid_rows", 0) for info in machines.values()) != derived.get("invalid_rows")):
        raise ValueError("Normalized machine row counts differ from lineage totals")
    for info in machines.values():
        if (info.get("first_observed_gpu_slots") not in (2, 4, 8)
                or not isinstance(info.get("rows"), int) or info["rows"] <= 0
                or not isinstance(info.get("invalid_rows"), int)
                or not 0 <= info["invalid_rows"] <= info["rows"]):
            raise ValueError("Normalized machine width or invalid-row count is unsupported")
    return {"raw": raw_audit, "normalized": derived}


def split_boundaries(min_utc_s: int, max_utc_s: int) -> dict:
    """Use only raw UTC extrema; discard the days containing either extreme."""
    first_full_day = min_utc_s // 86400 + 1
    last_full_day_exclusive = max_utc_s // 86400
    days = max(0, last_full_day_exclusive - first_full_day)
    train_days = math.floor(0.60 * days)
    validation_end_days = math.floor(0.80 * days)
    day_edges = (first_full_day, first_full_day + train_days,
                 first_full_day + validation_end_days, last_full_day_exclusive)
    spans = {
        name: {"start_utc_s": day_edges[i] * 86400,
               "end_utc_s": day_edges[i + 1] * 86400,
               "start_utc_day": day_edges[i], "end_utc_day_exclusive": day_edges[i + 1],
               "days": day_edges[i + 1] - day_edges[i]}
        for i, name in enumerate(SPLIT_NAMES)
    }
    return {
        "raw_min_utc_s": min_utc_s, "raw_max_utc_s": max_utc_s,
        "first_full_utc_day": first_full_day,
        "last_full_utc_day_exclusive": last_full_day_exclusive,
        "full_utc_days": days, "spans": spans,
        "duration_gate_pass": all(span["days"] >= 3 for span in spans.values()),
    }


def split_for_decisions(decision_s: np.ndarray, bounds: dict) -> np.ndarray:
    result = np.full(len(decision_s), -1, dtype=np.int8)
    for index, name in enumerate(SPLIT_NAMES):
        span = bounds["spans"][name]
        inside = ((decision_s >= span["start_utc_s"])
                  & (decision_s + LABEL_AVAILABLE_AFTER_DECISION_SECONDS <= span["end_utc_s"]))
        result[inside] = index
    return result


def _quality_by_machine_day_slot(ordered: pd.DataFrame, duplicate: np.ndarray,
                                 machine_hashes: np.ndarray) -> tuple[pd.DataFrame, np.ndarray]:
    """Classify an observed machine/minute/slot once; NA and empty are merged."""
    n = len(ordered)
    if not n:
        return pd.DataFrame(columns=MINUTE_QUALITY_SCHEMA.names), np.empty((0, 8), bool)
    expected = ordered.expected_gpu_slots.to_numpy(int)
    width_matches = ordered.width_matches_first.to_numpy(bool)
    row_usable = ordered.row_usable.to_numpy(bool)
    values = ordered[list(GPU_COLUMNS)].to_numpy(dtype=float, na_value=np.nan)
    finite = np.isfinite(values)
    valid = finite & (values >= 0) & (values <= 100)
    valid &= row_usable[:, None] & (~duplicate)[:, None]
    valid &= (np.arange(8)[None, :] < expected[:, None])
    day = ordered.time_utc_s.to_numpy(np.int64) // 86400
    pieces = []
    for slot in range(8):
        exists = expected > slot
        if not exists.any():
            continue
        reason = np.where(duplicate[exists], "duplicate_minute",
                 np.where(~width_matches[exists], "width_mismatch",
                 np.where(~row_usable[exists], "row_unusable",
                 np.where(~finite[exists, slot], "null_na_or_empty",
                 np.where(values[exists, slot] < 0, "below_zero",
                 np.where(values[exists, slot] > 100, "above_100", "valid"))))))
        one = pd.DataFrame({"machine_hash": machine_hashes[exists], "utc_day": day[exists],
                            "gpu_index": np.int8(slot), "reason": reason})
        pieces.append(one.groupby(["machine_hash", "utc_day", "gpu_index", "reason"],
                                  sort=False).size().reset_index(name="minutes"))
    quality = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=MINUTE_QUALITY_SCHEMA.names)
    return quality, valid


def minute_to_windows(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Require exactly five distinct, valid, aligned minutes for each slot."""
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame(columns=MINUTE_QUALITY_SCHEMA.names), {
            "raw_rows": 0, "off_minute_rows": 0, "duplicate_minute_keys": 0,
            "irregular_width_rows": 0, "observed_window_keys": 0,
        }
    missing = set(INPUT_COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"Missing collector columns: {sorted(missing)}")
    if raw.machine_id.isna().any() or raw.time_utc_s.isna().any():
        raise ValueError("Collector sample has null machine or timestamp")
    time = pd.to_numeric(raw.time_utc_s, errors="coerce").to_numpy(float)
    if not np.isfinite(time).all() or not np.equal(np.mod(time, 1), 0).all():
        raise ValueError("Collector sample has invalid UTC seconds")
    ordered = raw.sort_values(["machine_id", "time_utc_s", "source_row_index"],
                              kind="mergesort").reset_index(drop=True)
    if ordered.source_row_index.duplicated().any():
        raise ValueError("Normalized source_row_index must be unique")
    if not ordered.expected_gpu_slots.isin((2, 4, 8)).all():
        raise ValueError("Normalized expected GPU slots must be 2, 4, or 8")
    if ordered.groupby("machine_id").expected_gpu_slots.nunique().gt(1).any():
        raise ValueError("Normalized first-width GPU count changed within a machine")
    if not ordered.first_observed_gpu_slots.eq(ordered.expected_gpu_slots).all():
        raise ValueError("Expected GPU slots must equal each machine's first observed width")
    matches = ordered.observed_gpu_slots.eq(ordered.expected_gpu_slots)
    if not matches.eq(ordered.width_matches_first).all():
        raise ValueError("Width-match flag disagrees with observed and expected GPU slots")
    if not ordered.row_usable.eq(ordered.width_matches_first).all():
        raise ValueError("Row usability disagrees with the normalized width rule")
    first_source = (ordered.sort_values(["machine_id", "source_row_index"], kind="mergesort")
                    .drop_duplicates("machine_id"))
    if not ((first_source.observed_gpu_slots.eq(first_source.expected_gpu_slots)
             & first_source.first_observed_gpu_slots.eq(first_source.expected_gpu_slots)
             & first_source.row_usable).all()):
        raise ValueError("First source row does not establish the expected GPU width")
    expected_all = ordered.expected_gpu_slots.to_numpy(int)
    values_all = ordered[list(GPU_COLUMNS)].to_numpy(dtype=float, na_value=np.nan)
    absent = np.arange(8)[None, :] >= expected_all[:, None]
    if np.isfinite(values_all[absent]).any():
        raise ValueError("Absent GPU slots must be null in normalized source")
    t = ordered.time_utc_s.to_numpy(np.int64)
    aligned = t % 60 == 0
    machine_ids = ordered.machine_id.astype(str).to_numpy()
    hash_by_machine = {mid: machine_hash(mid) for mid in np.unique(machine_ids)}
    hashes = np.array([hash_by_machine[mid] for mid in machine_ids], dtype=object)
    observed_blocks = pd.DataFrame({
        "machine_id": machine_ids, "machine_hash": hashes,
        "expected_gpu_slots": expected_all.astype(np.int8),
        "window_start_utc_s": (t // WINDOW_SECONDS) * WINDOW_SECONDS,
    }).drop_duplicates(["machine_id", "window_start_utc_s"]).reset_index(drop=True)
    off = ordered.loc[~aligned]
    off_quality = []
    if not off.empty:
        off_hashes = hashes[~aligned]
        off_days = t[~aligned] // 86400
        off_expected = expected_all[~aligned]
        for slot in range(8):
            exists = off_expected > slot
            if not exists.any():
                continue
            one = pd.DataFrame({"machine_hash": off_hashes[exists], "utc_day": off_days[exists],
                                "gpu_index": np.int8(slot), "reason": "off_minute"})
            off_quality.append(one.groupby(["machine_hash", "utc_day", "gpu_index", "reason"],
                                           sort=False).size().reset_index(name="minutes"))
    aligned_rows = ordered.loc[aligned].reset_index(drop=True)
    aligned_hashes = hashes[aligned]
    if aligned_rows.empty:
        q = pd.concat(off_quality, ignore_index=True) if off_quality else pd.DataFrame(columns=MINUTE_QUALITY_SCHEMA.names)
        result = observed_blocks.copy()
        result["observed_minute_keys"] = np.int8(0)
        for slot in range(8):
            result[f"valid_{slot}"] = False
            result[f"util_{slot}"] = np.nan
        return result, q, {"raw_rows": len(raw), "off_minute_rows": int((~aligned).sum()),
                                   "duplicate_minute_keys": 0,
                                   "width_mismatch_rows": int((~ordered.width_matches_first).sum()),
                                   "row_unusable_rows": int((~ordered.row_usable).sum()),
                                   "absent_slot_cells_excluded": int((8 - expected_all).sum()),
                                   "observed_window_keys": int(len(result)),
                                   "valid_window_slots": 0,
                                   "observed_window_slots": int(result.expected_gpu_slots.sum())}
    a_t = aligned_rows.time_utc_s.to_numpy(np.int64)
    a_machine = aligned_rows.machine_id.astype(str).to_numpy()
    same_prev = np.r_[False, (a_machine[1:] == a_machine[:-1]) & (a_t[1:] == a_t[:-1])]
    same_next = np.r_[(a_machine[1:] == a_machine[:-1]) & (a_t[1:] == a_t[:-1]), False]
    duplicate_all = same_prev | same_next
    first = np.flatnonzero(~same_prev)
    unique = aligned_rows.iloc[first].reset_index(drop=True)
    duplicate_keys = duplicate_all[first]
    unique_hashes = aligned_hashes[first]
    minute_quality, slot_valid = _quality_by_machine_day_slot(unique, duplicate_keys, unique_hashes)
    if off_quality:
        minute_quality = pd.concat([minute_quality, *off_quality], ignore_index=True)
    u_time = unique.time_utc_s.to_numpy(np.int64)
    u_machine = unique.machine_id.astype(str).to_numpy()
    window_start = (u_time // WINDOW_SECONDS) * WINDOW_SECONDS
    group_start = np.r_[0, np.flatnonzero((u_machine[1:] != u_machine[:-1])
                                         | (window_start[1:] != window_start[:-1])) + 1]
    group_end = np.r_[group_start[1:], len(unique)]
    if np.any(group_end - group_start > 5):
        raise AssertionError("More than five distinct aligned minutes in one five-minute window")
    values = unique[list(GPU_COLUMNS)].to_numpy(dtype=float, na_value=np.nan)
    valid_count = np.add.reduceat(slot_valid.astype(np.int8), group_start, axis=0)
    valid_sum = np.add.reduceat(np.where(slot_valid, values, 0.0), group_start, axis=0)
    window_valid = valid_count == 5
    result = pd.DataFrame({"machine_id": u_machine[group_start],
                           "window_start_utc_s": window_start[group_start],
                           "observed_minute_keys": (group_end - group_start).astype(np.int8)})
    for slot in range(8):
        result[f"valid_{slot}"] = window_valid[:, slot]
        result[f"util_{slot}"] = np.where(window_valid[:, slot], valid_sum[:, slot] / 5.0, np.nan)
    result = observed_blocks.merge(result, on=["machine_id", "window_start_utc_s"],
                                   how="left", validate="one_to_one", sort=False)
    result["observed_minute_keys"] = result.observed_minute_keys.fillna(0).astype(np.int8)
    for slot in range(8):
        result[f"valid_{slot}"] = result[f"valid_{slot}"].astype("boolean").fillna(False).astype(bool)
    result = result.sort_values(["machine_id", "window_start_utc_s"], kind="mergesort").reset_index(drop=True)
    summary = {
        "raw_rows": int(len(raw)), "off_minute_rows": int((~aligned).sum()),
        "duplicate_minute_rows": int(duplicate_all.sum()),
        "duplicate_minute_keys": int(duplicate_keys.sum()),
        "width_mismatch_rows": int((~ordered.width_matches_first).sum()),
        "row_unusable_rows": int((~ordered.row_usable).sum()),
        "absent_slot_cells_excluded": int((8 - expected_all).sum()),
        "observed_window_keys": int(len(result)),
        "valid_window_slots": int(sum(result[f"valid_{slot}"].sum() for slot in range(8))),
        "observed_window_slots": int(result.expected_gpu_slots.sum()),
    }
    return result, minute_quality, summary


def _lookup(keys: np.ndarray, machine_code: np.ndarray,
            window_start: np.ndarray, offset: int) -> tuple[np.ndarray, np.ndarray]:
    query = np.empty(len(window_start), dtype=keys.dtype)
    query["machine"] = machine_code
    query["window"] = window_start + offset
    pos = np.searchsorted(keys, query, side="left")
    safe = np.minimum(pos, len(keys) - 1)
    found = (pos < len(keys)) & (keys[safe] == query)
    return safe, found


class GateAccumulator:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict] = defaultdict(lambda: {
            "candidates": 0, "future_complete": 0, "positive": 0, "negative": 0,
            "machines": set(), "positive_machines": set(), "negative_machines": set(),
            "positive_days": set(),
        })

    def add(self, frame: pd.DataFrame, future_complete: np.ndarray,
            positive_if_observed: np.ndarray) -> None:
        if frame.empty:
            return
        if len(positive_if_observed) != len(frame) or len(future_complete) != len(frame):
            raise ValueError("Internal outcome length differs from candidates")
        for key, positions in frame.groupby(["split", "cohort", "current_state"], sort=False).indices.items():
            part = frame.iloc[positions]
            observed = future_complete[positions]
            positives = observed & positive_if_observed[positions]
            negatives = observed & ~positive_if_observed[positions]
            row = self.rows[key]
            row["candidates"] += len(part)
            row["future_complete"] += int(observed.sum())
            row["positive"] += int(positives.sum())
            row["negative"] += int(negatives.sum())
            row["machines"].update(part.loc[observed, "machine_hash"].unique())
            row["positive_machines"].update(part.loc[positives, "machine_hash"].unique())
            row["negative_machines"].update(part.loc[negatives, "machine_hash"].unique())
            row["positive_days"].update(int(v) for v in part.loc[positives, "utc_day"].unique())

    def result(self) -> list[dict]:
        result = []
        for key in sorted(self.rows):
            row = self.rows[key]
            candidates, observed = row["candidates"], row["future_complete"]
            result.append({
                "split": key[0], "cohort": key[1], "current_state": key[2],
                "candidates": candidates, "future_complete": observed,
                "future_observation_fraction": observed / candidates if candidates else None,
                "positive_gate_count": row["positive"],
                "negative_gate_count": row["negative"],
                "eligible_machine_count": len(row["machines"]),
                "positive_machine_count": len(row["positive_machines"]),
                "negative_machine_count": len(row["negative_machines"]),
                "positive_utc_day_count": len(row["positive_days"]),
            })
        return result


def construct_candidates(windows: pd.DataFrame, bounds: dict,
                         gates: GateAccumulator | None = None,
                         *, include_scoring_labels: bool = False
                         ) -> tuple[list[pd.DataFrame], list[pd.DataFrame],
                                    list[pd.DataFrame] | None, dict]:
    """Define cohorts before outcomes; labels are opt-in and memory-only.

    The dry-run always passes include_scoring_labels=False. A later, separately
    frozen scorer may replay the same normalized source with True, compare the
    decision-feature file hash, and receive keyed nullable labels in memory.
    """
    if windows.empty:
        return [], [], [] if include_scoring_labels else None, {
            "current_valid_before_split": 0, "excluded_by_split_boundary": 0}
    frame = windows.sort_values(["machine_id", "window_start_utc_s"], kind="mergesort").reset_index(drop=True)
    if frame.duplicated(["machine_id", "window_start_utc_s"]).any():
        raise ValueError("Five-minute window keys must be unique")
    code, _ = pd.factorize(frame.machine_id, sort=False)
    t_all = frame.window_start_utc_s.to_numpy(np.int64)
    keys = np.empty(len(frame), dtype=[("machine", "<i8"), ("window", "<i8")])
    keys["machine"], keys["window"] = code, t_all
    if len(keys) > 1 and np.any((code[1:] < code[:-1])
                                | ((code[1:] == code[:-1]) & (t_all[1:] < t_all[:-1]))):
        raise AssertionError("Window lookup keys are not sorted")
    candidates: list[pd.DataFrame] = []
    observability: list[pd.DataFrame] = []
    labels: list[pd.DataFrame] | None = [] if include_scoring_labels else None
    summary = Counter()
    for slot in range(8):
        idx = np.flatnonzero(frame[f"valid_{slot}"].to_numpy(bool))
        summary["current_valid_before_split"] += len(idx)
        if not len(idx):
            continue
        d = t_all[idx] + DECISION_DELAY_SECONDS
        split_ids = split_for_decisions(d, bounds)
        summary["excluded_by_split_boundary"] += int((split_ids < 0).sum())
        keep = split_ids >= 0
        idx, d, split_ids = idx[keep], d[keep], split_ids[keep]
        if not len(idx):
            continue
        base_time = t_all[idx]
        machine_code = code[idx]
        past_valid = []
        past_util = []
        for offset in PAST_OFFSETS:
            pos, found = _lookup(keys, machine_code, base_time, offset)
            valid = found & frame[f"valid_{slot}"].to_numpy(bool)[pos]
            util = np.where(valid, frame[f"util_{slot}"].to_numpy(float)[pos], np.nan)
            past_valid.append(valid)
            past_util.append(util)
        current_util = frame[f"util_{slot}"].to_numpy(float)[idx]
        current_high = current_util >= HIGH_THRESHOLD
        complete_past = past_valid[0] & past_valid[1]
        cohort = np.where(complete_past, COHORTS[0], COHORTS[1])
        state = np.where(current_high, STATES[1], STATES[0])
        any_recent = current_high.copy()
        for valid, util in zip(past_valid, past_util):
            any_recent |= valid & (util >= HIGH_THRESHOLD)
        future_valid = []
        future_high = []
        util_column = frame[f"util_{slot}"].to_numpy(float)
        valid_column = frame[f"valid_{slot}"].to_numpy(bool)
        for offset in FUTURE_OFFSETS:
            pos, found = _lookup(keys, machine_code, base_time, offset)
            valid = found & valid_column[pos]
            future_valid.append(valid)
            # This temporary gate bit never goes into the row-level artifact.
            future_high.append(valid & (util_column[pos] >= HIGH_THRESHOLD))
        observed = np.logical_and.reduce(future_valid)
        gate_positive = observed & np.logical_or.reduce(future_high)
        machine_hashes = frame.machine_hash.to_numpy(str)[idx]
        output = pd.DataFrame({
            "machine_hash": machine_hashes, "gpu_index": np.int8(slot),
            "window_start_utc_s": base_time, "decision_utc_s": d,
            "utc_day": d // 86400,
            "split": np.asarray(SPLIT_NAMES, dtype=object)[split_ids],
            "cohort": cohort, "current_state": state,
            "expected_gpu_slots": frame.expected_gpu_slots.to_numpy(np.int8)[idx],
            "older_util": past_util[0], "middle_util": past_util[1],
            "current_util": current_util,
            "older_valid": past_valid[0], "middle_valid": past_valid[1],
            "past_gap_s": np.where(past_valid[1], 300,
                                   np.where(past_valid[0], 600, 900)).astype(np.int16),
            "current_high": current_high, "any_recent_high": any_recent,
        })[CANDIDATE_SCHEMA.names]
        future = pd.DataFrame({
            "machine_hash": machine_hashes, "gpu_index": np.int8(slot),
            "decision_utc_s": d,
            "future_valid_count": np.sum(future_valid, axis=0).astype(np.int8),
            "future_complete": observed,
        })
        for i, flag in enumerate(future_valid):
            future[f"future_valid_{i}"] = flag
        future = future[FUTURE_OBSERVABILITY_SCHEMA.names]
        if gates is not None:
            gates.add(output, observed, gate_positive)
        if labels is not None:
            y = pd.Series(pd.NA, index=range(len(output)), dtype="Int8")
            y.loc[observed] = gate_positive[observed].astype(np.int8)
            labels.append(pd.DataFrame({
                "machine_hash": machine_hashes, "gpu_index": np.int8(slot),
                "decision_utc_s": d, "y": y,
            }))
        candidates.append(output)
        observability.append(future)
        summary["candidate_rows"] += len(output)
        summary["future_complete_rows"] += int(observed.sum())
    return candidates, observability, labels, dict(summary)


def _write_frame(writer: pq.ParquetWriter, frame: pd.DataFrame, schema: pa.Schema) -> None:
    if not frame.empty:
        table = pa.Table.from_pandas(frame[schema.names], schema=schema, preserve_index=False)
        writer.write_table(table, row_group_size=READ_BATCH_ROWS)


def _window_quality(windows: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    if windows.empty:
        return pd.DataFrame(columns=WINDOW_QUALITY_SCHEMA.names)
    day = windows.window_start_utc_s.to_numpy(np.int64) // 86400
    for slot in range(8):
        exists = windows.expected_gpu_slots.to_numpy(int) > slot
        if not exists.any():
            continue
        part = pd.DataFrame({"machine_hash": windows.machine_hash.to_numpy()[exists],
                             "utc_day": day[exists], "gpu_index": np.int8(slot),
                             "observed_windows": 1,
                             "valid_windows": windows[f"valid_{slot}"].to_numpy(np.int64)[exists]})
        pieces.append(part.groupby(["machine_hash", "utc_day", "gpu_index"],
                                   sort=False)[["observed_windows", "valid_windows"]].sum().reset_index())
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=WINDOW_QUALITY_SCHEMA.names)


def split_input_window_counts(window_quality: pd.DataFrame, bounds: dict) -> dict[str, Counter[str]]:
    """Use window starts, including decision-boundary rows, for input quality."""
    counts: dict[str, Counter[str]] = {split: Counter() for split in SPLIT_NAMES}
    for split in SPLIT_NAMES:
        span = bounds["spans"][split]
        in_split = window_quality.utc_day.ge(span["start_utc_day"]) & window_quality.utc_day.lt(
            span["end_utc_day_exclusive"])
        part = window_quality.loc[in_split]
        counts[split]["observed_window_slots"] = int(part.observed_windows.sum())
        counts[split]["valid_window_slots"] = int(part.valid_windows.sum())
    return counts


def _observability(frame: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=OBSERVABILITY_SCHEMA.names)
    if len(frame) != len(future):
        raise ValueError("Decision and future-observability rows differ")
    joined = frame[["machine_hash", "utc_day", "gpu_index", "split",
                    "cohort", "current_state"]].copy()
    joined["future_complete"] = future.future_complete.to_numpy(bool)
    result = joined.groupby(["machine_hash", "utc_day", "gpu_index", "split",
                             "cohort", "current_state"], sort=False).agg(
        candidates=("future_complete", "size"),
        future_complete=("future_complete", "sum"),
    ).reset_index()
    return result[OBSERVABILITY_SCHEMA.names]


def partition_sample(source: Path, directory: Path, partitions: int,
                     max_partition_rows: int) -> dict[int, tuple[Path, int]]:
    """Stream the wide source once; each machine is wholly in one disk bucket."""
    if partitions < 1 or partitions > 512:
        raise ValueError("partitions must be between 1 and 512")
    parquet = pq.ParquetFile(source)
    schema = pa.schema([parquet.schema_arrow.field(name) for name in INPUT_COLUMNS])
    writers: dict[int, pq.ParquetWriter] = {}
    paths: dict[int, Path] = {}
    counts: Counter[int] = Counter()
    cache: dict[str, int] = {}
    try:
        for batch in parquet.iter_batches(batch_size=READ_BATCH_ROWS, columns=list(INPUT_COLUMNS)):
            frame = batch.to_pandas()
            for mid in frame.machine_id.unique():
                mid = str(mid)
                if not machine_selected(mid):
                    raise ValueError("Promoted sample includes a machine outside the fixed 5% rule")
                cache.setdefault(mid, bucket_for_machine(mid, partitions))
            b = frame.machine_id.map(cache).to_numpy(int)
            for index in np.unique(b):
                selected = frame.loc[b == index, list(INPUT_COLUMNS)]
                counts[int(index)] += len(selected)
                if counts[int(index)] > max_partition_rows:
                    raise MemoryError("Machine bucket exceeds max_partition_rows; rerun with more partitions")
                if index not in writers:
                    path = directory / f"bucket_{int(index):03d}.parquet"
                    paths[int(index)] = path
                    writers[int(index)] = pq.ParquetWriter(path, schema, compression="zstd")
                _write_frame(writers[int(index)], selected, schema)
            require_free_space(directory)
    finally:
        for writer in writers.values():
            writer.close()
    if sum(counts.values()) != parquet.metadata.num_rows:
        raise AssertionError("Partitioning changed selected sample row count")
    return {index: (paths[index], counts[index]) for index in sorted(paths)}


def _gate_checks(rows: list[dict], bounds: dict) -> list[dict]:
    index = {(r["split"], r["cohort"], r["current_state"]): r for r in rows}
    checks = []
    for split in SPLIT_NAMES:
        for cohort in COHORTS:
            for state in STATES:
                row = index.get((split, cohort, state), {})
                observed = int(row.get("future_complete", 0))
                positive = int(row.get("positive_gate_count", 0))
                negative = int(row.get("negative_gate_count", 0))
                candidate = int(row.get("candidates", 0))
                check = {
                    "split": split, "cohort": cohort, "current_state": state,
                    "duration_at_least_three_days": bounds["spans"][split]["days"] >= 3,
                    "future_observation_at_least_half": candidate > 0 and observed / candidate >= 0.50,
                    "eligible_at_least_2000": observed >= 2000,
                    "positive_at_least_100": positive >= 100,
                    "negative_at_least_100": negative >= 100,
                    "eligible_machines_at_least_10": row.get("eligible_machine_count", 0) >= 10,
                    "positive_machines_at_least_5": row.get("positive_machine_count", 0) >= 5,
                    "negative_machines_at_least_5": row.get("negative_machine_count", 0) >= 5,
                    "test_positives_on_five_days": (
                        split != "test" or row.get("positive_utc_day_count", 0) >= 5
                    ),
                }
                check["passes"] = all(value for key, value in check.items()
                                      if key not in ("split", "cohort", "current_state", "passes"))
                checks.append(check)
    return checks


def prepare(source: Path = SOURCE, audit_path: Path = AUDIT,
            source_audit_path: Path = SOURCE_AUDIT, output: Path = OUTPUT,
            partitions: int = DEFAULT_PARTITIONS,
            max_partition_rows: int = DEFAULT_MAX_PARTITION_ROWS) -> dict:
    """Write only candidate inputs, observability, and pre-score gate aggregates."""
    audits = verify_promoted_source(source, audit_path, source_audit_path)
    raw_audit, normalized_audit = audits["raw"], audits["normalized"]
    bounds = split_boundaries(raw_audit["gpu_csv"]["min_time_utc_s"],
                              raw_audit["gpu_csv"]["max_time_utc_s"])
    if not bounds["duration_gate_pass"]:
        raise ValueError("Philly UTC span has fewer than three full days in a split")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite Philly candidate output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    require_free_space(output.parent)
    with tempfile.TemporaryDirectory(prefix=".philly_candidates_", dir=output.parent) as temporary:
        root = Path(temporary)
        buckets_dir = root / "buckets"
        final_dir = root / "prepared"
        buckets_dir.mkdir()
        final_dir.mkdir()
        buckets = partition_sample(source, buckets_dir, partitions, max_partition_rows)
        gates = GateAccumulator()
        totals: Counter[str] = Counter()
        minute_reasons: Counter[str] = Counter()
        input_window_quality: dict[str, Counter[str]] = {
            split: Counter() for split in SPLIT_NAMES
        }
        with (pq.ParquetWriter(final_dir / "candidates.parquet", CANDIDATE_SCHEMA, compression="zstd") as candidate_writer,
              pq.ParquetWriter(final_dir / "minute_quality.parquet", MINUTE_QUALITY_SCHEMA, compression="zstd") as minute_writer,
              pq.ParquetWriter(final_dir / "window_quality.parquet", WINDOW_QUALITY_SCHEMA, compression="zstd") as window_writer,
              pq.ParquetWriter(final_dir / "future_observability.parquet", FUTURE_OBSERVABILITY_SCHEMA, compression="zstd") as future_writer,
              pq.ParquetWriter(final_dir / "future_observability_summary.parquet", OBSERVABILITY_SCHEMA, compression="zstd") as obs_writer):
            for bucket, (path, expected_rows) in buckets.items():
                raw = pd.read_parquet(path)
                if len(raw) != expected_rows or len(raw) > max_partition_rows:
                    raise AssertionError(f"Bucket {bucket} exceeded its verified row count")
                windows, quality, summary = minute_to_windows(raw)
                del raw
                for name, value in summary.items():
                    totals[name] += value
                for reason, count in quality.groupby("reason")["minutes"].sum().items():
                    minute_reasons[str(reason)] += int(count)
                _write_frame(minute_writer, quality, MINUTE_QUALITY_SCHEMA)
                window_quality = _window_quality(windows)
                _write_frame(window_writer, window_quality, WINDOW_QUALITY_SCHEMA)
                bucket_quality = split_input_window_counts(window_quality, bounds)
                for split in SPLIT_NAMES:
                    input_window_quality[split].update(bucket_quality[split])
                frames, future_frames, label_frames, candidate_summary = construct_candidates(
                    windows, bounds, gates, include_scoring_labels=False)
                if label_frames is not None:
                    raise AssertionError("Dry-run must not construct row-level labels")
                del windows
                for name, value in candidate_summary.items():
                    totals[name] += value
                for frame, future in zip(frames, future_frames):
                    _write_frame(candidate_writer, frame, CANDIDATE_SCHEMA)
                    _write_frame(future_writer, future, FUTURE_OBSERVABILITY_SCHEMA)
                    _write_frame(obs_writer, _observability(frame, future), OBSERVABILITY_SCHEMA)
                require_free_space(root)
        if totals["raw_rows"] != raw_audit["gpu_csv"]["selected_rows"]:
            raise AssertionError("Prepared raw row count differs from complete source audit")
        gate_rows = gates.result()
        width_by_machine = []
        for machine_id, info in sorted(normalized_audit["machines"].items()):
            invalid_fraction = info["invalid_rows"] / info["rows"]
            width_by_machine.append({
                "machine_hash": machine_hash(machine_id),
                "expected_gpu_slots": info["first_observed_gpu_slots"],
                "raw_rows": info["rows"],
                "width_mismatch_rows": info["invalid_rows"],
                "width_mismatch_fraction": invalid_fraction,
                "passes_at_most_one_percent": invalid_fraction <= 0.01,
            })
        input_quality_by_split = {}
        for split, counts in input_window_quality.items():
            observed = counts["observed_window_slots"]
            valid = counts["valid_window_slots"]
            fraction = valid / observed if observed else None
            input_quality_by_split[split] = {
                "denominator": "raw-row-observed machine × 5min UTC block × expected GPU slot whose window start is in split",
                "observed_window_slots": observed,
                "valid_window_slots": valid,
                "valid_fraction": fraction,
                "passes_at_least_half": fraction is not None and fraction >= 0.50,
            }
        result = {
            "kind": "audit_only_dry_run_no_model_fit_no_row_level_outcome",
            "source_audit_sha256": sha256_file(source_audit_path),
            "normalization_audit_sha256": sha256_file(audit_path),
            "source_sample_sha256": raw_audit["sample_sha256"],
            "normalized_sample_sha256": normalized_audit["output_sha256"],
            "preparation_script_sha256": sha256_file(Path(__file__)),
            "source_selected_rows": raw_audit["gpu_csv"]["selected_rows"],
            "source_raw_utc_extrema_only_split": bounds,
            "partitioning": {"machine_hash_buckets": partitions,
                             "max_partition_rows": max_partition_rows,
                             "bucket_count_used": len(buckets),
                             "max_bucket_rows_observed": max((n for _, n in buckets.values()), default=0)},
            "totals": dict(sorted(totals.items())),
            "minute_reason_counts": dict(sorted(minute_reasons.items())),
            "width_quality_by_machine": width_by_machine,
            "five_minute_input_quality_by_split": input_quality_by_split,
            "source_quality_gates_pass": (
                all(row["passes_at_most_one_percent"] for row in width_by_machine)
                and all(row["passes_at_least_half"] for row in input_quality_by_split.values())
            ),
            "gate_counts_by_split_cohort_state": gate_rows,
            "draft_gate_checks": _gate_checks(gate_rows, bounds),
            "limitations": [
                "Raw terminal empty fields are CSV delimiters for 2/4-GPU rows. Expected-slot nulls come from NA or invalid rows; the source 8-wide encoding could merge NA and empty, but the full-source audit found no extra empty tokens.",
                "The draft protocol must be frozen separately before any predictive scoring.",
                "Gate counts are aggregate only; candidates contain no future flags, future utilization or row-level label.",
            ],
        }
        files = ("candidates.parquet", "minute_quality.parquet", "window_quality.parquet",
                 "future_observability.parquet", "future_observability_summary.parquet")
        result["outputs"] = {
            name: {"rows": pq.ParquetFile(final_dir / name).metadata.num_rows,
                   "sha256": sha256_file(final_dir / name)} for name in files
        }
        (final_dir / "dry_run_audit.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(final_dir, output)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    parser.add_argument("--source-audit", type=Path, default=SOURCE_AUDIT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--partitions", type=int, default=DEFAULT_PARTITIONS)
    parser.add_argument("--max-partition-rows", type=int, default=DEFAULT_MAX_PARTITION_ROWS)
    arguments = parser.parse_args()
    result = prepare(arguments.source, arguments.audit, arguments.source_audit,
                     arguments.output,
                     arguments.partitions, arguments.max_partition_rows)
    print(json.dumps({"output": str(arguments.output),
                      "source_selected_rows": result["source_selected_rows"],
                      "candidate_rows": result["totals"].get("candidate_rows", 0),
                      "model_fitted": False,
                      "outcome_rates_printed": False}, ensure_ascii=False), flush=True)
