"""Chronological, selection-only audit of an abstaining GenTD26 p90 guard.

The first day of each selection block chooses the calibration policy. Labels
available from the second day by test start audit that *frozen* policy. No test
outcome selects a gate, blend, margin, or rolling window. Actual completion
timestamps are unavailable: feedback availability is assumed, not observed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_gentd26_coverage_guard import (
    BLENDS, _keys, fit_guard, predict_guard, selected_group_prediction,
)
from evaluate_gentd26_request_latency import (
    DEFAULT_DATA, encode_features, fit_feature_state, load_requests, metrics,
    train_quantile,
)
from evaluate_gentd26_subgroup import _evaluate_candidates, causal_global_offsets
from evaluate_gentd26_walkforward import (
    FEEDBACK_DELAY_SECONDS, ORIGINS, ROOT, assumed_availability,
    selectable_feedback,
    select_rolling_window, sha256, split_origin,
)


DEFAULT_OUTPUT = ROOT / "artifacts/gentd26_selective_guard_v1"
GROUPS = ("predict_type", "checkpoint_model_version_id")
POLICY_TARGET = 0.92
# These are post-hoc hypotheses proposed after inspecting the existing five
# folds, not preregistered thresholds or operational reliability guarantees.
SELECTION_MIN = 100
AUDIT_MIN = 30
AUDIT_TARGET = 0.90
STRICT_AUDIT_MIN = 50
STRICT_LOWER_TARGET = 0.90


def wilson_lower(successes: int, n: int, z: float = 1.2815515655446004) -> float:
    """One-sided 90% Wilson lower bound; no value with zero observations."""
    if n < 0 or successes < 0 or successes > n or z < 0:
        raise ValueError("invalid binomial observations or z")
    if n == 0:
        return 0.0
    p = successes / n
    denominator = 1 + z * z / n
    center = p + z * z / (2 * n)
    spread = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((center - spread) / denominator)


def coverage_record(frame: pd.DataFrame, prediction: np.ndarray) -> dict:
    y = frame.exec_time_seconds.to_numpy(float)
    n = len(frame)
    hits = int(np.sum(y <= prediction))
    return {
        "n": n,
        "hits": hits,
        "coverage": hits / n if n else None,
        "wilson_90pct_lower": wilson_lower(hits, n),
    }


def partition_selection(
    selection: pd.DataFrame, origin: pd.Timestamp, delay_seconds: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Separate policy fitting from the later held-out audit by event time."""
    audit_start = pd.Timestamp(origin) - pd.Timedelta(days=1)
    early = selection.loc[selection.t.lt(audit_start)].copy()
    late = selection.loc[selection.t.ge(audit_start)].copy()
    policy_fit = selectable_feedback(early, audit_start, delay_seconds)
    audit = selectable_feedback(late, pd.Timestamp(origin), delay_seconds)
    available = selectable_feedback(selection, pd.Timestamp(origin), delay_seconds)
    if not policy_fit.index.is_unique or not audit.index.is_unique:
        raise ValueError("selection request IDs must be unique")
    if not set(policy_fit.index).isdisjoint(audit.index):
        raise AssertionError("policy-fitting and audit labels overlap")
    if not policy_fit.empty and assumed_availability(
        policy_fit, delay_seconds
    ).gt(audit_start).any():
        raise AssertionError("policy fit used feedback unavailable at audit start")
    if not audit.empty and assumed_availability(
        audit, delay_seconds
    ).gt(pd.Timestamp(origin)).any():
        raise AssertionError("audit used feedback unavailable at test origin")
    return early, policy_fit, audit, available


def gate_evidence(
    policy_fit: pd.DataFrame, audit: pd.DataFrame,
    audit_prediction: np.ndarray, selection_available: pd.DataFrame,
) -> dict:
    """Compile evidence known by the test origin; preserve zero-count groups."""
    evidence = {"overall_audit": coverage_record(audit, audit_prediction), "groups": {}}
    for column in GROUPS:
        values = {}
        audit_keys = _keys(audit, column)
        policy_keys = _keys(policy_fit, column)
        selection_keys = _keys(selection_available, column)
        for key in sorted(set(selection_keys) | set(audit_keys)):
            chosen = audit_keys == key
            record = coverage_record(audit.loc[chosen], audit_prediction[chosen])
            record["policy_fit_support"] = int(np.sum(policy_keys == key))
            record["selection_support_at_origin"] = int(np.sum(selection_keys == key))
            values[str(key)] = record
        evidence["groups"][column] = values
    return evidence


