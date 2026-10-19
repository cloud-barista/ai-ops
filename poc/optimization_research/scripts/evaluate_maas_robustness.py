"""Chronological stress test of one-hour normalized-QPS forecasts on MaaS cases.

This reuses the pinned app-case loader and fixed forecast formulas. Each origin
selects a simple static comparator on the preceding completed day and scores
the following day. The seasonal correction was devised after inspecting this
same week, so these are retrospective sensitivity checks, not independent
prospective validation or evidence of a scaling policy.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_maas_trace import (
    ROOT, SOURCE, STATIC_BASELINES, build_examples, load_qps,
    select_static_by_app, sha256, verify_sources,
)


OUTPUT = ROOT / "artifacts/maas_robustness_v1"
ORIGINS = (72, 96, 120, 144)
HORIZON = 1
METHODS = STATIC_BASELINES + (
    "validation_selected_static_by_app", "level_corrected_seasonal",
    "validation_selected_persistence_or_corrected",
)


def split_origin(frame: pd.DataFrame, test_start: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Score only targets that finish before the next decision boundary.

    `decision_idx` labels the most recent observed hourly bin. A decision is
    assumed to occur after that bin has completed. Validation target bins
    are strictly earlier than the first test decision bin. The test day uses
    decision bins [test_start, test_start + 24), without reusing test rows.
    """
    if test_start < 48 or test_start % 24:
        raise ValueError("test_start must be a day boundary with at least two prior days")
    validation_start = test_start - 24
    train = frame.loc[frame.target_idx.lt(validation_start)].copy()
    validation = frame.loc[
        frame.decision_idx.ge(validation_start) & frame.target_idx.lt(test_start)
    ].copy()
    test = frame.loc[
        frame.decision_idx.ge(test_start) & frame.decision_idx.lt(test_start + 24)
    ].copy()
    if min(map(len, (train, validation, test))) < 20:
        raise ValueError("insufficient rows at chronological origin")
    if not (train.target_idx.max() < validation_start
            and validation.target_idx.max() < test_start
            and test.decision_idx.min() >= test_start):
        raise AssertionError("an evaluation target is not yet available at its boundary")
    return train, validation, test


