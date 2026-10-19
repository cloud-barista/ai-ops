#!/usr/bin/env python3
"""Audit whether pinned AgentPerfBench kernel rows identify GPU choices.

This is a support audit, not a latency model. Matching keys use only published
logical-operation metadata; NCU measurements and launch parameters are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import pandas as pd


REVISION = "722f2b9d8a6acd222811f4e210a96865f774f6ee"
EXPECTED_SHA256 = {
    "README.md": "49ff2f0aad7a8baf39b63458c0a4433a141646d019915c000fc766b992b5cf5e",
    "kernel_profiles/kernels_labeled.parquet": "f9822c8c872bbfc00bc69d335ebef23bb8603f9a9e624267c56905f1e8cf8aa8",
}
LOGICAL_KEY = [
    "source", "model", "kernel_family", "dtype", "op_type", "M", "N", "K",
    "bs", "seq", "n_heads", "head_dim", "kv_heads", "numel",
]
TARGET = "gpu_time_duration_ms"
MEASURED_OR_IMPLEMENTATION_COLUMNS = [
    "kernel_name", "launch_block_size", "launch_grid_size", "dram_bytes_sum",
    "launch_registers_per_thread",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def paired_support(rows: pd.DataFrame, key: list[str]) -> dict:
    # Deduplicate call occurrences before counting independent conditions.
    cells = rows[key + ["gpu"]].drop_duplicates()
    groups = cells.groupby(key, dropna=False).gpu.agg(lambda x: tuple(sorted(x)))
    matched = groups[groups.map(len) >= 2]
    gpu_names = sorted(rows.gpu.unique())
    return {
        "logical_groups": int(len(groups)),
        "gpu_condition_cells": int(len(cells)),
        "groups_with_2plus_gpus": int(len(matched)),
        "groups_with_3plus_gpus": int((matched.map(len) >= 3).sum()),
        "groups_with_4_gpus": int((matched.map(len) >= 4).sum()),
        "gpu_pairs": {
            f"{left}|{right}": int(groups.map(lambda x: left in x and right in x).sum())
            for left, right in itertools.combinations(gpu_names, 2)
        },
        "matched_by_source": {
            str(name): int(count)
            for name, count in matched.reset_index().groupby("source").size().items()
        },
    }


def audit(root: Path) -> dict:
    checksums = {}
    for relative, expected in EXPECTED_SHA256.items():
        source = root / relative
        actual = sha256(source)
        if actual != expected:
            raise ValueError(f"{relative}: SHA256 {actual} != {expected}")
        checksums[relative] = actual
    source = root / "kernel_profiles/kernels_labeled.parquet"
    rows = pd.read_parquet(source)
    required = set(LOGICAL_KEY + ["gpu", "held_out", TARGET] + MEASURED_OR_IMPLEMENTATION_COLUMNS)
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if len(rows) != 148_077:
        raise ValueError(f"Unexpected row count: {len(rows)}")
    if rows[TARGET].isna().any() or (rows[TARGET] <= 0).any():
        raise ValueError("Kernel durations must be positive and non-null")
    is_sweep = rows.source.str.endswith("_sweep")
    if not rows.loc[~is_sweep, "source"].str.endswith("_prefill").all():
        raise ValueError("Unexpected source classification")
    if (rows.groupby("source").held_out.nunique() != 1).any():
        raise ValueError("held_out must be constant within each source")

    real = rows.loc[~is_sweep]
    sweep = rows.loc[is_sweep]
    cells = real.groupby(LOGICAL_KEY + ["gpu"], dropna=False).agg(
        calls=("gpu", "size"),
        kernel_names=("kernel_name", "nunique"),
        distinct_durations=(TARGET, "nunique"),
    )
    matched_gpu_count = real[LOGICAL_KEY + ["gpu"]].drop_duplicates().groupby(
        LOGICAL_KEY, dropna=False
    ).gpu.nunique().rename("gpu_count").reset_index()
    matched_keys = matched_gpu_count.loc[matched_gpu_count.gpu_count >= 2]
    full_mnk = matched_keys[["M", "N", "K"]].notna().all(axis=1)
    has_size = full_mnk | matched_keys.numel.notna()
    source_gpu = real.groupby(["source", "gpu"], dropna=False).size().unstack(fill_value=0)
    held_out_by_source = real.groupby("source").held_out.first()

    return {
        "source": {
            "dataset": "agent-perf-bench/AgentPerfBench",
            "revision": REVISION,
            "file_bytes": source.stat().st_size,
            "sha256": checksums,
        },
        "rows": int(len(rows)),
        "columns": list(rows.columns),
        "gpu_types": sorted(rows.gpu.unique().tolist()),
        "positive_duration_rows": int((rows[TARGET] > 0).sum()),
        "exact_duplicate_rows": int(rows.duplicated().sum()),
        "real_prefill": {
            "rows": int(len(real)),
            "models": int(real.model.nunique()),
            "sources": int(real.source.nunique()),
            "exact_duplicate_rows": int(real.duplicated().sum()),
            "support_logical_key": paired_support(real, LOGICAL_KEY),
            "support_same_kernel_name_sensitivity": paired_support(real, LOGICAL_KEY + ["kernel_name"]),
            "source_gpu_rows": {
                str(src): {str(gpu): int(value) for gpu, value in values.items() if value}
                for src, values in source_gpu.iterrows()
            },
            "original_held_out_sources": sorted(held_out_by_source[held_out_by_source].index.tolist()),
            "matched_groups_with_mnk_or_numel": int(has_size.sum()),
            "matched_groups_missing_mnk_and_numel": int((~has_size).sum()),
            "gpu_condition_cells_with_multiple_calls": int((cells.calls > 1).sum()),
            "gpu_condition_cells_with_multiple_kernel_names": int((cells.kernel_names > 1).sum()),
            "gpu_condition_cells_with_multiple_durations": int((cells.distinct_durations > 1).sum()),
            "model_profile_bs_seq_values": {
                str(src): {
                    "bs": sorted(int(x) for x in part.bs.dropna().unique()),
                    "seq": sorted(int(x) for x in part.seq.dropna().unique()),
                }
                for src, part in real.groupby("source")
            },
        },
        "synthetic_sweeps": {
            "rows": int(len(sweep)),
            "source_names": sorted(sweep.source.unique().tolist()),
            "support_logical_key": paired_support(sweep, LOGICAL_KEY),
        },
        "logical_key": LOGICAL_KEY,
        "excluded_pre_decision_features": MEASURED_OR_IMPLEMENTATION_COLUMNS + ["held_out", TARGET],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root", type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/agentperfbench",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.data_root)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
