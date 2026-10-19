#!/usr/bin/env python3
"""Audit the pinned AgentPerfBench serving sweeps for shape-comparison support.

This checks the two small public summary tables only. It deliberately does not
load kernel profiles and does not train a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


REVISION = "722f2b9d8a6acd222811f4e210a96865f774f6ee"
EXPECTED_SHA256 = {
    "README.md": "49ff2f0aad7a8baf39b63458c0a4433a141646d019915c000fc766b992b5cf5e",
    "trace_replay/summary.parquet": "568efbdae473a830ba792317fb09cfba0ea4f667db67b93d28e72637a5c71d1e",
    "synthetic_distributional/summary.parquet": "db70f78a09cec9dee9159c5011892fb28510e06d2824cc175d26bb47d0e39585",
}
K4 = ["model", "engine", "profile", "concurrency"]
K5 = K4 + ["num_requests"]
POSITIVE_METRICS = [
    "p90_ttft_ms",
    "p90_tpot_ms",
    "p90_e2el_ms",
    "request_throughput",
    "input_token_throughput",
    "output_token_throughput",
]


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def gpu_count(name: str) -> int:
    match = re.search(r"x([248])$", name)
    return int(match.group(1)) if match else 1


def support(rows: pd.DataFrame, keys: list[str]) -> dict:
    counts = rows.groupby(keys, dropna=False)["hardware"].nunique()
    return {
        "rows": int(len(rows)),
        "context_groups": int(len(counts)),
        "groups_with_2plus_shapes": int((counts >= 2).sum()),
        "rows_in_2plus_shape_groups": int(counts[counts >= 2].sum()),
        "groups_with_3plus_shapes": int((counts >= 3).sum()),
        "groups_with_4plus_shapes": int((counts >= 4).sum()),
        "max_shapes": int(counts.max()),
        "shape_count_distribution": {
            str(int(n)): int(count) for n, count in counts.value_counts().sort_index().items()
        },
        "groups_with_2plus_shapes_by_model": {
            str(model): int((part >= 2).sum())
            for model, part in counts.groupby(level=0)
        },
    }


def token_coverage(rows: pd.DataFrame) -> dict:
    grouped = rows.groupby(K5, dropna=False)
    shapes = grouped.hardware.nunique()
    comparable = shapes[shapes >= 2].index
    paired = rows.set_index(K5).loc[comparable].reset_index()
    results = {}
    for metric in ["input_token_throughput", "output_token_throughput"]:
        tokens = paired[metric].to_numpy(dtype=float) * paired.duration_s.to_numpy(dtype=float)
        paired["total_tokens"] = tokens
        ratios = paired.groupby(K5).total_tokens.agg(lambda x: x.max() / x.min())
        results[metric] = {
            "groups": int(len(ratios)),
            "max_min_ratio_median": float(ratios.median()),
            "max_min_ratio_p90": float(ratios.quantile(0.9)),
            "max_min_ratio_max": float(ratios.max()),
            "within_0_001pct": int((ratios <= 1.00001).sum()),
            "within_1pct": int((ratios <= 1.01).sum()),
            "within_5pct": int((ratios <= 1.05).sum()),
            "over_10pct": int((ratios > 1.10).sum()),
        }
    return results


def partial_failure_shape_support(rows: pd.DataFrame) -> dict:
    """Count shape contrasts for partial request completion in published runs."""
    grouped = rows.groupby(K5, dropna=False).agg(
        shapes=("hardware", "nunique"),
        partial_shapes=("partial_failure", "sum"),
    )
    paired = grouped[grouped.shapes >= 2]
    mixed = paired[(paired.partial_shapes > 0) &
                   (paired.partial_shapes < paired.shapes)]
    return {
        "published_rows": int(len(rows)),
        "partial_failure_rows": int(rows.partial_failure.sum()),
        "groups_with_two_or_more_shapes": int(len(paired)),
        "groups_with_both_complete_and_partial_shapes": int(len(mixed)),
        "mixed_groups_by_model": {
            str(model): int(count)
            for model, count in mixed.reset_index().groupby("model").size().items()
        },
    }


def audit(root: Path) -> dict:
    checksums = {}
    for relative_path, expected in EXPECTED_SHA256.items():
        actual = sha256(root / relative_path)
        if actual != expected:
            raise ValueError(f"{relative_path}: SHA256 {actual} != pinned {expected}")
        checksums[relative_path] = actual

    trace = pd.read_parquet(root / "trace_replay/summary.parquet")
    synthetic = pd.read_parquet(root / "synthetic_distributional/summary.parquet")
    required = set(K5 + ["hardware", "run_id", "duration_s", "tensor_parallelism"] + POSITIVE_METRICS)
    for name, rows in [("trace_replay", trace), ("synthetic_distributional", synthetic)]:
        missing = sorted(required - set(rows.columns))
        if missing:
            raise ValueError(f"{name}: missing columns {missing}")
    if trace.isna().any().any():
        raise ValueError("trace_replay contains null values")
    if trace.duplicated(K4 + ["hardware"]).any():
        raise ValueError("trace_replay has duplicate model/engine/profile/concurrency/hardware rows")
    if trace.run_id.duplicated().any():
        raise ValueError("trace_replay has duplicate run_id rows")

    candidate_count = trace.hardware.map(gpu_count)
    successes_float = trace.request_throughput.astype(float) * trace.duration_s.astype(float)
    nearest_successes = np.rint(successes_float.to_numpy(dtype=float)).astype(np.int64)
    max_count_error = float(np.max(np.abs(successes_float.to_numpy(dtype=float) - nearest_successes)))
    if max_count_error > 1e-6:
        raise ValueError("request_throughput * duration_s is not close to integer counts")
    trace = trace.assign(
        inferred_successful_requests=nearest_successes,
        inferred_success_rate=nearest_successes / trace.num_requests.to_numpy(dtype=float),
    )
    trace["partial_failure"] = (
        trace.inferred_successful_requests < trace.num_requests
    )
    positive = (trace[POSITIVE_METRICS].astype(float) > 0).all(axis=1)
    complete = trace.inferred_successful_requests == trace.num_requests
    paired_clean = trace.loc[positive & complete].copy()

    result = {
        "source": {
            "dataset": "agent-perf-bench/AgentPerfBench",
            "revision": REVISION,
            "sha256": checksums,
        },
        "trace_replay": {
            "rows": int(len(trace)),
            "columns": list(trace.columns[:30]),
            "models": sorted(trace.model.unique().tolist()),
            "model_count": int(trace.model.nunique()),
            "hardware": sorted(trace.hardware.unique().tolist()),
            "hardware_count": int(trace.hardware.nunique()),
            "engines": sorted(trace.engine.unique().tolist()),
            "profile_count": int(trace.profile.nunique()),
            "concurrency_values": sorted(int(x) for x in trace.concurrency.unique()),
            "unique_run_ids": int(trace.run_id.nunique()),
            "tp_gpu_count_mismatches": int((trace.tensor_parallelism != candidate_count).sum()),
            "zero_or_negative_metric_rows": int((~positive).sum()),
            "zero_output_token_throughput_rows": int((trace.output_token_throughput <= 0).sum()),
            "inferred_success": {
                "max_integer_residual": max_count_error,
                "rows_with_some_failure": int((~complete).sum()),
                "lowest_retained_fraction": float(trace.inferred_success_rate.min()),
                "rows_below_75pct": int((trace.inferred_success_rate < 0.75).sum()),
                "rows_100pct": int(complete.sum()),
            },
            "support_4key_all": support(trace, K4),
            "support_4key_positive_100pct": support(paired_clean, K4),
            "support_5key_positive_100pct": support(paired_clean, K5),
            "matched_5key_token_total_ratios": token_coverage(paired_clean),
            "partial_failure_shape_support": partial_failure_shape_support(trace),
        },
        "synthetic_distributional": {
            "rows": int(len(synthetic)),
            "models": int(synthetic.model.nunique()),
            "hardware": int(synthetic.hardware.nunique()),
            "profiles": sorted(synthetic.profile.unique().tolist()),
            "concurrency_values": sorted(int(x) for x in synthetic.concurrency.unique()),
            "run_id_overlap_with_trace": int(len(set(synthetic.run_id) & set(trace.run_id))),
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/agentperfbench",
    )
    args = parser.parse_args()
    print(json.dumps(audit(args.data_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
