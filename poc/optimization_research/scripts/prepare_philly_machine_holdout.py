"""Prepare a score-free Philly test on machines absent from the original 5% sample.

This script uses the existing frozen five-minute window and candidate definitions.
It writes only decision-time features, future-observability bits, and aggregate
gate counts. Row-level outcomes and model probabilities are never persisted.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import prepare_philly_gpu_risk_candidates as builder
from fetch_philly_gpu_sample import EXPECTED_FILES, EXPECTED_SHA256, EXPECTED_SIZE


ROOT = Path(__file__).resolve().parents[1]
OLD_SOURCE_AUDIT = ROOT / "data/external/msr_philly_2017/gpu_util_audit.json"
OLD_DRY_RUN = ROOT / "artifacts/philly_gpu_risk_candidates_v1/dry_run_audit.json"
OUTPUT = ROOT / "artifacts/philly_machine_holdout_candidates_v1"
FROZEN_PROTOCOL = ROOT / "reports/philly_machine_holdout_protocol_frozen.md"
MODEL_SPEC = ROOT / "artifacts/philly_machine_holdout_model_v1/model_spec.json"
EXPECTED_OLD_DRY_RUN_SHA256 = "bccc573ea0f4f8fca9357d72bdc56f60788c85c31c2065d75f639322412bbf0f"
EXPECTED_OLD_SCORE_SHA256 = "cb3080d46c39c748a894065ab59c93df167fcb182c2bddb75e2205a816ddc4d2"
HOLDOUT_LOW = 5
HOLDOUT_HIGH = 15
HOLDOUT_RULE = "5 <= int.from_bytes(SHA256(machineId UTF-8), 'big') % 100 < 15"
PARTITIONS = 128
MAX_PARTITION_ROWS = 500_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def machine_residue(machine_id: str) -> int:
    return int.from_bytes(hashlib.sha256(machine_id.encode("utf-8")).digest(), "big") % 100


def holdout_selected(machine_id: str) -> bool:
    return HOLDOUT_LOW <= machine_residue(machine_id) < HOLDOUT_HIGH


def require_pre_score_freeze(protocol_path: Path, expected_protocol_sha256: str,
                             model_spec_path: Path, expected_model_spec_sha256: str
                             ) -> dict:
    """Enforce the model/code/protocol lock before opening new-machine data."""
    if (not re.fullmatch(r"[0-9a-f]{64}", expected_protocol_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_model_spec_sha256)):
        raise ValueError("Expected protocol and model SHA256 values are required")
    if sha256_file(protocol_path) != expected_protocol_sha256:
        raise ValueError("Holdout FROZEN protocol SHA256 changed")
    if sha256_file(model_spec_path) != expected_model_spec_sha256:
        raise ValueError("Holdout original-only model spec SHA256 changed")
    protocol = protocol_path.read_text(encoding="utf-8")
    if (not re.search(r"^\*\*상태:\s*FROZEN\b", protocol, flags=re.MULTILINE)
            or expected_model_spec_sha256 not in protocol
            or "**PENDING" in protocol):
        raise ValueError("Holdout protocol has not frozen the original-only model")
    code_paths = (
        ROOT / "scripts/fetch_philly_gpu_holdout.py",
        ROOT / "scripts/normalize_philly_gpu_sample.py",
        ROOT / "scripts/prepare_philly_machine_holdout.py",
        ROOT / "scripts/evaluate_philly_machine_holdout.py",
        ROOT / "scripts/prepare_philly_gpu_risk_candidates.py",
    )
    if any(sha256_file(path) not in protocol for path in code_paths):
        raise ValueError("Holdout protocol does not pin current ingestion/evaluation code")
    spec = json.loads(model_spec_path.read_text(encoding="utf-8"))
    if (spec.get("kind") != "philly_original_machines_only_frozen_holdout_model_spec"
            or spec.get("original_score_sha256") != EXPECTED_OLD_SCORE_SHA256
            or spec.get("original_dry_run_sha256") != EXPECTED_OLD_DRY_RUN_SHA256
            or spec.get("code_sha256", {}).get("holdout_scorer")
            != sha256_file(ROOT / "scripts/evaluate_philly_machine_holdout.py")):
        raise ValueError("Original-only model spec differs from fixed old experiment/code")
    if (EXPECTED_OLD_SCORE_SHA256 not in protocol
            or EXPECTED_OLD_DRY_RUN_SHA256 not in protocol):
        raise ValueError("Holdout protocol does not pin the original experiment")
    return {"frozen_protocol_sha256": expected_protocol_sha256,
            "original_model_spec_sha256": expected_model_spec_sha256}


def verify_collector_pre_fetch_freeze(raw_audit: dict,
                                      *, protocol_path: Path = FROZEN_PROTOCOL,
                                      model_spec_path: Path = MODEL_SPEC) -> None:
    """Bind the collected sample to the model and protocol frozen before HTTP access."""
    expected = {
        "frozen_holdout_protocol_sha256": sha256_file(protocol_path),
        "original_only_model_spec_sha256": sha256_file(model_spec_path),
        "holdout_collector_script_sha256": sha256_file(
            ROOT / "scripts/fetch_philly_gpu_holdout.py"),
    }
    if raw_audit.get("pre_fetch_freeze") != expected:
        raise ValueError("Holdout collection predates or differs from the current pre-fetch freeze")


def verify_holdout_lineage(source: Path, normalization_audit: Path,
                           source_audit: Path, old_source_audit: Path,
                           old_dry_run: Path) -> tuple[dict, dict, dict]:
    """Verify archive, derived rows and strict machine disjointness before reading utilization."""
    if sha256_file(old_dry_run) != EXPECTED_OLD_DRY_RUN_SHA256:
        raise ValueError("Original Philly dry-run audit changed")
    old_dry = json.loads(old_dry_run.read_text(encoding="utf-8"))
    old_raw = json.loads(old_source_audit.read_text(encoding="utf-8"))
    raw = json.loads(source_audit.read_text(encoding="utf-8"))
    verify_collector_pre_fetch_freeze(raw)
    normalized = json.loads(normalization_audit.read_text(encoding="utf-8"))
    for audit in (old_raw, raw):
        if audit.get("complete") is not True or audit.get("integrity_pass") is not True:
            raise ValueError("Philly source integrity has not passed")
        if (audit.get("compressed_sha256") != EXPECTED_SHA256
                or audit.get("compressed_bytes_read") != EXPECTED_SIZE
                or audit.get("http_content_length") != EXPECTED_SIZE):
            raise ValueError("Philly archive identity changed")
        members = {row.get("name") for row in audit.get("tar_members", []) if row.get("is_file")}
        if members != EXPECTED_FILES:
            raise ValueError("Philly archive inventory changed")
    if (sha256_file(old_source_audit) != old_dry.get("source_audit_sha256")
            or old_raw.get("sample_sha256") != old_dry.get("source_sample_sha256")):
        raise ValueError("Original Philly source audit differs from frozen dry-run")
    old_gpu, gpu = old_raw["gpu_csv"], raw["gpu_csv"]
    if (raw.get("sample_rule") != HOLDOUT_RULE
            or raw.get("collector_script_sha256")
            != sha256_file(ROOT / "scripts/fetch_philly_gpu_holdout.py")
            or raw.get("source_parser_script_sha256")
            != sha256_file(ROOT / "scripts/fetch_philly_gpu_sample.py")):
        raise ValueError("Holdout selection or collector implementation changed")
    for key in ("rows", "machine_count", "min_time_utc_s", "max_time_utc_s",
                "non_minute_timestamp_rows", "gpu_field_count_histogram"):
        if gpu.get(key) != old_gpu.get(key):
            raise ValueError(f"Full archive GPU audit changed: {key}")
    if (gpu["min_time_utc_s"] != old_dry["source_raw_utc_extrema_only_split"]["raw_min_utc_s"]
            or gpu["max_time_utc_s"] != old_dry["source_raw_utc_extrema_only_split"]["raw_max_utc_s"]):
        raise ValueError("Original UTC split source extrema changed")
    selected = gpu.get("selected_machine_ids", [])
    if (not isinstance(selected, list) or not selected
            or len(selected) != len(set(selected))
            or any(not holdout_selected(str(machine)) for machine in selected)):
        raise ValueError("New machine IDs do not match disjoint [5,15) selection")
    old_selected = set(old_gpu.get("selected_machine_ids", []))
    if old_selected & set(selected):
        raise ValueError("New machine sample overlaps original sample")
    if any(machine_residue(str(machine)) >= HOLDOUT_LOW for machine in old_selected):
        raise ValueError("Original machine audit is not the fixed <5 sample")
    raw_sample = Path(raw.get("sample_parquet", ""))
    if (raw_sample != source_audit.parent / raw_sample.name
            or not raw_sample.is_file()
            or sha256_file(raw_sample) != raw.get("sample_sha256")):
        raise ValueError("Holdout raw sample differs from its complete source audit")
    if (normalized.get("integrity_pass") is not True
            or normalized.get("source_parquet") != str(raw_sample)
            or normalized.get("source_audit") != str(source_audit)
            or normalized.get("source_sha256") != raw["sample_sha256"]
            or normalized.get("source_archive_sha256") != EXPECTED_SHA256
            or normalized.get("output_parquet") != str(source)
            or normalized.get("output_sha256") != sha256_file(source)
            or normalized.get("rows") != gpu.get("selected_rows")
            or normalized.get("source_row_index_sequence_sha256")
            != normalized.get("derived_row_index_sequence_sha256")):
        raise ValueError("Normalized holdout lineage differs from the verified source")
    expected_normalizer = ROOT / "scripts/normalize_philly_gpu_sample.py"
    if normalized.get("normalization_script_sha256") != sha256_file(expected_normalizer):
        raise ValueError("Holdout normalization implementation changed")
    machines = normalized.get("machines", {})
    if not isinstance(machines, dict) or set(machines) != set(selected):
        raise ValueError("Normalized machine inventory differs from selected source IDs")
    if (sum(int(item["rows"]) for item in machines.values()) != normalized["rows"]
            or sum(int(item["invalid_rows"]) for item in machines.values())
            != normalized["invalid_rows"]):
        raise ValueError("Normalized per-machine row counts differ")
    parquet = pq.ParquetFile(source)
    if (parquet.metadata.num_rows != normalized["rows"]
            or not set(builder.INPUT_COLUMNS).issubset(parquet.schema_arrow.names)):
        raise ValueError("Holdout normalized Parquet row count/schema differs")
    recount = recount_machine_quality(parquet)
    for machine, item in machines.items():
        if recount.get(machine) != {
                "rows": int(item["rows"]),
                "invalid_rows": int(item["invalid_rows"]),
                "first_source_row_index": int(item["first_source_row_index"]),
                "first_observed_gpu_slots": int(item["first_observed_gpu_slots"])}:
            raise ValueError("Holdout per-machine width counts differ from normalized rows")
    return raw, normalized, old_dry


def recount_machine_quality(parquet: pq.ParquetFile) -> dict[str, dict]:
    """Recount the fields that decide machine exclusion from actual normalized rows."""
    counts: dict[str, dict] = {}
    columns = ["machine_id", "source_row_index", "row_usable",
               "observed_gpu_slots", "expected_gpu_slots", "first_observed_gpu_slots"]
    for batch in parquet.iter_batches(batch_size=builder.READ_BATCH_ROWS, columns=columns):
        frame = batch.to_pandas()
        if frame[columns].isna().any().any():
            raise ValueError("Holdout normalized quality fields have null values")
        for machine, part in frame.groupby("machine_id", sort=False):
            expected = part.expected_gpu_slots.to_numpy(dtype=int)
            first = part.first_observed_gpu_slots.to_numpy(dtype=int)
            if not (expected == first).all() or len(set(first)) != 1:
                raise ValueError("Holdout expected GPU width changed within a machine")
            indices = part.source_row_index.to_numpy(dtype=int)
            index = int(indices.argmin())
            first_source = int(indices[index])
            first_observed = int(part.observed_gpu_slots.iloc[index])
            entry = counts.setdefault(str(machine), {
                "rows": 0, "invalid_rows": 0,
                "first_source_row_index": first_source,
                "first_observed_gpu_slots": first_observed,
            })
            if first_source < entry["first_source_row_index"]:
                entry["first_source_row_index"] = first_source
                entry["first_observed_gpu_slots"] = first_observed
            entry["rows"] += len(part)
            entry["invalid_rows"] += int((~part.row_usable.to_numpy(dtype=bool)).sum())
            if entry["first_observed_gpu_slots"] != int(first[0]):
                raise ValueError("Holdout first-row width differs from normalized fixed width")
    return counts


def test_only_bounds(old_dry: dict) -> dict:
    """Keep exact original test dates while constructing no train/validation candidates."""
    original = old_dry["source_raw_utc_extrema_only_split"]
    bounds = copy.deepcopy(original)
    start = int(original["spans"]["test"]["start_utc_s"])
    start_day = int(original["spans"]["test"]["start_utc_day"])
    for split in ("train", "validation"):
        bounds["spans"][split] = {
            "start_utc_s": start, "end_utc_s": start,
            "start_utc_day": start_day, "end_utc_day_exclusive": start_day,
            "days": 0,
        }
    bounds["duration_gate_pass"] = False
    bounds["test_duration_gate_pass"] = int(original["spans"]["test"]["days"]) >= 3
    return bounds


def partition_holdout(source: Path, directory: Path,
                      eligible_machines: set[str],
                      test_start_utc_s: int, test_end_utc_s: int,
                      partitions: int = PARTITIONS,
                      max_rows: int = MAX_PARTITION_ROWS
                      ) -> tuple[dict[int, tuple[Path, int]], dict]:
    """Bound memory and preserve the existing builder's bucket/slot iteration order."""
    if not 1 <= partitions <= 512 or max_rows < 1:
        raise ValueError("Invalid holdout partition limits")
    parquet = pq.ParquetFile(source)
    schema = pa.schema([parquet.schema_arrow.field(name) for name in builder.INPUT_COLUMNS])
    writers: dict[int, pq.ParquetWriter] = {}
    paths: dict[int, Path] = {}
    counts: Counter[int] = Counter()
    cache: dict[str, int] = {}
    excluded_rows = 0
    all_test_active: set[str] = set()
    retained_test_active: set[str] = set()
    try:
        for batch in parquet.iter_batches(batch_size=builder.READ_BATCH_ROWS,
                                          columns=list(builder.INPUT_COLUMNS)):
            frame = batch.to_pandas()
            for machine in frame.machine_id.unique():
                mid = str(machine)
                if not holdout_selected(mid):
                    raise ValueError("Normalized holdout contains an unselected machine")
                cache.setdefault(mid, builder.bucket_for_machine(mid, partitions))
            active = frame.loc[frame.time_utc_s.ge(test_start_utc_s)
                               & frame.time_utc_s.lt(test_end_utc_s), "machine_id"]
            all_test_active.update(active.astype(str).unique())
            retained_test_active.update(mid for mid in active.astype(str).unique()
                                        if mid in eligible_machines)
            keep = frame.machine_id.isin(eligible_machines)
            excluded_rows += int((~keep).sum())
            frame = frame.loc[keep]
            if frame.empty:
                continue
            buckets = frame.machine_id.map(cache).to_numpy(int)
            for index in sorted(set(buckets)):
                selection = frame.loc[buckets == index, list(builder.INPUT_COLUMNS)]
                counts[index] += len(selection)
                if counts[index] > max_rows:
                    raise MemoryError("Holdout bucket exceeds fixed row limit")
                if index not in writers:
                    paths[index] = directory / f"bucket_{index:03d}.parquet"
                    writers[index] = pq.ParquetWriter(paths[index], schema, compression="zstd")
                builder._write_frame(writers[index], selection, schema)
            builder.require_free_space(directory)
    finally:
        for writer in writers.values():
            writer.close()
    if sum(counts.values()) + excluded_rows != parquet.metadata.num_rows:
        raise AssertionError("Holdout partitioning changed source row count")
    return ({index: (paths[index], counts[index]) for index in sorted(paths)},
            {"excluded_rows": excluded_rows,
             "all_test_active_machines": len(all_test_active),
             "retained_test_active_machines": len(retained_test_active),
             "excluded_test_active_machines": len(all_test_active - retained_test_active)})


