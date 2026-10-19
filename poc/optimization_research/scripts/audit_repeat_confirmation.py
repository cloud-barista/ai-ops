"""Time-ordered replay of repeated benchmark confirmation before a next run.

The near-boundary target is synthetic and fixed from the first 20 rounds of
each condition. This is a retrospective reliability audit, not deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/audit_v1/measurements.parquet"
SNAPSHOT_SHA256 = "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906"
VERSIONS = ("khu-hf-direct-collector/v1", "khu-hf-direct-collector/v2")
EXPECTED_ROUNDS = {VERSIONS[0]: 63, VERSIONS[1]: 47}
EXPECTED_CELLS = 16
CALIBRATION_ROUNDS = 20
STREAKS = (1, 2, 3, 5)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_version(rows: pd.DataFrame, *, expected_cells: int,
                    calibration_rounds: int) -> pd.DataFrame:
    """Validate complete, sequential rounds and add chronological round index."""
    columns = ["run_id", "condition_group", "model_id", "collector_version",
               "started_at", "finished_at", "latency_p95_ms", "examples_per_second",
               "batch_size", "latency_slo_ms", "target_throughput_rps"]
    missing = set(columns) - set(rows.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if rows.empty or rows.collector_version.nunique() != 1:
        raise ValueError("one nonempty collector version required")
    if rows[columns].isna().any().any() or rows.duplicated(["run_id", "condition_group"]).any():
        raise ValueError("null or duplicate run/condition")
    result = rows.copy()
    result["start"] = pd.to_datetime(result.started_at, utc=True)
    result["finish"] = pd.to_datetime(result.finished_at, utc=True)
    if (result.finish < result.start).any():
        raise ValueError("measurement finishes before it starts")
    values = result[["latency_p95_ms", "examples_per_second", "batch_size",
                     "latency_slo_ms", "target_throughput_rps"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("invalid performance measurement or target")
    rounds = result.groupby("run_id").agg(start=("start", "min"), finish=("finish", "max"),
                                            cells=("condition_group", "nunique"))
    rounds = rounds.sort_values("start")
    if len(rounds) <= calibration_rounds or not rounds.cells.eq(expected_cells).all():
        raise ValueError("incomplete run or insufficient post-calibration rounds")
    if (rounds.start.iloc[1:].to_numpy() <= rounds.finish.iloc[:-1].to_numpy()).any():
        raise ValueError("rounds overlap or have ambiguous chronology")
    if result.condition_group.nunique() != expected_cells:
        raise ValueError("condition set differs across rounds")
    if not result.groupby("condition_group").size().eq(len(rounds)).all():
        raise ValueError("a condition is missing from a run")
    if result.groupby("condition_group")[["model_id", "batch_size", "latency_slo_ms",
                                          "target_throughput_rps"]].nunique().gt(1).any().any():
        raise ValueError("condition identity or original target changed")
    result["round_index"] = result.run_id.map(
        pd.Series(np.arange(len(rounds), dtype=int), index=rounds.index))
    return result.sort_values(["condition_group", "round_index"]).reset_index(drop=True)


def replay_version(rows: pd.DataFrame, *, expected_cells: int = EXPECTED_CELLS,
                   calibration_rounds: int = CALIBRATION_ROUNDS) -> pd.DataFrame:
    prepared = prepare_version(rows, expected_cells=expected_cells,
                               calibration_rounds=calibration_rounds)
    prepared["rps"] = prepared.examples_per_second / prepared.batch_size
    prefix = prepared[prepared.round_index < calibration_rounds]
    target = prefix.groupby("condition_group").agg(
        near_latency_ms=("latency_p95_ms", lambda s: float(np.quantile(s, 0.9))),
        near_rps=("rps", lambda s: float(np.quantile(s, 0.1))),
    )
    prepared = prepared.join(target, on="condition_group", validate="many_to_one")
    output = []
    for scenario, latency_target, rps_target in (
        ("original_generated", prepared.latency_slo_ms, prepared.target_throughput_rps),
        ("prefix_q90_latency_q10_rps", prepared.near_latency_ms, prepared.near_rps),
    ):
        frame = prepared.copy()
        frame["scenario"] = scenario
        frame["target_latency_ms"] = latency_target
        frame["target_rps"] = rps_target
        frame["pass"] = ((frame.latency_p95_ms <= latency_target) &
                         (frame.rps >= rps_target)).astype(int)
        for count in STREAKS:
            frame[f"accept_last_{count}"] = frame.groupby("condition_group")["pass"].transform(
                lambda s: s.shift(1).rolling(count, min_periods=count).sum().eq(count))
        output.append(frame[frame.round_index >= calibration_rounds].copy())
    return pd.concat(output, ignore_index=True)


def summarize(frame: pd.DataFrame) -> dict:
    summary = {"decisions": int(len(frame)), "cells": int(frame.condition_group.nunique()),
               "rounds": int(frame.round_index.nunique()),
               "next_pass": int(frame["pass"].sum()),
               "next_fail": int((1 - frame["pass"]).sum())}
    for count in STREAKS:
        accepted = frame[frame[f"accept_last_{count}"]]
        successes = int(accepted["pass"].sum())
        failures = int(len(accepted) - successes)
        summary[f"last_{count}"] = {
            "accepted": int(len(accepted)),
            "next_success": successes,
            "next_failure": failures,
            "accept_rate": float(len(accepted) / len(frame)),
            "failure_rate_given_accept": float(failures / len(accepted)) if len(accepted) else None,
        }
    return summary


def run(output: Path) -> dict:
    source_hash = sha256(SNAPSHOT)
    if source_hash != SNAPSHOT_SHA256:
        raise ValueError(f"measurement snapshot SHA-256 mismatch: {source_hash}")
    snapshot = pd.read_parquet(SNAPSHOT)
    live = snapshot[(snapshot.campaign == "khu-hf-live") & snapshot.device.eq("cuda") &
                    snapshot.eligible.eq(True) & snapshot.success.eq(True)]
    if set(live.collector_version) != set(VERSIONS):
        raise ValueError("unexpected live GPU collector versions")
    parts = []
    for version in VERSIONS:
        source = live[live.collector_version.eq(version)]
        prepared = prepare_version(source, expected_cells=EXPECTED_CELLS,
                                   calibration_rounds=CALIBRATION_ROUNDS)
        if prepared.round_index.nunique() != EXPECTED_ROUNDS[version]:
            raise ValueError(f"unexpected run count for {version}")
        parts.append(replay_version(source))
    rows = pd.concat(parts, ignore_index=True)
    records = []
    for (version, scenario), part in rows.groupby(["collector_version", "scenario"], sort=True):
        records.append({"collector_version": version, "scenario": scenario, **summarize(part)})
    output.mkdir(parents=True, exist_ok=True)
    row_path = output / "next_run_decisions.csv"
    columns = ["collector_version", "run_id", "condition_group", "model_id", "round_index",
               "scenario", "target_latency_ms", "target_rps", "pass"] + [
        f"accept_last_{count}" for count in STREAKS]
    rows[columns].to_csv(row_path, index=False)
    report = {
        "scope": "retrospective next-benchmark-run confirmation; no user SLO, VM action, or independent validation",
        "snapshot_sha256": source_hash,
        "script_sha256": sha256(Path(__file__)),
        "decision_rows_sha256": sha256(row_path),
        "time_order": "run minimum started_at; every previous run finishes before next run begins",
        "calibration": "first 20 complete rounds per collector version; per-condition latency q90 and throughput q10 fixed thereafter",
        "decision": "at round t, accept iff all previous k outcomes passed; score only current round t",
        "streaks": list(STREAKS),
        "aggregates": records,
    }
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/repeat_confirmation_v1")
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result["aggregates"], ensure_ascii=False, indent=2))