def with_selected_static(validation: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    choice = select_static_by_app(validation)
    scored = test.copy()
    scored["validation_selected_static_by_app"] = [
        scored.loc[i, choice[app]] for i, app in scored.app_id.items()
    ]
    return scored, choice


def with_selected_pair(validation: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Select current-value persistence or the fixed correction per app."""
    pair = ("last_value", "level_corrected_seasonal")
    choice = {
        app: min(pair, key=lambda name: (
            float(np.abs(rows.observed_qps_norm - rows[name]).mean()), pair.index(name)
        ))
        for app, rows in validation.groupby("app_id", sort=True)
    }
    scored = test.copy()
    scored["validation_selected_persistence_or_corrected"] = [
        scored.loc[i, choice[app]] for i, app in scored.app_id.items()
    ]
    return scored, choice


def error_summary(frame: pd.DataFrame) -> dict:
    """Equal-weight application-hour MAE, including zero-QPS hours."""
    y = frame.observed_qps_norm.to_numpy(float)
    return {
        "n": int(len(frame)),
        "positive_qps_rows": int(np.count_nonzero(y > 0)),
        "mae": {
            method: float(np.mean(np.abs(y - frame[method].to_numpy(float))))
            for method in METHODS
        },
    }


def paired_day_interval(frame: pd.DataFrame, first: str, second: str) -> dict:
    """Exact paired resampling of observed day blocks; descriptive only."""
    days = sorted(frame.test_day.unique())
    if len(days) < 2:
        raise ValueError("At least two test days are needed")
    y = frame.observed_qps_norm.to_numpy(float)
    delta = np.abs(y - frame[first].to_numpy(float)) - np.abs(y - frame[second].to_numpy(float))
    block = [(int(np.count_nonzero(frame.test_day.to_numpy() == day)),
              float(delta[frame.test_day.to_numpy() == day].sum())) for day in days]
    draws = []
    for sample in itertools.product(range(len(days)), repeat=len(days)):
        n = sum(block[i][0] for i in sample)
        if n:
            draws.append(sum(block[i][1] for i in sample) / n)
    if not draws:
        raise ValueError("No valid paired draws")
    return {
        "first_minus_second_mae": float(delta.mean()),
        "interval_95_within_week": np.quantile(draws, [0.025, 0.975]).tolist(),
        "test_days": len(days),
        "exact_resamples": len(draws),
        "note": "Exploratory within-week day resampling; ignores method-development and between-week uncertainty",
    }


def summarize(frame: pd.DataFrame, *, include_uncertainty: bool = True) -> dict:
    by_app = {}
    for app, rows in frame.groupby("app_label", sort=True):
        positive = rows.loc[rows.observed_qps_norm.gt(0)]
        by_app[app] = {
            "all_hours": error_summary(rows),
            "positive_qps_hours": error_summary(positive) if len(positive) else None,
        }
    positive = frame.loc[frame.observed_qps_norm.gt(0)]
    result = {
        "all_hours": error_summary(frame),
        "positive_qps_hours": error_summary(positive),
        "by_app": by_app,
    }
    if include_uncertainty:
        result["corrected_minus_persistence"] = paired_day_interval(
            frame, "level_corrected_seasonal", "last_value"
        )
        result["corrected_minus_validation_selected"] = paired_day_interval(
            frame, "level_corrected_seasonal", "validation_selected_static_by_app"
        )
        result["pair_selected_minus_corrected"] = paired_day_interval(
            frame, "validation_selected_persistence_or_corrected", "level_corrected_seasonal"
        )
        result["positive_corrected_minus_persistence"] = paired_day_interval(
            positive, "level_corrected_seasonal", "last_value"
        )
        for app, rows in frame.groupby("app_label", sort=True):
            result["by_app"][app]["corrected_minus_persistence"] = paired_day_interval(
                rows, "level_corrected_seasonal", "last_value"
            )
            app_positive = rows.loc[rows.observed_qps_norm.gt(0)]
            if app_positive.test_day.nunique() >= 2:
                result["by_app"][app]["positive_corrected_minus_persistence"] = paired_day_interval(
                    app_positive, "level_corrected_seasonal", "last_value"
                )
    return result


def evaluate(source: Path = SOURCE, output: Path = OUTPUT) -> dict:
    if source.resolve() != SOURCE.resolve():
        raise ValueError("Only the pinned public app cases are supported")
    source_hashes = verify_sources(source)
    qps, labels, diagnostics = load_qps(source)
    frame = build_examples(qps, HORIZON)
    origin_rows = []
    scored = []
    for origin in ORIGINS:
        train, validation, test = split_origin(frame, origin)
        test, choice = with_selected_static(validation, test)
        test, pair_choice = with_selected_pair(validation, test)
        test["test_day"] = origin // 24
        test["app_label"] = test.app_id.map(labels)
        origin_rows.append({
            "test_day": origin // 24,
            "test_start_hour": origin,
            "train_rows": int(len(train)),
            "validation_rows": int(len(validation)),
            "test_rows": int(len(test)),
            "validation_max_target_hour": int(validation.target_idx.max()),
            "test_min_decision_hour": int(test.decision_idx.min()),
            "selected_static_by_app": choice,
            "selected_persistence_or_corrected_by_app": pair_choice,
            "scores": summarize(test, include_uncertainty=False),
        })
        scored.append(test)
    combined = pd.concat(scored, ignore_index=True)
    if combined.duplicated(["app_id", "target_idx"]).any():
        raise AssertionError("overlapping test targets")
    summary = summarize(combined)
    result = {
        "experiment": "MaaS one-hour normalized QPS retrospective robustness",
        "source_sha256": source_hashes,
        "evaluation_script_sha256": sha256(Path(__file__)),
        "upstream_script_sha256": sha256(ROOT / "scripts/evaluate_maas_trace.py"),
        "horizon_hours": HORIZON,
        "decision_time_assumption": "after the indexed hourly observation bin ends",
        "test_origins_hours": list(ORIGINS),
        "origins": origin_rows,
        "pooled": summary,
        "diagnostics": {
            "qps_apps": diagnostics["qps_apps"],
            "hourly_timestamps": diagnostics["hourly_timestamps"],
        },
        "limits": [
            "QPS is normalized by a peak determined after the week; these are released-trace offline scores",
            "The corrected seasonal formula was explored on this same week",
            "No scale actions, absolute QPS, user SLO, or causal scale-effect outcomes",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    columns = [
        "test_day", "app_id", "app_label", "decision_idx", "target_idx",
        "observed_qps_norm", *METHODS,
    ]
    combined[columns].to_parquet(output / "test_predictions.parquet", index=False)
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.source, args.output)
    print(json.dumps({"pooled": result["pooled"], "origins": [
        {"test_day": row["test_day"], "scores": row["scores"]["all_hours"]}
        for row in result["origins"]
    ]}, ensure_ascii=False, indent=2))
