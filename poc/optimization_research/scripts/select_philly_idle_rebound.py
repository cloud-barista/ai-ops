"""Fit and select the frozen Philly idle-rebound study on train/validation only.

The Parquet reader excludes December GPU rows. This module has no test scoring
path and writes no row-level labels or predictions.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_philly_idle_rebound as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
OUTPUT = ROOT / "artifacts/philly_idle_rebound_v1"
METRICS = OUTPUT / "validation_metrics.json"
MODEL = OUTPUT / "selected_model.joblib"
SELECTION = OUTPUT / "selection_manifest.json"
BASELINE_ORDER = ("age_24h_beta", "recent_2h_beta", "train_beta")
C_ORDER = ("0.1", "1", "10")
FEATURE_NAMES = (
    "idle_u_1", "idle_u_2", "idle_u_3", "idle_u_4", "idle_u_5", "idle_u_6",
    "past_2h_mean", "past_2h_max", "past_2h_valid_fraction",
    "past_24h_mean", "past_24h_max", "past_24h_valid_fraction",
    "last_high_age_hours_capped_24", "no_observed_high_24h",
    "expected_gpu_slots", "utc_hour_sin", "utc_hour_cos",
)


def history_slice(peak: np.ndarray, valid: np.ndarray, i: int, width: int):
    lo = max(0, i - width + 1)
    positions = np.arange(lo, i + 1)
    usable = valid[positions]
    return positions[usable], peak[positions[usable]], int(usable.sum()) / width


def feature_and_groups(grid: np.ndarray, peak: np.ndarray, valid: np.ndarray,
                       i: int, decision_s: int, slots: int) -> tuple[np.ndarray, str, str]:
    """Use only the 288 latest windows available after the assumed 5m delay."""
    assert int(grid[i]) + audit.DECISION_LAG_S == decision_s
    assert i >= audit.IDLE_WINDOWS - 1
    recent = peak[i - 5:i + 1]
    if not (valid[i - 5:i + 1].all() and np.all(recent < audit.IDLE_THRESHOLD)):
        raise ValueError("Feature extraction requires an eligible six-window idle decision")
    past_2h_idx, past_2h_values, past_2h_fraction = history_slice(peak, valid, i, 24)
    past_24h_idx, past_24h_values, past_24h_fraction = history_slice(peak, valid, i, 288)
    if len(past_2h_values) < 6 or len(past_24h_values) < 6:
        raise AssertionError("An idle candidate must have at least six available windows")
    recent_high = np.any(past_2h_values >= audit.REBOUND_THRESHOLD)
    twohour_group = "high" if recent_high else ("low" if past_2h_fraction == 1 else "unknown")
    past_high_indices = past_24h_idx[past_24h_values >= audit.REBOUND_THRESHOLD]
    if len(past_high_indices):
        last_high_start = int(grid[past_high_indices[-1]])
        high_age_h = min(24.0, (decision_s - (last_high_start + 300)) / 3_600)
        no_high = 0.0
        age_group = "at_most_2h" if high_age_h <= 2 else (
            "over_2_to_6h" if high_age_h <= 6 else "over_6h")
    else:
        high_age_h, no_high, age_group = 24.0, 1.0, "no_observed_high"
    theta = 2 * math.pi * ((decision_s % 86_400) / 86_400)
    features = np.array([
        *recent.tolist(),
        float(np.mean(past_2h_values)), float(np.max(past_2h_values)), past_2h_fraction,
        float(np.mean(past_24h_values)), float(np.max(past_24h_values)), past_24h_fraction,
        high_age_h, no_high, float(slots), math.sin(theta), math.cos(theta),
    ], dtype=float)
    if not np.isfinite(features).all() or len(features) != len(FEATURE_NAMES):
        raise AssertionError("Decision-time features are nonfinite or misaligned")
    return features, twohour_group, age_group


def build_train_validation(source: Path) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Filter source dates before any label/feature construction."""
    end = audit.SPLITS[1][2]
    frame = pd.read_parquet(source, columns=list(audit.READ_COLUMNS),
                            filters=[("time_utc_s", "<", end)])
    if frame.empty or int(frame.time_utc_s.max()) >= end:
        raise ValueError("The train/validation reader crossed the December holdout boundary")
    records: dict[str, list[dict]] = {"train": [], "validation": []}
    for machine_id, group in frame.groupby("machine_id", sort=True):
        grid, peak, valid = audit.windows_for_machine(group)
        slots = int(group.expected_gpu_slots.iloc[0])
        for row in audit.iter_decisions(
                str(machine_id), grid, peak, valid,
                allowed_splits=frozenset(("train", "validation"))):
            if not row["future_complete"]:
                continue
            decision = row["decision_utc_s"]
            i = int((decision - audit.DECISION_LAG_S - int(grid[0])) // 300)
            features, twohour_group, age_group = feature_and_groups(
                grid, peak, valid, i, decision, slots)
            records[row["split"]].append({
                "machine_id": str(machine_id), "decision_utc_s": decision,
                "rebound": int(row["rebound"]), "recent_2h_group": twohour_group,
                "age_24h_group": age_group,
                **dict(zip(FEATURE_NAMES, features.tolist())),
            })
    return frame, {name: pd.DataFrame(rows).sort_values(
        ["decision_utc_s", "machine_id"], kind="mergesort").reset_index(drop=True)
        for name, rows in records.items()}


def fit_beta_baselines(train: pd.DataFrame) -> dict:
    y = train.rebound.to_numpy(int)
    global_rate = float((y.sum() + 1) / (len(y) + 2))
    result = {"train_beta": global_rate, "recent_2h_beta": {}, "age_24h_beta": {}}
    for name, column in (("recent_2h_beta", "recent_2h_group"),
                         ("age_24h_beta", "age_24h_group")):
        for cell, group in train.groupby(column, sort=True):
            result[name][str(cell)] = float(
                (group.rebound.sum() + 20 * global_rate) / (len(group) + 20))
    return result


def predict_beta_baselines(spec: dict, rows: pd.DataFrame) -> dict[str, np.ndarray]:
    prior = spec["train_beta"]
    result = {"train_beta": np.full(len(rows), prior, dtype=float)}
    for name, column in (("recent_2h_beta", "recent_2h_group"),
                         ("age_24h_beta", "age_24h_group")):
        result[name] = rows[column].map(spec[name]).fillna(prior).to_numpy(float)
    return result


def prepare_features(train: pd.DataFrame, validation: pd.DataFrame):
    train_x = train[list(FEATURE_NAMES)].to_numpy(float)
    validation_x = validation[list(FEATURE_NAMES)].to_numpy(float)
    median = np.median(train_x, axis=0)
    scale = np.std(train_x, axis=0)
    scale[scale == 0] = 1.0
    return (train_x - median) / scale, (validation_x - median) / scale, median, scale


def select_from_validation(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[dict, dict]:
    spec = fit_beta_baselines(train)
    beta = predict_beta_baselines(spec, validation)
    y_train = train.rebound.to_numpy(int)
    y_val = validation.rebound.to_numpy(int)
    baseline_brier = {name: float(brier_score_loss(y_val, beta[name]))
                      for name in BASELINE_ORDER}
    chosen_baseline = min(BASELINE_ORDER,
                          key=lambda key: (baseline_brier[key], BASELINE_ORDER.index(key)))
    train_x, val_x, median, scale = prepare_features(train, validation)
    logistic_brier: dict[str, float] = {}
    models = {}
    validation_aux = {}
    for c in C_ORDER:
        model = LogisticRegression(
            penalty="l2", C=float(c), solver="lbfgs", max_iter=2_000,
            class_weight=None, random_state=42,
        )
        model.fit(train_x, y_train)
        p = model.predict_proba(val_x)[:, 1]
        logistic_brier[c] = float(brier_score_loss(y_val, p))
        validation_aux[c] = {
            "average_precision": float(average_precision_score(y_val, p)),
            "mean_probability": float(np.mean(p)), "converged": bool(np.max(model.n_iter_) < 2_000),
        }
        models[c] = model
    chosen_c = min(C_ORDER, key=lambda key: (logistic_brier[key], C_ORDER.index(key)))
    selected = {
        "model": models[chosen_c], "feature_names": FEATURE_NAMES,
        "train_median": median, "train_scale": scale,
        "selected_c": chosen_c, "baseline_spec": spec,
        "selected_baseline": chosen_baseline,
    }
    metrics = {
        "selected_c": chosen_c, "selected_baseline": chosen_baseline,
        "train_n": len(train), "train_positive": int(y_train.sum()),
        "validation_n": len(validation), "validation_positive": int(y_val.sum()),
        "baseline_validation_brier": baseline_brier,
        "model_validation_brier_by_c": logistic_brier,
        "model_validation_diagnostics_by_c": validation_aux,
        "feature_names": list(FEATURE_NAMES),
        "notes": "Only pre-December development train/validation labels were read; no December score or model prediction.",
    }
    return selected, metrics


def run() -> dict:
    if any(path.exists() for path in (METRICS, MODEL, SELECTION)):
        raise FileExistsError("Selection artifacts already exist; do not overwrite a frozen choice")
    freeze = audit.verify_freeze(audit.FREEZE)
    dev = json.loads(audit.DEVELOPMENT_ELIGIBILITY.read_text(encoding="utf-8"))
    dev_sha = audit.sha256_file(audit.DEVELOPMENT_ELIGIBILITY)
    if (dev.get("freeze_manifest_sha256") != audit.sha256_file(audit.FREEZE)
            or dev.get("source_sha256") != audit.SOURCE["development"]["sha256"]
            or dev.get("quality_gate", {}).get("all_pass") is not True):
        raise ValueError("Development source or pre-score eligibility gate differs")
    source, _ = audit.verify_source("development")
    _, tables = build_train_validation(source)
    for name in ("train", "validation"):
        expected = dev["splits"][name]
        actual = tables[name]
        if len(actual) != expected["future_complete"] or int(actual.rebound.sum()) != expected["positive_rebound"]:
            raise ValueError(f"{name} rows or positive support differ from score-free audit")
    selected, metrics = select_from_validation(tables["train"], tables["validation"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(selected, MODEL, compress=3)
    code_sha = audit.sha256_file(SCRIPT)
    metrics.update({
        "kind": "philly_idle_rebound_validation_selection_v1",
        "protocol_sha256": freeze["protocol_sha256"],
        "freeze_manifest_sha256": audit.sha256_file(audit.FREEZE),
        "development_source_sha256": audit.SOURCE["development"]["sha256"],
        "development_eligibility_sha256": dev_sha,
        "selected_model_sha256": audit.sha256_file(MODEL),
        "selection_code_sha256": code_sha,
    })
    METRICS.write_text(json.dumps(metrics, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    selection = {
        "kind": "philly_idle_rebound_selection_freeze_v1",
        "protocol_sha256": freeze["protocol_sha256"],
        "freeze_manifest_sha256": audit.sha256_file(audit.FREEZE),
        "development_source_sha256": audit.SOURCE["development"]["sha256"],
        "development_eligibility_sha256": dev_sha,
        "selected_model_id": "l2_logistic", "selected_c": metrics["selected_c"],
        "selected_baseline": metrics["selected_baseline"],
        "selected_model_path": str(MODEL.relative_to(ROOT)),
        "selected_model_sha256": audit.sha256_file(MODEL),
        "selection_code_path": str(SCRIPT.relative_to(ROOT)),
        "selection_code_sha256": code_sha,
        "validation_metrics_path": str(METRICS.relative_to(ROOT)),
        "validation_metrics_sha256": audit.sha256_file(METRICS),
    }
    SELECTION.write_text(json.dumps(selection, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    audit.verify_selection_for_holdout(SELECTION, freeze, audit.FREEZE)
    return {"selected_c": metrics["selected_c"], "selected_baseline": metrics["selected_baseline"],
            "validation_n": metrics["validation_n"], "selection_manifest_sha256": audit.sha256_file(SELECTION)}


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(), sort_keys=True))


if __name__ == "__main__":
    main()
