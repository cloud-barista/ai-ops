#!/usr/bin/env python3
"""Score-free support audit on the exposed MaaS bucket-0 development days.

This script cannot open the proposed bucket-1 holdout. It restricts the pinned
bucket-0 compact trace to days 140--144 before constructing any future labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_maas_instance_risk as original


SOURCE = original.SOURCE
AUDIT = original.AUDIT
DAY_S = 86400
SPLITS = {
    "fit": (140 * DAY_S, 143 * DAY_S),
    "calibration": (143 * DAY_S, 144 * DAY_S),
    "validation": (144 * DAY_S, 145 * DAY_S),
}
FUTURE_OFFSETS = original.FUTURE_OFFSETS
PAST_OFFSETS = original.PAST_OFFSETS
CLASS_NAMES = ("observed_high", "fully_observed_normal", "unresolved")


def classify_future(valid: np.ndarray, high: np.ndarray) -> np.ndarray:
    """Known high takes precedence over an unresolved different future slot."""
    if valid.shape != high.shape or valid.ndim != 2 or valid.shape[1] != 6:
        raise ValueError("future status must have six aligned windows")
    known_high = (valid & high).any(axis=1)
    known_normal = valid.all(axis=1) & ~known_high
    result = np.full(len(valid), 2, dtype=np.int8)
    result[known_normal] = 1
    result[known_high] = 0
    return result


def audit() -> dict:
    source = original.verify_source(SOURCE, AUDIT)
    raw = original.load_compact(SOURCE)
    # The source file includes exposed bucket-0 test rows. Drop them before
    # building any exact lookup or classification for this new target.
    raw = raw.loc[raw.time_s < 145 * DAY_S].copy()
    raw = raw.loc[raw.time_s.notna()].sort_values(
        ["instance_hash", "time_s"], kind="mergesort").reset_index(drop=True)
    if raw.empty:
        raise ValueError("empty bucket-0 development source")
    times = raw.time_s.to_numpy(np.int64)
    ids = raw.instance_hash.to_numpy()
    codes, _ = pd.factorize(ids, sort=False)
    keys = np.empty(len(raw), dtype=[("instance", "<i8"), ("time", "<i8")])
    keys["instance"], keys["time"] = codes, times
    repeated = keys[1:] == keys[:-1]
    unique = np.ones(len(raw), dtype=bool)
    unique[1:] &= ~repeated
    unique[:-1] &= ~repeated

    sm = raw.sm_util.to_numpy(float)
    samples = raw.samples.to_numpy(float)
    gpu = raw.gpu_count.to_numpy(float)
    cluster_present = raw.cluster_id.notna().to_numpy()
    cluster = raw.cluster_id.astype("string").fillna("<missing>").to_numpy(dtype=str)
    valid_measurement = (np.isfinite(sm) & (sm >= 0) & (sm <= 100)
                         & np.isfinite(samples) & (samples > 0)
                         & np.isfinite(gpu) & (gpu > 0) & cluster_present)
    current = unique & valid_measurement & (sm < 80)
    decisions = times + original.DECISION_DELAY_S
    records = {}
    for name, (start, end) in SPLITS.items():
        in_split = ((decisions >= start) &
                    (decisions + original.HORIZON_S + original.LABEL_DELIVERY_DELAY_S <= end))
        selected = np.flatnonzero(in_split & current)
        if not len(selected):
            raise ValueError(f"no {name} current-normal decisions")
        observations = []
        for offset in PAST_OFFSETS[:2] + FUTURE_OFFSETS:
            position, found = original._exact_lookup(keys, codes[selected], times[selected], offset)
            same_allocation = ((cluster[position] == cluster[selected]) &
                               (gpu[position] == gpu[selected]))
            valid = found & unique[position] & valid_measurement[position] & same_allocation
            observations.append((valid, sm[position]))
        past_count = (observations[0][0].astype(np.int8) +
                      observations[1][0].astype(np.int8) + 1)
        future_valid = np.column_stack([item[0] for item in observations[2:]])
        future_high = np.column_stack([item[1] >= 80 for item in observations[2:]])
        classes = classify_future(future_valid, future_high)
        day = decisions[selected] // 86400
        six_hour = ((decisions[selected] % 86400) // 21600).astype(int)
        split_result = {
            "candidate_decisions": int(len(selected)),
            "distinct_instances": int(len(set(ids[selected]))),
            "past_valid_count": {str(k): int(np.sum(past_count == k)) for k in (1, 2, 3)},
            "future_full_observation": int(future_valid.all(axis=1).sum()),
            "future_incomplete": int((~future_valid.all(axis=1)).sum()),
            "class_counts": {}, "by_day": {}, "by_six_hour_block": {},
        }
        for code, label in enumerate(CLASS_NAMES):
            mask = classes == code
            per_instance = pd.Series(ids[selected[mask]]).value_counts()
            split_result["class_counts"][label] = {
                "rows": int(mask.sum()), "instances": int(len(set(ids[selected[mask]]))),
                "largest_instance_fraction": float(per_instance.max() / mask.sum()) if mask.any() else None,
            }
        for group_name, grouping in (("by_day", day), ("by_six_hour_block", six_hour)):
            for value in sorted(set(grouping)):
                mask = grouping == value
                split_result[group_name][str(int(value))] = {
                    label: int(np.sum(mask & (classes == code)))
                    for code, label in enumerate(CLASS_NAMES)
                }
        if sum(v["rows"] for v in split_result["class_counts"].values()) != len(selected):
            raise AssertionError("future classes do not partition decisions")
        records[name] = split_result
    return {
        "status": "FEASIBILITY_ONLY_NO_MODEL_SCORES_NO_BUCKET1_FETCH",
        "source_compact_sha256": source["compact_sha256"],
        "source_audit_sha256": source["audit_sha256"],
        "raw_development_rows": int(len(raw)),
        "source_memory_percent_over_100_excluded_from_target": True,
        "target": "SM-only observed >=80%; 0..100 valid; six future 5-minute windows",
        "splits": records,
    }


def main() -> None:
    print(json.dumps(audit(), indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
