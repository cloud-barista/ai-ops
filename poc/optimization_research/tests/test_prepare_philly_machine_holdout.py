"""Synthetic checks for strict machine disjointness and score-free holdout prep."""

from __future__ import annotations

from pathlib import Path
import json
import sys

import pandas as pd
import pyarrow.parquet as pq
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import prepare_philly_gpu_risk_candidates as builder  # noqa: E402
import prepare_philly_machine_holdout as holdout  # noqa: E402


def machine_in(low: int, high: int) -> str:
    return next(f"synthetic-machine-{i}" for i in range(100_000)
                if low <= holdout.machine_residue(f"synthetic-machine-{i}") < high)


def minute(machine: str, time_s: int, index: int) -> dict:
    return {
        "source_row_index": index,
        "time_utc_s": time_s,
        "machine_id": machine,
        "expected_gpu_slots": 2,
        "observed_gpu_slots": 2,
        "first_observed_gpu_slots": 2,
        "width_matches_first": True,
        "row_usable": True,
        "row_invalid_reason": None,
        "gpu0_util": 10.0, "gpu1_util": 10.0,
        **{f"gpu{i}_util": None for i in range(2, 8)},
    }


def test_partition_keeps_only_quality_eligible_new_machines(tmp_path: Path):
    keep = machine_in(5, 15)
    exclude = next(f"other-{i}" for i in range(100_000)
                   if holdout.holdout_selected(f"other-{i}") and f"other-{i}" != keep)
    start = 100 * 86400
    frame = pd.DataFrame([
        minute(mid, start + k * 60, 10 * k + j)
        for k in range(10) for j, mid in enumerate((keep, exclude), start=1)
    ])
    source = tmp_path / "normalized.parquet"
    frame.to_parquet(source, index=False)
    buckets, audit = holdout.partition_holdout(
        source, tmp_path, {keep}, start, start + 86400, partitions=8,
        max_rows=100)
    assert sum(n for _, n in buckets.values()) == 10
    assert audit == {
        "excluded_rows": 10,
        "all_test_active_machines": 2,
        "retained_test_active_machines": 1,
        "excluded_test_active_machines": 1,
    }
    assert set(pd.concat([pd.read_parquet(path) for path, _ in buckets.values()]).machine_id) == {keep}


def test_partition_rejects_original_training_machine(tmp_path: Path):
    old = machine_in(0, 5)
    source = tmp_path / "normalized.parquet"
    pd.DataFrame([minute(old, 100 * 86400, 1)]).to_parquet(source, index=False)
    try:
        holdout.partition_holdout(source, tmp_path, {old}, 100 * 86400,
                                  101 * 86400)
    except ValueError as exc:
        assert "unselected machine" in str(exc)
    else:
        raise AssertionError("Original sample machine was accepted")


def test_original_test_dates_only_and_no_row_labels():
    original = builder.split_boundaries(95 * 86400 + 9 * 3600,
                                         130 * 86400 + 17 * 3600)
    test_start = original["spans"]["test"]["start_utc_s"]
    bounds = holdout.test_only_bounds({"source_raw_utc_extrema_only_split": original})
    assert bounds["spans"]["test"] == original["spans"]["test"]
    assert bounds["spans"]["train"]["days"] == 0
    assert bounds["spans"]["validation"]["days"] == 0
    machine = machine_in(5, 15)
    rows = [minute(machine, test_start - 1800 + i * 60, i + 1) for i in range(60)]
    windows, _, _ = builder.minute_to_windows(pd.DataFrame(rows))
    candidates, future, labels, _ = builder.construct_candidates(
        windows, bounds, builder.GateAccumulator(), include_scoring_labels=False)
    assert labels is None
    candidate = pd.concat(candidates, ignore_index=True)
    observed = pd.concat(future, ignore_index=True)
    assert set(candidate.split) == {"test"}
    assert candidate.decision_utc_s.min() >= test_start
    assert "y" not in candidate and not any(x.startswith("future_") for x in candidate.columns)
    assert "y" not in observed


