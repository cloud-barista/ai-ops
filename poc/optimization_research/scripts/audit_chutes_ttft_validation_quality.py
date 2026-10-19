#!/usr/bin/env python3
"""Post-validation descriptive label audit; never reads sealed test groups."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_chutes_ttft_tail_v1 import (
    ARTIFACT_ROOT, DATA_ROOT, EXPOSED_RG65, REVISION, SPLITS, VALIDATION_PATH,
    build_decisions, load_bins, save_json, sha256, verify_extracted, verify_frozen,
)


OUTPUT = ARTIFACT_ROOT / "validation_quality.json"
STATUS_ORDER = [
    "missing_or_nonfinite_ttft",
    "nonpositive_ttft",
    "missing_or_nonpositive_completion",
    "ttft_exceeds_request_duration",
    "completion_after_60min_feedback_limit",
    "valid_label",
]


def classify(part: pd.DataFrame) -> np.ndarray:
    duration = (part.completed_at - part.started_at).dt.total_seconds().to_numpy(dtype=float)
    ttft = part.ttft.to_numpy(dtype=float)
    status = np.full(len(part), "valid_label", dtype=object)
    pending = np.ones(len(part), dtype=bool)
    rules = [
        ("missing_or_nonfinite_ttft", ~np.isfinite(ttft)),
        ("nonpositive_ttft", ttft <= 0),
        ("missing_or_nonpositive_completion", ~np.isfinite(duration) | (duration <= 0)),
        ("ttft_exceeds_request_duration", ttft > duration + 0.001),
        ("completion_after_60min_feedback_limit",
         (part.completed_at > part.started_at.dt.floor("5min") + pd.Timedelta(minutes=65)).to_numpy()),
    ]
    for name, condition in rules:
        matched = pending & condition
        status[matched] = name
        pending &= ~matched
    return status


def audit() -> dict:
    verify_frozen()
    verify_extracted(73)
    validation = json.loads(VALIDATION_PATH.read_text())
    if validation["frozen_manifest_sha256"] != sha256(ARTIFACT_ROOT / "frozen_manifest.json"):
        raise ValueError("validation and frozen manifest disagree")
    registry = set(validation["registry"])
    split_counts: dict[str, Counter] = defaultdict(Counter)
    model_counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for group in range(65, 74):
        path = EXPOSED_RG65 if group == 65 else DATA_ROOT / f"rg{group:03d}.parquet"
        frame = pd.read_parquet(
            path, columns=["function_name", "chute_id", "started_at", "completed_at", "ttft"]
        )
        for split in ("train", "val"):
            start, end = SPLITS[split]
            eligible = frame.loc[
                frame.function_name.isin(["chat_stream", "completion_stream"])
                & frame.chute_id.isin(registry)
                & frame.started_at.ge(start)
                & frame.started_at.lt(end)
            ].copy()
            if eligible.empty:
                continue
            eligible["reason"] = classify(eligible)
            for (model, reason), count in eligible.groupby(["chute_id", "reason"]).size().items():
                split_counts[split][str(reason)] += int(count)
                model_counts[split][str(model)][str(reason)] += int(count)
    result = {
        "status": "descriptive_after_validation_no_test_opened",
        "source_revision": REVISION,
        "validation_sha256": sha256(VALIDATION_PATH),
        "source_groups": list(range(65, 74)),
        "splits": {},
    }
    for split in ("train", "val"):
        counts = split_counts[split]
        total = sum(counts.values())
        expected = validation["supports"][split]
        if total != expected["stream_starts_on_model_grid"] or counts["valid_label"] != expected["valid_ttft_on_model_grid"]:
            raise ValueError(f"descriptive label accounting disagrees with frozen result: {split}")
        result["splits"][split] = {
            "total_registered_stream_starts": total,
            "counts": {name: counts[name] for name in STATUS_ORDER},
            "valid_fraction": counts["valid_label"] / total,
            "per_model": {
                model: {
                    "total": sum(parts.values()),
                    "counts": {name: parts[name] for name in STATUS_ORDER},
                    "valid_fraction": parts["valid_label"] / sum(parts.values()),
                }
                for model, parts in sorted(model_counts[split].items())
            },
        }

    bins = load_bins(73)
    val = build_decisions(bins, validation["registry"], "val")
    primary = val.loc[
        val.eligible_past & val.future_valid_ttft.ge(100)
        & val.model.isin(validation["evaluation_models"])
    ]
    result["validation_primary_feature_support"] = {
        "rows": int(len(primary)),
        "observed_instances30_eq1": int((primary.observed_instances30 == 1).sum()),
        "observed_instances30_le2": int((primary.observed_instances30 <= 2).sum()),
        "median_observed_instances30": float(primary.observed_instances30.median()),
        "median_cache_token_fraction30": float(primary.cache_token_fraction30.median()),
        "models_with_constant_observed_instances30": int(
            (primary.groupby("model").observed_instances30.nunique() == 1).sum()
        ),
    }
    return result


if __name__ == "__main__":
    report = audit()
    save_json(OUTPUT, report)
    print(json.dumps({
        "train": report["splits"]["train"]["counts"],
        "val": report["splits"]["val"]["counts"],
        "validation_primary_feature_support": report["validation_primary_feature_support"],
    }, indent=2))
