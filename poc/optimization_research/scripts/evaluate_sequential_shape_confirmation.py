"""Retrospective, within-workload shape confirmation using frozen predictions.

This is a replay of one recorded measurement per shape, not an online trial.
Only a probed candidate's outcome may affect the next probe or acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit_saved_gpu_shape_decisions import (
    CONFIGS, FACTORS, GROUP_KEY, INPUTS, ROOT, sha256_file, validated_gpu_rows,
    verify_pins,
)


POLICIES = ("ascending", "model_first_fallback", "verify_first")


def replay(predicted: np.ndarray, observed: np.ndarray, gpu_seconds: np.ndarray,
           policy: str) -> dict:
    """Probe in a fixed order; continue only after seeing a probed failure."""
    if policy not in POLICIES:
        raise ValueError(f"unknown policy: {policy}")
    predicted = np.asarray(predicted, dtype=bool)
    observed = np.asarray(observed, dtype=bool)
    gpu_seconds = np.asarray(gpu_seconds, dtype=float)
    if predicted.shape != (8,) or observed.shape != (8,) or gpu_seconds.shape != (8,):
        raise ValueError("replay requires one complete eight-shape slate")
    if not np.isfinite(gpu_seconds).all() or (gpu_seconds <= 0).any():
        raise ValueError("GPU measurement costs must be positive and finite")

    first = int(np.flatnonzero(predicted)[0]) if predicted.any() else None
    if policy == "ascending":
        order = list(range(8))
    elif first is None:
        order = [] if policy == "verify_first" else list(range(8))
    elif policy == "verify_first":
        order = [first]
    else:
        order = [first] + [i for i in range(8) if i != first]

    probed = []
    selected = None
    for index in order:
        probed.append(index)
        if bool(observed[index]):
            selected = index
            break
    return {
        "probed_indices": probed,
        "probes": len(probed),
        "gpu_seconds": float(gpu_seconds[probed].sum()),
        "selected_index": selected,
    }


def score_slate(slate: pd.DataFrame, latency_factor: float, rps_factor: float) -> dict:
    slate = slate.sort_values(["replica_count", "cpu_core_limit"]).reset_index(drop=True)
    configs = tuple(map(tuple, slate[["replica_count", "cpu_core_limit"]]
                               .itertuples(index=False, name=None)))
    if configs != CONFIGS or latency_factor <= 0 or rps_factor <= 0:
        raise ValueError("invalid shape grid or target factors")
    target_latency = float(slate.latency_slo_ms.iloc[0] * latency_factor)
    target_rps = float(slate.target_throughput_rps.iloc[0] * rps_factor)
    batch = slate.batch_size.to_numpy(dtype=float)
    observed = ((slate.latency_p95_ms.to_numpy(dtype=float) <= target_latency) &
                (slate.examples_per_second.to_numpy(dtype=float) / batch >= target_rps))
    predicted = ((slate.predicted_latency_p95_ms.to_numpy(dtype=float) <= target_latency) &
                 (slate.predicted_examples_per_second.to_numpy(dtype=float) / batch >= target_rps))
    costs = slate.replica_count.to_numpy(dtype=float) * slate.wall_seconds.to_numpy(dtype=float)
    oracle = int(np.flatnonzero(observed)[0]) if observed.any() else None
    model_choice = int(np.flatnonzero(predicted)[0]) if predicted.any() else None
    row = {column: slate[column].iloc[0] for column in GROUP_KEY}
    row.update({
        "latency_factor": latency_factor,
        "rps_factor": rps_factor,
        "feasible": oracle is not None,
        "oracle_index": oracle,
        "passing_shapes": int(observed.sum()),
        "nonmonotone": bool(np.any(np.diff(observed.astype(int)) < 0)),
        "model_approved": model_choice is not None,
        "model_success": bool(model_choice is not None and observed[model_choice]),
        "model_false_approval": bool(model_choice is not None and not observed[model_choice]),
        "model_index": model_choice,
    })
    for policy in POLICIES:
        result = replay(predicted, observed, costs, policy)
        selected = result["selected_index"]
        row.update({
            f"{policy}_probes": result["probes"],
            f"{policy}_gpu_seconds": result["gpu_seconds"],
            f"{policy}_selected_index": selected,
            f"{policy}_success": selected is not None,
            f"{policy}_exact_min": bool(selected is not None and selected == oracle),
        })
    return row


def summarize(part: pd.DataFrame) -> dict:
    feasible = part[part.feasible]
    summary = {
        "groups": int(len(part)),
        "feasible": int(part.feasible.sum()),
        "nonmonotone": int(part.nonmonotone.sum()),
        "model_approved": int(part.model_approved.sum()),
        "model_success": int(part.model_success.sum()),
        "model_false_approval": int(part.model_false_approval.sum()),
    }
    for policy in POLICIES:
        summary[policy] = {
            "success": int(part[f"{policy}_success"].sum()),
            "exact_min": int(part[f"{policy}_exact_min"].sum()),
            "mean_probes_all": float(part[f"{policy}_probes"].mean()),
            "mean_probes_feasible": float(feasible[f"{policy}_probes"].mean()) if len(feasible) else None,
            "mean_gpu_seconds_all": float(part[f"{policy}_gpu_seconds"].mean()),
            "mean_gpu_seconds_feasible": float(feasible[f"{policy}_gpu_seconds"].mean()) if len(feasible) else None,
        }
    return summary


def run(output: Path) -> dict:
    pins = verify_pins()
    snapshot = pd.read_parquet(INPUTS["snapshot"][0])
    predictions = pd.read_parquet(INPUTS["predictions"][0])
    manifest = pd.read_csv(INPUTS["manifest"][0])
    joined = validated_gpu_rows(snapshot, predictions, manifest)
    wall = snapshot[["host", "execution_id", "wall_seconds"]]
    if wall.duplicated(["host", "execution_id"]).any():
        raise ValueError("duplicate physical measurement key")
    joined = joined.merge(wall, on=["host", "execution_id"], how="left", validate="one_to_one")
    if joined.wall_seconds.isna().any():
        raise ValueError("missing benchmark wall time")

    records = []
    for _, slate in joined.groupby(GROUP_KEY, sort=True):
        for latency_factor in FACTORS:
            for rps_factor in FACTORS:
                records.append(score_slate(slate, latency_factor, rps_factor))
    rows = pd.DataFrame(records)
    groups_per_campaign = joined.groupby("campaign").workload_group.nunique().to_dict()
    if len(rows) != 9 * sum(groups_per_campaign.values()):
        raise AssertionError("expected exactly nine target queries per complete slate")
    original = rows[rows.latency_factor.eq(1.0) & rows.rps_factor.eq(1.0)]
    # Cross-check this new replay against the existing, independently stored audit.
    expected_original = {
        "khu-hf-local-exhaustive": (90, 55, 54, 54, 0),
        "khu-hf-local-novel-matrix": (10, 9, 9, 9, 0),
        "khu-hf-local-space-filling": (33, 26, 25, 25, 0),
    }
    for campaign, expected in expected_original.items():
        part = original[original.campaign.eq(campaign)]
        actual = (len(part), int(part.feasible.sum()), int(part.model_approved.sum()),
                  int(part.model_success.sum()), int(part.model_false_approval.sum()))
        if actual != expected:
            raise AssertionError(f"saved decision audit mismatch for {campaign}: {actual}")

    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / "replay_rows.csv"
    rows.to_csv(rows_path, index=False)
    aggregates = []
    for (campaign, latency_factor, rps_factor), part in rows.groupby(
            ["campaign", "latency_factor", "rps_factor"], sort=True):
        aggregates.append({"campaign": campaign, "latency_factor": latency_factor,
                           "rps_factor": rps_factor, **summarize(part)})
    report = {
        "scope": "exposed retrospective replay of one physical measurement per shape; not a new model, independent validation, or deployment trial",
        "input_sha256": pins,
        "script_sha256": sha256_file(Path(__file__)),
        "rows_sha256": sha256_file(rows_path),
        "groups_per_campaign": {str(k): int(v) for k, v in groups_per_campaign.items()},
        "resource_order": "fewest same-host GPU replicas, then fewest CPU affinity cores",
        "policies": {
            "ascending": "probe shapes in resource order until first observed pass",
            "model_first_fallback": "probe smallest predicted pass first; after observed failure probe remaining shapes in resource order",
            "verify_first": "probe only smallest predicted pass and accept if that measurement passes; otherwise abstain",
        },
        "cost_proxy": "sum of recorded per-candidate wall_seconds * replica_count; not VM cost or user SLO",
        "scenario_caveat": "nine SLO factor pairs reuse the same eight measurements per workload",
        "measurement_caveat": "one recorded outcome per shape; observed pass at a probe is true by construction in this replay, not a future reliability guarantee",
        "aggregates": aggregates,
    }
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/sequential_shape_confirmation_v1")
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps([row for row in result["aggregates"]
                      if row["latency_factor"] == 1.0 and row["rps_factor"] in (1.0, 1.25)],
                     ensure_ascii=False, indent=2))