def test_gate_rows(gates: builder.GateAccumulator) -> list[dict]:
    """Return all four test strata, including absent strata with zero counts."""
    rows = {(row["cohort"], row["current_state"]): row
            for row in gates.result() if row["split"] == "test"}
    result = []
    for cohort in builder.COHORTS:
        for state in builder.STATES:
            row = rows.get((cohort, state), {
                "split": "test", "cohort": cohort, "current_state": state,
                "candidates": 0, "future_complete": 0,
                "future_observation_fraction": None,
                "positive_gate_count": 0, "negative_gate_count": 0,
                "eligible_machine_count": 0, "positive_machine_count": 0,
                "negative_machine_count": 0, "positive_utc_day_count": 0,
            })
            candidate, observed = int(row["candidates"]), int(row["future_complete"])
            positive, negative = int(row["positive_gate_count"]), int(row["negative_gate_count"])
            if not (0 <= observed <= candidate and positive + negative == observed):
                raise ValueError("Holdout future gate counts are inconsistent")
            flags = {
                "future_observation_at_least_half": candidate > 0 and observed / candidate >= 0.50,
                "eligible_at_least_2000": observed >= 2000,
                "positive_at_least_100": positive >= 100,
                "negative_at_least_100": negative >= 100,
                "eligible_machines_at_least_10": int(row["eligible_machine_count"]) >= 10,
                "positive_machines_at_least_5": int(row["positive_machine_count"]) >= 5,
                "negative_machines_at_least_5": int(row["negative_machine_count"]) >= 5,
                "test_positives_on_five_days": int(row["positive_utc_day_count"]) >= 5,
            }
            result.append({**row, "gate_checks": flags, "gate_pass": all(flags.values())})
    return result


