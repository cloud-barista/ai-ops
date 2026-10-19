"""Audit frozen and delayed-label calibration of GenTD26 queue-risk models.

At the end of minute t a five-minute future outcome for minute s becomes
available only if s + 6 minutes <= t. Model weights remain frozen. Rolling
intercepts use only observed outcomes from the validation period onward.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = ROOT / "scripts/evaluate_gentd26_monitoring.py"
BASE_ARTIFACTS = ROOT / "artifacts/gentd26_monitoring_v1"
SOURCE = ROOT / "data/external/gentd26"
OUTPUT = ROOT / "artifacts/gentd26_queue_calibration_v1"

spec = importlib.util.spec_from_file_location("gentd26_monitoring_base", BASE_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot import original GenTD26 monitoring experiment")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

MODEL_COLUMNS = {
    "qps_logistic": ["generative_qps_sum", "generative_qps_sum_past5_mean",
                     "generative_qps_sum_past15_mean", "generative_qps_n", "api_qps_sum"],
    "qps_sample_count_logistic": ["generative_qps_n", "generative_qps_n_past5_mean",
                                   "generative_qps_n_past15_mean", "api_qps_n"],
    "all_logistic": None,  # All original features in their original order.
}
WINDOWS_MINUTES = (30, 60, 120, 240)
RIDGE_STRENGTHS = (1.0, 4.0, 16.0)
TARGET = "queue_future_positive"
OBSERVED = "queue_future_observed"
TASK = "future_queue_positive"
LABEL_DELAY_MINUTES = base.HORIZON_MIN + 1


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probability_from_logit(logit: np.ndarray) -> np.ndarray:
    """Stable sigmoid without relying on package-specific expit behavior."""
    logit = np.asarray(logit, dtype=float)
    out = np.empty_like(logit)
    positive = logit >= 0
    out[positive] = 1 / (1 + np.exp(-logit[positive]))
    exponential = np.exp(logit[~positive])
    out[~positive] = exponential / (1 + exponential)
    return out


def shifted_probability(probability: np.ndarray, offset: float) -> np.ndarray:
    probability = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    logits = np.log(probability) - np.log1p(-probability)
    return probability_from_logit(logits + offset)


def fit_intercept(probability: np.ndarray, outcome: np.ndarray,
                  *, ridge: float, prior: float = 0.0) -> float:
    """Fit an offset-only logistic model with ridge pull toward `prior`.

    Ridge > 0 ensures a finite answer even when no events were observed.
    The root is unique because the derivative is strictly decreasing.
    """
    if ridge <= 0:
        raise ValueError("ridge must be positive")
    probability = np.asarray(probability, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    if probability.shape != outcome.shape:
        raise ValueError("probability and outcome must match")
    if len(outcome) == 0:
        return float(prior)

    def derivative(delta: float) -> float:
        return float(np.sum(outcome - shifted_probability(probability, delta))
                     - ridge * (delta - prior))

    low, high = -30.0, 30.0
    if derivative(low) < 0 or derivative(high) > 0:
        raise RuntimeError("Intercept root outside the supported numerical range")
    for _ in range(70):
        middle = (low + high) / 2
        if derivative(middle) > 0:
            low = middle
        else:
            high = middle
    return float((low + high) / 2)


def causal_rolling_intercept(index: pd.DatetimeIndex, probability: np.ndarray,
                             outcome: np.ndarray, *, window_minutes: int,
                             ridge: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Predict with historical labels whose *entire* target window has ended.

    The index contains only rows with complete five-minute queue monitoring.
    During validation it contains validation rows only. During testing it
    contains validation and test rows, so no fitted training labels are reused.
    """
    if not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("index must be strictly increasing")
    if window_minutes <= LABEL_DELAY_MINUTES:
        raise ValueError("rolling window must exceed label delay")
    probability = np.asarray(probability, dtype=float)
    outcome = np.asarray(outcome, dtype=int)
    if len(index) != len(probability) or len(index) != len(outcome):
        raise ValueError("index, probability and outcome must match")
    if not np.isin(outcome, [0, 1]).all():
        raise ValueError("outcomes must be binary")
    prediction = np.empty(len(index), dtype=float)
    offsets = np.empty(len(index), dtype=float)
    counts = np.empty(len(index), dtype=int)
    values = index.asi8
    minute_ns = pd.Timedelta(minutes=1).value
    for position, time_ns in enumerate(values):
        earliest = time_ns - window_minutes * minute_ns
        latest = time_ns - LABEL_DELAY_MINUTES * minute_ns
        left = int(np.searchsorted(values, earliest, side="left"))
        right = min(position, int(np.searchsorted(values, latest, side="right")))
        offset = fit_intercept(probability[left:right], outcome[left:right], ridge=ridge)
        prediction[position] = shifted_probability(probability[position:position + 1], offset)[0]
        offsets[position] = offset
        counts[position] = right - left
    return prediction, offsets, counts