def test_end_to_end_dry_run_excludes_bad_width_and_saves_no_outcomes(
        tmp_path: Path, monkeypatch):
    keep = machine_in(5, 15)
    exclude = next(f"bad-width-{i}" for i in range(100_000)
                   if holdout.holdout_selected(f"bad-width-{i}") and f"bad-width-{i}" != keep)
    split = builder.split_boundaries(95 * 86400 + 9 * 3600,
                                     130 * 86400 + 17 * 3600)
    start = split["spans"]["test"]["start_utc_s"]
    rows = [minute(mid, start - 1800 + k * 60, 10 * k + j)
            for k in range(70) for j, mid in enumerate((keep, exclude), start=1)]
    source = tmp_path / "normalized.parquet"
    pd.DataFrame(rows).to_parquet(source, index=False)
    raw = {"gpu_csv": {"selected_rows": len(rows)}, "sample_sha256": "synthetic"}
    normalized = {
        "output_sha256": "synthetic",
        "machines": {
            keep: {"rows": 70, "invalid_rows": 0, "first_observed_gpu_slots": 2},
            exclude: {"rows": 70, "invalid_rows": 1, "first_observed_gpu_slots": 2},
        },
    }
    old_dry = {"source_raw_utc_extrema_only_split": split}
    monkeypatch.setattr(holdout, "require_pre_score_freeze",
                        lambda *args: {"frozen_protocol_sha256": "a" * 64,
                                       "original_model_spec_sha256": "b" * 64})
    monkeypatch.setattr(holdout, "verify_holdout_lineage",
                        lambda *args: (raw, normalized, old_dry))
    audit_files = [tmp_path / name for name in
                   ("normalization_audit.json", "source_audit.json",
                    "old_source_audit.json", "old_dry_run.json")]
    for path in audit_files:
        path.write_text("{}\n")
    out = tmp_path / "prepared"
    result = holdout.prepare(source, audit_files[0], audit_files[1],
                             old_source_audit=audit_files[2],
                             old_dry_run=audit_files[3], output=out,
                             expected_protocol_sha256="a" * 64,
                             expected_model_spec_sha256="b" * 64,
                             partitions=8, max_partition_rows=100)
    quality = result["source_quality"]
    assert quality["retained_machines"] == quality["excluded_machines"] == 1
    assert quality["retained_rows"] == quality["excluded_rows"] == 70
    assert quality["retained_test_active_machines"] == 1
    assert quality["all_test_active_machines"] == 2
    assert result["totals"]["candidate_rows"] > 0
    assert sum(row["positive_gate_count"] for row in
               result["gate_counts_by_split_cohort_state"]) == 0
    assert len(result["primary_half_gate_counts"]) == 2
    assert sum(row["eligible"] for row in result["primary_half_gate_counts"]) == sum(
        row["future_complete"] for row in result["gate_counts_by_split_cohort_state"]
        if row["cohort"] == "complete" and row["current_state"] == "below_80")
    candidates = pd.read_parquet(out / "candidates.parquet")
    futures = pd.read_parquet(out / "future_observability.parquet")
    assert len(candidates) == len(futures) > 0
    assert set(candidates.machine_hash) == {builder.machine_hash(keep)}
    assert set(candidates.split) == {"test"}
    assert "y" not in candidates and "y" not in futures
    assert json.loads((out / "dry_run_audit.json").read_text())["kind"].startswith("holdout_audit_only")


def test_prepare_rejects_missing_freeze_before_source_read(tmp_path: Path):
    source = tmp_path / "unreadable-source.parquet"
    try:
        holdout.prepare(source, tmp_path / "normalization-audit.json",
                        tmp_path / "source-audit.json",
                        frozen_protocol=tmp_path / "missing-frozen.md",
                        model_spec=tmp_path / "missing-model.json",
                        expected_protocol_sha256="a" * 64,
                        expected_model_spec_sha256="b" * 64,
                        output=tmp_path / "should-not-exist")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Holdout source could be opened before freeze")
    assert not source.exists()
    assert not (tmp_path / "should-not-exist").exists()