class HoldoutGateAccumulator(builder.GateAccumulator):
    """Keep aggregate class support for both fixed test halves before scoring."""

    def __init__(self, test_span: dict):
        super().__init__()
        self.start_day = int(test_span["start_utc_day"])
        self.end_day = int(test_span["end_utc_day_exclusive"])
        self.mid_day = self.start_day + (self.end_day - self.start_day) // 2
        self.halves = {1: Counter(), 2: Counter()}

    def add(self, frame: pd.DataFrame, future_complete, positive_if_observed) -> None:
        super().add(frame, future_complete, positive_if_observed)
        if frame.empty:
            return
        primary = (frame.split.eq("test").to_numpy()
                   & frame.cohort.eq("complete").to_numpy()
                   & frame.current_state.eq("below_80").to_numpy()
                   & future_complete)
        days = frame.utc_day.to_numpy(dtype=int)
        for half, lo, hi in ((1, self.start_day, self.mid_day),
                             (2, self.mid_day, self.end_day)):
            select = primary & (days >= lo) & (days < hi)
            positive = int((select & positive_if_observed).sum())
            negative = int((select & ~positive_if_observed).sum())
            self.halves[half]["positive"] += positive
            self.halves[half]["negative"] += negative

    def half_gate(self) -> list[dict]:
        result = []
        for half, lo, hi in ((1, self.start_day, self.mid_day),
                             (2, self.mid_day, self.end_day)):
            positive = int(self.halves[half]["positive"])
            negative = int(self.halves[half]["negative"])
            result.append({"half": half, "start_utc_day": lo,
                           "end_utc_day_exclusive": hi,
                           "positives": positive, "negatives": negative,
                           "eligible": positive + negative,
                           "passes_both_classes": positive >= 1 and negative >= 1})
        return result