def score(outcome: np.ndarray, probability: np.ndarray) -> dict:
    outcome = np.asarray(outcome, dtype=int)
    probability = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    return {
        "n": int(len(outcome)),
        "positive": int(outcome.sum()),
        "prevalence": float(outcome.mean()),
        "brier": float(brier_score_loss(outcome, probability)),
        "log_loss": float(log_loss(outcome, probability, labels=[0, 1])),
        "average_precision": (float(average_precision_score(outcome, probability))
                              if outcome.sum() else None),
        "roc_auc": (float(roc_auc_score(outcome, probability))
                    if len(np.unique(outcome)) == 2 else None),
        "mean_predicted_risk": float(probability.mean()),
        "calibration_in_the_large": float(probability.mean() - outcome.mean()),
    }


def reliability_bins(outcome: np.ndarray, probability: np.ndarray,
                     *, count: int = 5) -> dict:
    """Equal-count bins avoid empty bins when late predictions concentrate."""
    order = np.argsort(probability, kind="stable")
    groups = np.array_split(order, count)
    bins = [
        {"n": int(len(group)), "positive": int(np.sum(outcome[group])),
         "observed_rate": float(np.mean(outcome[group])),
         "mean_predicted_risk": float(np.mean(probability[group]))}
        for group in groups
    ]
    ece = sum(row["n"] * abs(row["mean_predicted_risk"] - row["observed_rate"])
              for row in bins) / len(outcome)
    return {"equal_count_bins": bins, "expected_calibration_error": float(ece)}


def selected_rows(source: Path) -> tuple[dict[str, pd.DataFrame], dict[str, np.ndarray]]:
    features, labels = base.aggregate_features(source)
    masks = base.chronological_masks(features.index)
    selected_features = {}
    selected_outcomes = {}
    for split in ("train", "validation", "test"):
        mask = masks[split] & labels[OBSERVED].to_numpy(bool)
        selected_features[split] = features.loc[mask]
        selected_outcomes[split] = labels.loc[mask, TARGET].to_numpy(int)
    return selected_features, selected_outcomes


def model_predictions(features: dict[str, pd.DataFrame],
                      source_artifacts: Path) -> dict[str, dict[str, np.ndarray]]:
    fitted = joblib.load(source_artifacts / "models.joblib")[TASK]
    result = {}
    for name, columns in MODEL_COLUMNS.items():
        used_columns = list(features["validation"].columns) if columns is None else columns
        result[name] = {
            split: fitted[name].predict_proba(features[split][used_columns])[:, 1]
            for split in ("validation", "test")
        }
    # The original frozen test predictions are an independent reproducibility
    # check against accidental changes in feature order or aggregation.
    reference = pd.read_csv(source_artifacts / f"{TASK}_test_predictions.csv",
                            parse_dates=["decision_time"]).set_index("decision_time")
    if not reference.index.equals(features["test"].index):
        raise RuntimeError("Original frozen test decision times differ")
    for name in MODEL_COLUMNS:
        np.testing.assert_allclose(result[name]["test"], reference[name].to_numpy(),
                                   rtol=0, atol=1e-12,
                                   err_msg=f"Original frozen {name} model could not be reproduced")
    return result