def test_half_gate_counts_only_observed_primary_rows():
    gate = holdout.HoldoutGateAccumulator({"start_utc_day": 100,
                                           "end_utc_day_exclusive": 115})
    frame = pd.DataFrame({
        "split": ["test"] * 5,
        "cohort": ["complete", "complete", "complete", "partial_past", "complete"],
        "current_state": ["below_80", "below_80", "below_80", "below_80", "at_least_80"],
        "machine_hash": ["m1", "m2", "m3", "m4", "m5"],
        "utc_day": [101, 108, 108, 108, 108],
    })
    import numpy as np
    gate.add(frame, np.array([True, True, False, True, True]),
             np.array([True, False, True, True, True]))
    assert gate.half_gate() == [
        {"half": 1, "start_utc_day": 100, "end_utc_day_exclusive": 107,
         "positives": 1, "negatives": 0, "eligible": 1, "passes_both_classes": False},
        {"half": 2, "start_utc_day": 107, "end_utc_day_exclusive": 115,
         "positives": 0, "negatives": 1, "eligible": 1, "passes_both_classes": False},
    ]


def test_recount_machine_quality_detects_per_machine_invalid_rows(tmp_path: Path):
    first = machine_in(5, 15)
    second = next(f"recount-{i}" for i in range(100_000)
                  if holdout.holdout_selected(f"recount-{i}") and f"recount-{i}" != first)
    rows = [minute(first, 100 * 86400, 1),
            minute(second, 100 * 86400, 2),
            minute(first, 100 * 86400 + 60, 3),
            minute(second, 100 * 86400 + 60, 4)]
    rows[-1]["row_usable"] = False
    rows[-1]["observed_gpu_slots"] = 4
    path = tmp_path / "normalized.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    recount = holdout.recount_machine_quality(pq.ParquetFile(path))
    assert recount[first] == {
        "rows": 2, "invalid_rows": 0,
        "first_source_row_index": 1, "first_observed_gpu_slots": 2}
    assert recount[second] == {
        "rows": 2, "invalid_rows": 1,
        "first_source_row_index": 2, "first_observed_gpu_slots": 2}


def test_collection_audit_must_match_the_pre_fetch_freeze(tmp_path: Path, monkeypatch):
    protocol = tmp_path / "frozen.md"
    model_spec = tmp_path / "model_spec.json"
    protocol.write_text("**상태: FROZEN**\n")
    model_spec.write_text("{}\n")
    expected = {
        "frozen_holdout_protocol_sha256": holdout.sha256_file(protocol),
        "original_only_model_spec_sha256": holdout.sha256_file(model_spec),
        "holdout_collector_script_sha256": holdout.sha256_file(
            holdout.ROOT / "scripts/fetch_philly_gpu_holdout.py"),
    }
    holdout.verify_collector_pre_fetch_freeze(
        {"pre_fetch_freeze": expected}, protocol_path=protocol,
        model_spec_path=model_spec)
    for key in expected:
        changed = dict(expected)
        changed[key] = "0" * 64
        with pytest.raises(ValueError, match="pre-fetch freeze"):
            holdout.verify_collector_pre_fetch_freeze(
                {"pre_fetch_freeze": changed}, protocol_path=protocol,
                model_spec_path=model_spec)
    with pytest.raises(ValueError, match="pre-fetch freeze"):
        holdout.verify_collector_pre_fetch_freeze(
            {}, protocol_path=protocol, model_spec_path=model_spec)

    # The lineage check rejects an unbound collection audit before opening
    # the normalized GPU rows, even if the raw archive audit exists.
    old_dry = tmp_path / "old_dry.json"
    old_dry.write_text("{}\n")
    old_raw = tmp_path / "old_raw.json"
    old_raw.write_text("{}\n")
    raw = tmp_path / "raw_audit.json"
    raw.write_text("{}\n")
    monkeypatch.setattr(holdout, "EXPECTED_OLD_DRY_RUN_SHA256",
                        holdout.sha256_file(old_dry))
    with pytest.raises(ValueError, match="pre-fetch freeze"):
        holdout.verify_holdout_lineage(
            tmp_path / "missing-normalized.parquet",
            tmp_path / "missing-normalized-audit.json", raw, old_raw, old_dry)