def prepare(source: Path, normalization_audit: Path, source_audit: Path,
            *, old_source_audit: Path = OLD_SOURCE_AUDIT,
            old_dry_run: Path = OLD_DRY_RUN, output: Path = OUTPUT,
            frozen_protocol: Path = FROZEN_PROTOCOL,
            expected_protocol_sha256: str,
            model_spec: Path = MODEL_SPEC,
            expected_model_spec_sha256: str,
            partitions: int = PARTITIONS, max_partition_rows: int = MAX_PARTITION_ROWS) -> dict:
    freeze = require_pre_score_freeze(
        frozen_protocol, expected_protocol_sha256,
        model_spec, expected_model_spec_sha256)
    raw, normalized, old_dry = verify_holdout_lineage(
        source, normalization_audit, source_audit, old_source_audit, old_dry_run)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite holdout candidates: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    builder.require_free_space(output.parent)
    bounds = test_only_bounds(old_dry)
    full_bounds = old_dry["source_raw_utc_extrema_only_split"]
    eligible_ids = set()
    width = []
    for machine, item in sorted(normalized["machines"].items()):
        rows, bad = int(item["rows"]), int(item["invalid_rows"])
        slots = int(item["first_observed_gpu_slots"])
        if rows < 1 or bad < 0 or bad > rows:
            raise ValueError("Invalid holdout per-machine width counts")
        reason = None
        if slots not in (2, 4, 8):
            reason = "unsupported_first_observed_width"
        elif bad / rows > 0.01:
            reason = "width_mismatch_above_one_percent"
        else:
            eligible_ids.add(machine)
        width.append({"machine_hash": builder.machine_hash(machine),
                      "expected_gpu_slots": slots, "rows": rows,
                      "width_mismatch_rows": bad,
                      "width_mismatch_fraction": bad / rows,
                      "retained": reason is None, "exclusion_reason": reason})
    with tempfile.TemporaryDirectory(prefix=".philly_holdout_prepare_", dir=output.parent) as temp:
        root = Path(temp)
        bucket_dir, final = root / "buckets", root / "prepared"
        bucket_dir.mkdir()
        final.mkdir()
        buckets, partition_audit = partition_holdout(
            source, bucket_dir, eligible_ids,
            int(bounds["spans"]["test"]["start_utc_s"]),
            int(bounds["spans"]["test"]["end_utc_s"]),
            partitions, max_partition_rows)
        excluded_rows = int(partition_audit["excluded_rows"])
        gates = HoldoutGateAccumulator(bounds["spans"]["test"])
        totals: Counter[str] = Counter()
        input_quality: dict[str, Counter[str]] = {
            split: Counter() for split in builder.SPLIT_NAMES}
        with (pq.ParquetWriter(final / "candidates.parquet", builder.CANDIDATE_SCHEMA,
                               compression="zstd") as candidate_writer,
              pq.ParquetWriter(final / "future_observability.parquet",
                               builder.FUTURE_OBSERVABILITY_SCHEMA,
                               compression="zstd") as future_writer,
              pq.ParquetWriter(final / "future_observability_summary.parquet",
                               builder.OBSERVABILITY_SCHEMA,
                               compression="zstd") as observability_writer):
            for bucket, (path, count) in buckets.items():
                frame = pd.read_parquet(path)
                if len(frame) != count or len(frame) > max_partition_rows:
                    raise ValueError(f"Holdout bucket {bucket} row count differs")
                windows, _, summary = builder.minute_to_windows(frame)
                totals.update(summary)
                quality = builder._window_quality(windows)
                bucket_quality = builder.split_input_window_counts(quality, full_bounds)
                for split in builder.SPLIT_NAMES:
                    input_quality[split].update(bucket_quality[split])
                candidates, futures, labels, candidate_summary = builder.construct_candidates(
                    windows, bounds, gates, include_scoring_labels=False)
                if labels is not None:
                    raise AssertionError("Dry-run constructed row-level labels")
                totals.update(candidate_summary)
                for candidate, future in zip(candidates, futures):
                    builder._write_frame(candidate_writer, candidate, builder.CANDIDATE_SCHEMA)
                    builder._write_frame(future_writer, future,
                                         builder.FUTURE_OBSERVABILITY_SCHEMA)
                    builder._write_frame(observability_writer,
                                         builder._observability(candidate, future),
                                         builder.OBSERVABILITY_SCHEMA)
                builder.require_free_space(root)
        if totals["raw_rows"] + excluded_rows != raw["gpu_csv"]["selected_rows"]:
            raise ValueError("Holdout prepared row count changed")
        test_span = bounds["spans"]["test"]
        if test_span != old_dry["source_raw_utc_extrema_only_split"]["spans"]["test"]:
            raise AssertionError("Original test dates changed")
        total_slots = sum(item["expected_gpu_slots"] for item in width if item["retained"])
        by_split = {}
        for split in builder.SPLIT_NAMES:
            observed = int(input_quality[split]["observed_window_slots"])
            valid = int(input_quality[split]["valid_window_slots"])
            days = int(full_bounds["spans"][split]["days"])
            grid = days * 288 * total_slots
            if observed > grid or valid > observed:
                raise ValueError("Holdout five-minute quality grid counts are inconsistent")
            by_split[split] = {
                "observed_window_slots": observed,
                "valid_window_slots": valid,
                "valid_fraction": valid / observed if observed else None,
                "fixed_calendar_grid_window_slots": grid,
                "raw_row_absent_grid_window_slots": grid - observed,
            }
        test_fraction = by_split["test"]["valid_fraction"]
        source_quality = {
            "per_machine": width,
            "excluded_machines": sum(not item["retained"] for item in width),
            "excluded_rows": excluded_rows,
            "retained_machines": len(eligible_ids),
            "retained_rows": int(totals["raw_rows"]),
            "all_test_active_machines": partition_audit["all_test_active_machines"],
            "retained_test_active_machines": partition_audit["retained_test_active_machines"],
            "excluded_test_active_machines": partition_audit["excluded_test_active_machines"],
            "input_quality_by_original_split": by_split,
            "test_input_passes": test_fraction is not None and test_fraction >= 0.50,
            "passes": bool(eligible_ids) and test_fraction is not None and test_fraction >= 0.50,
        }
        gate_rows = test_gate_rows(gates)
        files = ("candidates.parquet", "future_observability.parquet",
                 "future_observability_summary.parquet")
        result = {
            "kind": "holdout_audit_only_no_model_fit_no_row_level_outcome",
            "source_archive_sha256": EXPECTED_SHA256,
            **freeze,
            "source_audit_path": str(source_audit),
            "source_audit_sha256": sha256_file(source_audit),
            "source_sample_sha256": raw["sample_sha256"],
            "normalization_audit_path": str(normalization_audit),
            "normalization_audit_sha256": sha256_file(normalization_audit),
            "normalized_sample_path": str(source),
            "normalized_sample_sha256": normalized["output_sha256"],
            "original_source_audit_sha256": sha256_file(old_source_audit),
            "original_dry_run_sha256": sha256_file(old_dry_run),
            "preparation_script_sha256": sha256_file(Path(__file__)),
            "candidate_builder_sha256": sha256_file(Path(builder.__file__)),
            "machine_selection": {"rule": HOLDOUT_RULE,
                                  "low_inclusive": HOLDOUT_LOW, "high_exclusive": HOLDOUT_HIGH,
                                  "selected_machines": len(normalized["machines"]),
                                  "overlap_with_original": 0},
            "test_span": test_span,
            "test_only_bounds": bounds,
            "source_raw_utc_extrema_only_split": full_bounds,
            "partitioning": {"machine_hash_buckets": partitions,
                             "max_partition_rows": max_partition_rows,
                             "bucket_count_used": len(buckets),
                             "max_bucket_rows_observed": max((n for _, n in buckets.values()), default=0)},
            "totals": dict(sorted(totals.items())),
            "source_quality": source_quality,
            "gate_counts_by_split_cohort_state": gate_rows,
            "primary_half_gate_counts": gates.half_gate(),
            "outputs": {name: {"rows": pq.ParquetFile(final / name).metadata.num_rows,
                               "sha256": sha256_file(final / name)} for name in files},
        }
        (final / "dry_run_audit.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8")
        os.replace(final, output)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--normalization-audit", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--frozen-protocol", type=Path, default=FROZEN_PROTOCOL)
    parser.add_argument("--expected-protocol-sha256", required=True)
    parser.add_argument("--model-spec", type=Path, default=MODEL_SPEC)
    parser.add_argument("--expected-model-spec-sha256", required=True)
    parser.add_argument("--partitions", type=int, default=PARTITIONS)
    parser.add_argument("--max-partition-rows", type=int, default=MAX_PARTITION_ROWS)
    args = parser.parse_args()
    result = prepare(args.source, args.normalization_audit, args.source_audit,
                     output=args.output, frozen_protocol=args.frozen_protocol,
                     expected_protocol_sha256=args.expected_protocol_sha256,
                     model_spec=args.model_spec,
                     expected_model_spec_sha256=args.expected_model_spec_sha256,
                     partitions=args.partitions,
                     max_partition_rows=args.max_partition_rows)
    print(json.dumps({"output": str(args.output),
                      "candidate_rows": result["outputs"]["candidates.parquet"]["rows"],
                      "source_quality_passes": result["source_quality"]["passes"],
                      "gate_passes": {f"{row['cohort']}/{row['current_state']}": row['gate_pass']
                                      for row in result["gate_counts_by_split_cohort_state"]}},
                     sort_keys=True))