def decide(
    frame: pd.DataFrame, evidence: dict, rule: str,
    selection_min: int = SELECTION_MIN, audit_min: int = AUDIT_MIN,
) -> np.ndarray:
    """Return acceptance from pre-test counts/coverage and request metadata."""
    if rule not in ("support", "audit_empirical", "audit_wilson"):
        raise ValueError("unknown rule")
    if selection_min < 1 or audit_min < 1:
        raise ValueError("support thresholds must be positive")
    accepted = np.ones(len(frame), dtype=bool)
    overall = evidence["overall_audit"]
    if rule == "audit_empirical":
        accepted &= overall["n"] >= 200 and overall["coverage"] is not None and overall["coverage"] >= AUDIT_TARGET
    if rule == "audit_wilson":
        accepted &= overall["n"] >= 200 and overall["wilson_90pct_lower"] >= STRICT_LOWER_TARGET
    for column in GROUPS:
        keys = _keys(frame, column)
        group_evidence = evidence["groups"][column]
        for key in np.unique(keys):
            record = group_evidence.get(str(key))
            # The frozen guard has learned a group margin only from the first
            # selection day. Later audit support cannot repair absent policy-fit
            # support retroactively.
            pass_group = (
                record is not None
                and record["policy_fit_support"] >= selection_min
                and record["selection_support_at_origin"] >= selection_min
            )
            if rule == "audit_empirical":
                pass_group = (
                    pass_group and record["n"] >= audit_min
                    and record["coverage"] is not None and record["coverage"] >= AUDIT_TARGET
                )
            elif rule == "audit_wilson":
                pass_group = (
                    pass_group and record["n"] >= STRICT_AUDIT_MIN
                    and record["wilson_90pct_lower"] >= STRICT_LOWER_TARGET
                )
            accepted[keys == key] &= pass_group
    return accepted


def selective_metrics(frame: pd.DataFrame, prediction_column: str, accepted: np.ndarray) -> dict:
    accepted = np.asarray(accepted, bool)
    if len(accepted) != len(frame):
        raise ValueError("mask length mismatch")
    result = {
        "total": len(frame),
        "accepted": int(accepted.sum()),
        "abstained": int((~accepted).sum()),
        "acceptance_fraction": float(accepted.mean()) if len(frame) else None,
        "abstention_fraction": float((~accepted).mean()) if len(frame) else None,
    }
    result["accepted_metrics"] = (
        metrics(
            frame.loc[accepted, "exec_time_seconds"].to_numpy(float),
            frame.loc[accepted, prediction_column].to_numpy(float),
        ) if accepted.any() else None
    )
    return result


def _subgroup(frame: pd.DataFrame, column: str, key: str, rule: str) -> dict:
    rows = frame.loc[_keys(frame, column) == key]
    return selective_metrics(rows, "nested_guard92_p90", rows[f"accept_{rule}"].to_numpy(bool))