def evaluate(source: Path = SOURCE, source_artifacts: Path = BASE_ARTIFACTS,
             output: Path = OUTPUT) -> dict:
    original = json.loads((source_artifacts / "evaluation.json").read_text())
    if original["script_sha256"] != sha256(BASE_SCRIPT):
        raise RuntimeError("Original aggregation script differs from the trained model record")
    for name, expected in original["source_sha256"].items():
        if sha256(source / name) != expected:
            raise RuntimeError(f"Source data changed: {name}")
    features, outcomes = selected_rows(source)
    for split, outcome in outcomes.items():
        expected = original["tasks"][TASK]["rows"][split]
        if (len(outcome), int(outcome.sum())) != (expected["n"], expected["positive"]):
            raise RuntimeError(f"Original {split} row count or outcomes changed")
    base_probability = model_predictions(features, source_artifacts)
    y_val, y_test = outcomes["validation"], outcomes["test"]
    val_index, test_index = features["validation"].index, features["test"].index
    joined_index = val_index.append(test_index)
    joined_y = np.concatenate([y_val, y_test])
    n_val = len(val_index)

    variants = {}
    validation_grid = {}
    test_frame = pd.DataFrame({"outcome": y_test}, index=test_index)
    validation_frame = pd.DataFrame({"outcome": y_val}, index=val_index)
    for model_name in MODEL_COLUMNS:
        val_p = base_probability[model_name]["validation"]
        test_p = base_probability[model_name]["test"]
        joined_p = np.concatenate([val_p, test_p])
        validation_frame[f"{model_name}_frozen"] = val_p

        # Fixed calibration choice, fitted on all validation outcomes only.
        static_offset = fit_intercept(val_p, y_val, ridge=1.0)
        static_test = shifted_probability(test_p, static_offset)

        # Every hyperparameter is selected by *prequential* validation Brier:
        # each validation prediction sees only earlier, matured validation labels.
        grid = []
        for window in WINDOWS_MINUTES:
            for ridge in RIDGE_STRENGTHS:
                prediction, _, _ = causal_rolling_intercept(
                    val_index, val_p, y_val, window_minutes=window, ridge=ridge
                )
                grid.append({"window_minutes": window, "ridge": ridge,
                             "validation_brier": score(y_val, prediction)["brier"],
                             "validation_log_loss": score(y_val, prediction)["log_loss"]})
        chosen = min(grid, key=lambda row: (row["validation_brier"], row["validation_log_loss"],
                                            row["window_minutes"], row["ridge"]))
        rolling_all, rolling_offsets, history_counts = causal_rolling_intercept(
            joined_index, joined_p, joined_y,
            window_minutes=chosen["window_minutes"], ridge=chosen["ridge"]
        )
        rolling_test = rolling_all[n_val:]
        validation_frame[f"{model_name}_rolling"] = rolling_all[:n_val]
        test_frame[f"{model_name}_frozen"] = test_p
        test_frame[f"{model_name}_static"] = static_test
        test_frame[f"{model_name}_rolling"] = rolling_test
        test_frame[f"{model_name}_rolling_offset"] = rolling_offsets[n_val:]
        test_frame[f"{model_name}_mature_history_rows"] = history_counts[n_val:]

        variants[model_name] = {
            "static_validation_intercept": static_offset,
            "rolling_chosen_on_prequential_validation": chosen,
            "rolling_test_mature_history_rows": {
                "minimum": int(history_counts[n_val:].min()),
                "median": float(np.median(history_counts[n_val:])),
                "maximum": int(history_counts[n_val:].max()),
            },
            "validation": {
                "frozen": score(y_val, val_p),
                "rolling_prequential": score(y_val, rolling_all[:n_val]),
            },
            "test": {
                method: {
                    **score(y_test, prediction),
                    **reliability_bins(y_test, prediction),
                }
                for method, prediction in (
                    ("frozen", test_p), ("static_validation_intercept", static_test),
                    ("causal_rolling_intercept", rolling_test),
                )
            },
        }
        validation_grid[model_name] = grid

    temporal = {}
    for label, mask in (
        ("14_to_20_utc", test_index < pd.Timestamp("2022-09-11 20:00:00")),
        ("20_to_24_utc", test_index >= pd.Timestamp("2022-09-11 20:00:00")),
    ):
        temporal[label] = {
            model: {method: score(y_test[mask], test_frame.loc[mask, f"{model}_{suffix}"].to_numpy())
                    for method, suffix in (("frozen", "frozen"),
                                           ("static_validation_intercept", "static"),
                                           ("causal_rolling_intercept", "rolling"))}
            for model in MODEL_COLUMNS
        }

    qps_roll = test_frame["qps_logistic_rolling"].to_numpy()
    paired_brier = {
        comparison: base.block_bootstrap_brier_difference(
            test_index, y_test, qps_roll, test_frame[comparison].to_numpy(),
            block_minutes=30, repetitions=2000,
        )
        for comparison in ("qps_logistic_frozen", "qps_logistic_static",
                           "qps_sample_count_logistic_frozen", "all_logistic_frozen",
                           "all_logistic_rolling")
    }
    result = {
        "dataset": "Alibaba GenTD26 one-day anonymized monitoring trace",
        "source_commit": "8c132fb7717fbd44cf434cf81f2cd865a80072d5",
        "source_model_sha256": sha256(source_artifacts / "models.joblib"),
        "source_evaluation_sha256": sha256(source_artifacts / "evaluation.json"),
        "source_script_sha256": sha256(BASE_SCRIPT),
        "script_sha256": sha256(Path(__file__)),
        "label_delay_minutes_from_index": LABEL_DELAY_MINUTES,
        "label_availability_rule": "row at s enters rolling history only when s + 6 minutes <= decision minute t",
        "selection": "Rolling window/ridge selected independently for each frozen model on prequential validation Brier only; static intercept fitted on complete validation only; 14:00+ test never used for selection",
        "split": {"validation": [str(val_index.min()), str(val_index.max())],
                  "test": [str(test_index.min()), str(test_index.max())]},
        "rows": {split: {"n": len(y), "positive": int(y.sum()),
                         "prevalence": float(np.mean(y))}
                 for split, y in outcomes.items()},
        "models": variants,
        "validation_grids": validation_grid,
        "test_by_period": temporal,
        "qps_rolling_minus_comparison_brier": paired_brier,
        "caveat": "One anonymized day and overlapping future windows; no scale-action or counterfactual labels, so predictions are not a learned SCALE_OUT policy",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    test_frame.rename_axis("decision_time").to_csv(output / "test_predictions.csv")
    validation_frame.rename_axis("decision_time").to_csv(output / "validation_predictions.csv")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--source-artifacts", type=Path, default=BASE_ARTIFACTS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.source, args.source_artifacts, args.output)
    print(json.dumps({name: {"selected": value["rolling_chosen_on_prequential_validation"],
                             "test": {method: {key: measure[key] for key in
                                               ("brier", "average_precision", "calibration_in_the_large")}
                                      for method, measure in value["test"].items()}}
                      for name, value in result["models"].items()}, indent=2))
