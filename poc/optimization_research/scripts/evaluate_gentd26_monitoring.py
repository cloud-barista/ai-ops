"""Chronological, observational queue-risk forecasting on the GenTD26 trace.

Each row represents a decision at the end of a minute. Features use that
minute and earlier observations; labels refer to the following five minutes.
There are no scale-action labels or counterfactual outcomes in this trace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/gentd26"
OUTPUT = ROOT / "artifacts/gentd26_monitoring_v1"
SOURCES = (
    "qps.csv",
    "queue_size_raw_anon.csv",
    "queue_rt_raw_anon.csv",
    "pod_gpu_duty_cycle_anon.csv",
    "pod_gpu_memory_used_bytes_anon.csv",
    "pipeline_inference_data_anon.csv",
)
TRAIN_END = pd.Timestamp("2022-09-11 10:00:00")
VALID_END = pd.Timestamp("2022-09-11 14:00:00")
HORIZON_MIN = 5
RT_THRESHOLD_MS = 400.0  # Research proxy only; the trace defines no user SLO.
MIN_FUTURE_RT_SAMPLES = 10


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_metric(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"timestamp_anon", "value"}
    if not required.issubset(frame):
        raise ValueError(f"{path.name}: missing {sorted(required - set(frame))}")
    frame = frame.loc[
        np.isfinite(frame.timestamp_anon) & np.isfinite(frame.value)
    ].copy()
    frame["minute"] = pd.to_datetime(frame.timestamp_anon, unit="s").dt.floor("min")
    return frame


def aggregate_features(source: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    queue = load_metric(source / "queue_size_raw_anon.csv")
    index = pd.date_range(queue.minute.min(), queue.minute.max(), freq="min")
    result = pd.DataFrame(index=index)

    queue_group = queue.groupby("minute").value
    result["queue_size"] = queue_group.max().reindex(index)
    result["queue_observed"] = queue_group.size().reindex(index).fillna(0).astype(float)

    qps = load_metric(source / "qps.csv")
    # Zero here means no QPS sample was recorded in a minute, not verified
    # zero true demand. Keep the sample count so the model sees this ambiguity.
    for req_type, name in (("Generative Requests", "generative"), ("API Requests", "api")):
        group = qps.loc[qps.request_type.eq(req_type)].groupby("minute").value
        result[f"{name}_qps_sum"] = group.sum().reindex(index).fillna(0)
        result[f"{name}_qps_n"] = group.size().reindex(index).fillna(0)

    rt = load_metric(source / "queue_rt_raw_anon.csv")
    rt_group = rt.groupby("minute").value
    result["queue_rt_median"] = rt_group.median().reindex(index)
    result["queue_rt_p90"] = rt_group.quantile(0.9).reindex(index)
    result["queue_rt_n"] = rt_group.size().reindex(index).fillna(0)

    gpu = load_metric(source / "pod_gpu_duty_cycle_anon.csv")
    gpu_group = gpu.groupby("minute").value
    result["gpu_duty_mean"] = gpu_group.mean().reindex(index)
    result["gpu_duty_p90"] = gpu_group.quantile(0.9).reindex(index)
    result["gpu_pod_n"] = gpu.groupby("minute").container_ip.nunique().reindex(index).fillna(0)

    gpu_mem = load_metric(source / "pod_gpu_memory_used_bytes_anon.csv")
    result["gpu_mem_mean_gb"] = (
        gpu_mem.groupby("minute").value.mean().reindex(index) / 1e9
    )

    pipeline = load_metric(source / "pipeline_inference_data_anon.csv")
    pipe_group = pipeline.groupby("minute").value
    result["pipeline_rt_p90"] = pipe_group.quantile(0.9).reindex(index)
    result["pipeline_rt_n"] = pipe_group.size().reindex(index).fillna(0)

    for key in ("queue_size", "generative_qps_sum", "generative_qps_n",
                "queue_rt_median", "gpu_duty_mean", "pipeline_rt_p90"):
        for width in (5, 15):
            result[f"{key}_past{width}_mean"] = result[key].rolling(width, min_periods=1).mean()
    result["queue_positive_past5"] = result.queue_size.gt(0).rolling(5, min_periods=1).sum()
    result["queue_positive_past15"] = result.queue_size.gt(0).rolling(15, min_periods=1).sum()

    # A zero queue label requires a queue monitor observation in every future
    # minute; absent monitoring must not silently become an all-clear outcome.
    observed = result.queue_observed.gt(0)
    queue_positive = result.queue_size.gt(0)
    future_observed = np.logical_and.reduce(
        [observed.shift(-lag, fill_value=False).to_numpy() for lag in range(1, HORIZON_MIN + 1)]
    )
    future_queue_positive = np.logical_or.reduce(
        [queue_positive.shift(-lag, fill_value=False).to_numpy() for lag in range(1, HORIZON_MIN + 1)]
    )
    labels = pd.DataFrame(index=index)
    labels["queue_future_observed"] = future_observed
    labels["queue_future_positive"] = future_queue_positive.astype(int)

    # Pooled future request samples, not per-minute p90s: sparse late periods
    # are evaluated only if the next five minutes contain enough observations.
    minute_values = rt.groupby("minute").value.apply(lambda x: x.to_numpy(float)).to_dict()
    future_rt_n = np.zeros(len(index), dtype=int)
    future_rt_p90 = np.full(len(index), np.nan)
    for i in range(len(index) - HORIZON_MIN):
        values = [minute_values[index[i + lag]] for lag in range(1, HORIZON_MIN + 1)
                  if index[i + lag] in minute_values]
        if values:
            pooled = np.concatenate(values)
            future_rt_n[i] = len(pooled)
            future_rt_p90[i] = float(np.quantile(pooled, 0.9))
    labels["future_rt_n"] = future_rt_n
    labels["future_rt_p90"] = future_rt_p90
    labels["rt_future_observed"] = future_rt_n >= MIN_FUTURE_RT_SAMPLES
    labels["rt_future_high"] = (future_rt_p90 > RT_THRESHOLD_MS).astype(int)
    return result, labels


def chronological_masks(index: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    """Purge rows whose entire target window crosses a split boundary."""
    future_end = index + pd.Timedelta(minutes=HORIZON_MIN + 1)
    return {
        "train": np.asarray(future_end <= TRAIN_END),
        "validation": np.asarray((index >= TRAIN_END) & (future_end <= VALID_END)),
        "test": np.asarray((index >= VALID_END) & (future_end <= index.max())),
    }


def score(y: np.ndarray, probability: np.ndarray) -> dict:
    probability = np.clip(probability, 1e-6, 1 - 1e-6)
    return {
        "n": int(len(y)),
        "positive": int(y.sum()),
        "prevalence": float(np.mean(y)),
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
        "average_precision": float(average_precision_score(y, probability)) if y.sum() else None,
        "roc_auc": float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else None,
        "mean_predicted_risk": float(np.mean(probability)),
    }


def block_bootstrap_brier_difference(
    index: pd.DatetimeIndex, y: np.ndarray, first: np.ndarray, second: np.ndarray,
    *, block_minutes: int = 30, repetitions: int = 2000,
) -> dict:
    """Within-trace uncertainty for Brier(first) - Brier(second)."""
    blocks = index.floor(f"{block_minutes}min")
    groups = [np.flatnonzero(blocks == key) for key in blocks.unique()]
    error_difference = (y - first) ** 2 - (y - second) ** 2
    rng = np.random.default_rng(42)
    draws = np.empty(repetitions)
    for i in range(repetitions):
        picked = rng.integers(0, len(groups), size=len(groups))
        positions = np.concatenate([groups[j] for j in picked])
        draws[i] = float(error_difference[positions].mean())
    return {
        "point_difference": float(error_difference.mean()),
        "interval_95_within_trace": np.quantile(draws, [0.025, 0.975]).tolist(),
        "block_minutes": block_minutes,
        "n_blocks": len(groups),
        "caveat": "Resamples one calendar day only; does not measure between-day uncertainty",
    }


def conditional_persistence(train_y: np.ndarray, train_recent: np.ndarray, future_recent: np.ndarray) -> np.ndarray:
    """Laplace-smoothed train rates conditional on recent queue activity."""
    prior = (train_y.sum() + 1.0) / (len(train_y) + 2.0)
    output = np.empty(len(future_recent), dtype=float)
    for value in (False, True):
        selected = train_recent == value
        rate = (train_y[selected].sum() + 2.0 * prior) / (selected.sum() + 2.0)
        output[future_recent == value] = rate
    return output


def evaluate_task(
    features: pd.DataFrame, labels: pd.DataFrame, masks: dict[str, np.ndarray],
    *, target: str, observed: str,
) -> tuple[dict, object, pd.DataFrame]:
    feature_columns = list(features.columns)
    selected = {name: masks[name] & labels[observed].to_numpy(bool) for name in masks}
    x = {name: features.loc[mask, feature_columns] for name, mask in selected.items()}
    y = {name: labels.loc[mask, target].to_numpy(int) for name, mask in selected.items()}
    if min(map(len, y.values())) < 20 or min(map(np.sum, y.values())) == 0:
        raise ValueError(f"{target}: inadequate positives or rows in a chronological split")
    recent = {name: x[name].queue_positive_past5.to_numpy() > 0 for name in x}
    prior = (y["train"].sum() + 1) / (len(y["train"]) + 2)
    candidates: dict[str, dict[str, np.ndarray]] = {
        "train_prior": {name: np.full(len(y[name]), prior) for name in ("validation", "test")},
        "recent_queue_persistence": {
            name: conditional_persistence(y["train"], recent["train"], recent[name])
            for name in ("validation", "test")
        },
    }
    models = {
        "queue_history_logistic": (
            make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                          LogisticRegression(C=0.1, max_iter=1000)),
            ["queue_size", "queue_size_past5_mean", "queue_size_past15_mean",
             "queue_positive_past5", "queue_positive_past15", "queue_observed"],
        ),
        "qps_sample_count_logistic": (
            make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                          LogisticRegression(C=0.1, max_iter=1000)),
            ["generative_qps_n", "generative_qps_n_past5_mean", "generative_qps_n_past15_mean",
             "api_qps_n"],
        ),
        "qps_logistic": (
            make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                          LogisticRegression(C=0.1, max_iter=1000)),
            ["generative_qps_sum", "generative_qps_sum_past5_mean", "generative_qps_sum_past15_mean",
             "generative_qps_n", "api_qps_sum"],
        ),
        "all_logistic": (
            make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                          LogisticRegression(C=0.1, max_iter=1000)),
            feature_columns,
        ),
        "all_hgb": (
            HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=7,
                                           min_samples_leaf=30, l2_regularization=10,
                                           learning_rate=0.04, random_state=42),
            feature_columns,
        ),
    }
    for name, (model, columns) in models.items():
        model.fit(x["train"][columns], y["train"])
        candidates[name] = {
            split: model.predict_proba(x[split][columns])[:, 1]
            for split in ("validation", "test")
        }
    results = {
        "rows": {name: {"n": len(y[name]), "positive": int(y[name].sum())} for name in y},
        "feature_columns": feature_columns,
        "validation": {name: score(y["validation"], values["validation"]) for name, values in candidates.items()},
        "test": {name: score(y["test"], values["test"]) for name, values in candidates.items()},
        "test_events_by_hour": {
            str(hour): {"n": int(len(values)), "positive": int(values.sum())}
            for hour, values in pd.Series(y["test"], index=x["test"].index).groupby(
                x["test"].index.floor("h")
            )
        },
    }
    chosen = min(candidates, key=lambda name: results["validation"][name]["brier"])
    results["chosen_on_validation_brier"] = chosen
    results["selected_vs_recent_queue_persistence"] = block_bootstrap_brier_difference(
        x["test"].index, y["test"], candidates[chosen]["test"],
        candidates["recent_queue_persistence"]["test"],
    )
    results["selected_vs_qps_sample_count"] = block_bootstrap_brier_difference(
        x["test"].index, y["test"], candidates[chosen]["test"],
        candidates["qps_sample_count_logistic"]["test"],
    )
    results["active_test_until_20h"] = {
        name: score(y["test"][x["test"].index < pd.Timestamp("2022-09-11 20:00:00")],
                    values["test"][x["test"].index < pd.Timestamp("2022-09-11 20:00:00")])
        for name, values in candidates.items()
    }
    predictions = pd.DataFrame({"outcome": y["test"]}, index=x["test"].index)
    for name, value in candidates.items():
        predictions[name] = value["test"]
    return results, {name: model for name, (model, _) in models.items()}, predictions


def evaluate(source: Path = DATA, output: Path = OUTPUT) -> dict:
    features, labels = aggregate_features(source)
    masks = chronological_masks(features.index)
    tasks = {}
    fitted = {}
    predictions = {}
    for name, target, observed in (
        ("future_queue_positive", "queue_future_positive", "queue_future_observed"),
        ("future_queue_rt_p90_over_400ms", "rt_future_high", "rt_future_observed"),
    ):
        tasks[name], fitted[name], predictions[name] = evaluate_task(
            features, labels, masks, target=target, observed=observed
        )
    result = {
        "dataset": "Alibaba GenTD26 monitoring trace",
        "source_commit": "8c132fb7717fbd44cf434cf81f2cd865a80072d5",
        "source_sha256": {name: sha256(source / name) for name in SOURCES},
        "script_sha256": sha256(Path(__file__)),
        "sklearn_version": sklearn.__version__,
        "sample_unit": "end of one-minute bin; only same or earlier observations used",
        "time_span": {"start": str(features.index.min()), "end": str(features.index.max())},
        "split": {"train_end_exclusive": str(TRAIN_END), "validation_end_exclusive": str(VALID_END),
                  "future_window_minutes": HORIZON_MIN, "boundary_purge": "entire label window before next split"},
        "target_caveat": "Observational queue/latency-risk proxies only; no scaling interventions, replica counts, or causal scale-need labels",
        "latency_threshold_ms": RT_THRESHOLD_MS,
        "minimum_future_queue_rt_samples": MIN_FUTURE_RT_SAMPLES,
        "queue_monitor_missing_minutes": int(features.queue_observed.eq(0).sum()),
        "tasks": tasks,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    joblib.dump(fitted, output / "models.joblib")
    for name, values in predictions.items():
        values.rename_axis("decision_time").to_csv(output / f"{name}_test_predictions.csv")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    summary = evaluate(args.source, args.output)
    print(json.dumps({name: {"rows": task["rows"],
                             "chosen_on_validation_brier": task["chosen_on_validation_brier"],
                             "test": task["test"]}
                      for name, task in summary["tasks"].items()}, indent=2))