def evaluate(
    data_path: Path = DEFAULT_DATA, output: Path = DEFAULT_OUTPUT,
    delay_seconds: int = FEEDBACK_DELAY_SECONDS,
    origins: tuple[pd.Timestamp, ...] = ORIGINS,
) -> dict:
    if delay_seconds < 0:
        raise ValueError("feedback delay must be nonnegative")
    if any(b - a < pd.Timedelta(days=2) for a, b in zip(origins, origins[1:])):
        raise ValueError("test windows must not overlap")
    data = load_requests(data_path)
    folds: list[dict] = []
    predictions: list[pd.DataFrame] = []
    for origin in origins:
        frames, bounds = split_origin(data, origin, delay_seconds)
        fit, calibration, selection, test = (
            frames[name].copy() for name in ("fit", "calibration", "selection", "test")
        )
        state = fit_feature_state(fit)
        model = train_quantile(fit, state)
        for frame in (calibration, selection, test):
            frame["base_p90"] = model.predict(encode_features(frame, state))

        audit_start = origin - pd.Timedelta(days=1)
        early, policy_fit, audit, selection_available = partition_selection(
            selection, origin, delay_seconds
        )
        if len(policy_fit) < 100 or len(audit) < 100:
            raise ValueError(f"too few split-selection labels at {origin}")
        global_window, _ = select_rolling_window(calibration, policy_fit, delay_seconds)
        candidates, _ = _evaluate_candidates(
            calibration, policy_fit, global_window,
            np.ones(len(policy_fit), bool), delay_seconds,
        )
        winner = min(candidates, key=lambda row: row["pinball_seconds"])
        fit_global_offset = causal_global_offsets(
            calibration, policy_fit, global_window, delay_seconds,
        )
        fit_global = np.maximum(0, policy_fit.base_p90.to_numpy(float) + fit_global_offset)
        fit_group = selected_group_prediction(
            calibration, policy_fit, fit_global_offset, winner, delay_seconds,
        )
        guard = min(
            (fit_guard(policy_fit, fit_global, fit_group, blend, POLICY_TARGET)
             for blend in BLENDS),
            key=lambda policy: policy["selection"]["pinball_seconds"],
        )

        audit_history = pd.concat([calibration, early], ignore_index=True)
        audit_offset = causal_global_offsets(audit_history, audit, global_window, delay_seconds)
        audit_global = np.maximum(0, audit.base_p90.to_numpy(float) + audit_offset)
        audit_group = selected_group_prediction(
            audit_history, audit, audit_offset, winner, delay_seconds,
        )
        audit_prediction = predict_guard(audit, audit_global, audit_group, guard)
        evidence = gate_evidence(policy_fit, audit, audit_prediction, selection_available)

        test_history = pd.concat([calibration, selection], ignore_index=True)
        test_offset = causal_global_offsets(test_history, test, global_window, delay_seconds)
        test_global = np.maximum(0, test.base_p90.to_numpy(float) + test_offset)
        test_group = selected_group_prediction(
            test_history, test, test_offset, winner, delay_seconds,
        )
        test_prediction = predict_guard(test, test_global, test_group, guard)
        fold_pred = test[["t", "predict_type", "checkpoint_model_version_id", "exec_time_seconds"]].copy()
        fold_pred["origin"] = str(origin)
        fold_pred["nested_guard92_p90"] = test_prediction
        for rule in ("support", "audit_empirical", "audit_wilson"):
            fold_pred[f"accept_{rule}"] = decide(test, evidence, rule)

        folds.append({
            "origin": str(origin),
            "boundaries": bounds,
            "rows": {name: len(frame) for name, frame in frames.items()},
            "policy_fit_cutoff": str(audit_start),
            "policy_fit_available_rows": len(policy_fit),
            "audit_available_rows": len(audit),
            "latest_policy_fit_assumed_availability": str(
                assumed_availability(policy_fit, delay_seconds).max()
            ),
            "latest_audit_assumed_availability": str(
                assumed_availability(audit, delay_seconds).max()
            ),
            "selection_available_rows_at_origin": len(selection_available),
            "global_window": global_window,
            "selected_group": {key: winner[key] for key in (
                "group", "window", "minimum", "shrinkage", "pinball_seconds", "coverage",
            )},
            "guard_blend": guard["blend"],
            "guard_policy_fit": guard["selection"],
            "audit": coverage_record(audit, audit_prediction),
            "evidence": evidence,
            "all_test": metrics(test.exec_time_seconds.to_numpy(float), test_prediction),
            "rules": {
                rule: selective_metrics(
                    fold_pred, "nested_guard92_p90", fold_pred[f"accept_{rule}"].to_numpy(bool)
                ) for rule in ("support", "audit_empirical", "audit_wilson")
            },
        })
        predictions.append(fold_pred)

    pooled = pd.concat(predictions, ignore_index=True)
    if pooled.groupby("t").origin.nunique().max() > 1:
        raise AssertionError("overlapping test folds")
    result = {
        "source": "Alibaba GenTD26 lora_request_trace.csv",
        "source_sha256": sha256(data_path),
        "script_sha256": sha256(Path(__file__)),
        "helper_sha256": {
            name: sha256(ROOT / "scripts" / name) for name in (
                "evaluate_gentd26_request_latency.py",
                "evaluate_gentd26_subgroup.py",
                "evaluate_gentd26_walkforward.py",
                "evaluate_gentd26_coverage_guard.py",
            )
        },
        "assumed_feedback_delay_seconds": delay_seconds,
        "policy_target": POLICY_TARGET,
        "post_hoc_gate_hypotheses": {
            "policy_fit_min_per_type_and_model": SELECTION_MIN,
            "selection_available_min_per_type_and_model": SELECTION_MIN,
            "audit_min_per_type_and_model": AUDIT_MIN,
            "audit_empirical_target": AUDIT_TARGET,
            "strict_audit_min": STRICT_AUDIT_MIN,
            "strict_wilson_lower_target": STRICT_LOWER_TARGET,
        },
        "folds": folds,
        "pooled_all_test": metrics(
            pooled.exec_time_seconds.to_numpy(float),
            pooled.nested_guard92_p90.to_numpy(float),
        ),
        "pooled_rules": {
            rule: selective_metrics(
                pooled, "nested_guard92_p90", pooled[f"accept_{rule}"].to_numpy(bool)
            ) for rule in ("support", "audit_empirical", "audit_wilson")
        },
        "pooled_groups": {
            "IMG_2_IMG": {rule: _subgroup(pooled, "predict_type", "IMG_2_IMG", rule)
                          for rule in ("support", "audit_empirical", "audit_wilson")},
            "M0003": {rule: _subgroup(pooled, "checkpoint_model_version_id", "M0003", rule)
                      for rule in ("support", "audit_empirical", "audit_wilson")},
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    pooled.to_parquet(output / "test_predictions.parquet", index=False)
    (output / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--feedback-delay-seconds", type=int, default=FEEDBACK_DELAY_SECONDS)
    args = parser.parse_args()
    outcome = evaluate(args.data, args.output, args.feedback_delay_seconds)
    print(json.dumps({
        "pooled_all_test": outcome["pooled_all_test"],
        "pooled_rules": outcome["pooled_rules"],
        "pooled_groups": outcome["pooled_groups"],
    }, indent=2))
