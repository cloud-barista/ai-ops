"""One-, six-, and twelve-hour load forecasts on the public Alibaba MaaS app cases.

The released QPS is normalized separately per application. It is a demand
proxy, not an absolute request rate or a label for any scale action. At a
forecast origin, every feature uses observations from that origin or earlier.
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
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/alibaba_maas_2026"
OUTPUT = ROOT / "artifacts/maas_trace_v1"
HORIZONS = (1, 6, 12)
TRAIN_END = 96  # Relative hours [0, 96): four days.
VALID_END = 120  # Relative hours [96, 120): one day; test is the final two days.
RIDGE_ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0)
STATIC_BASELINES = ("last_value", "same_hour_previous_day", "last_24h_mean")
SOURCE_SHA256 = {
    "app_metrics.csv.zip": "c03d3a0cd81d1bd1060eee5035022ea071879817b794cc0c7bf2ad6659cfe927",
    "app_metrics.csv": "ac536b99cd23d4ada0ff85925627a3d71cce88d242fdb4c645612bb657f20496",
    "app_cases.json": "67a38e16e1d9af90d84a4f2b20c6e05e19c72f69fb9a5bfe763ec9e5b4adc304",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources(source: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, expected in SOURCE_SHA256.items():
        actual = sha256(source / name)
        if actual != expected:
            raise ValueError(f"{name}: SHA256 mismatch; expected {expected}, got {actual}")
        hashes[name] = actual
    return hashes


def load_qps(source: Path) -> tuple[pd.DataFrame, dict[str, str], dict]:
    raw = pd.read_csv(source / "app_metrics.csv", na_values=["\\N"])
    required = {"time_s", "app_id", "series_id", "metric", "value"}
    if not required.issubset(raw):
        raise ValueError(f"Missing columns: {sorted(required - set(raw))}")
    if raw.duplicated(["time_s", "app_id", "series_id", "metric"]).any():
        raise ValueError("Duplicate metric-series observation")
    qps = raw.loc[raw.metric.eq("qps_norm"), ["time_s", "app_id", "series_id", "value"]]
    if qps.empty or not np.isfinite(qps.value.to_numpy(float)).all():
        raise ValueError("Missing or non-finite normalized QPS")
    if (qps.value < 0).any():
        raise ValueError("Normalized QPS must be nonnegative")
    pivot = qps.groupby(["time_s", "app_id"], sort=True).value.sum().unstack("app_id")
    times = pivot.index.to_numpy(np.int64)
    if len(times) < VALID_END + 24 or not np.all(np.diff(times) == 3600):
        raise ValueError("Expected a contiguous hourly trace spanning the evaluation")
    if pivot.isna().any().any():
        raise ValueError("Missing hourly QPS for an application")
    if (pivot.max() > 1.0 + 1e-8).any():
        raise ValueError("App-level normalized QPS exceeds one")
    cases = json.loads((source / "app_cases.json").read_text())
    labels = {
        app["app_id"]: app["label"]
        for figure in cases["figures"].values()
        for app in figure["apps"]
    }
    diagnostics = {
        "raw_rows": int(len(raw)),
        "qps_rows": int(len(qps)),
        "hourly_timestamps": int(len(pivot)),
        "qps_apps": int(pivot.shape[1]),
        "raw_apps": int(raw.app_id.nunique()),
        "first_time_s": int(times.min()),
        "last_time_s": int(times.max()),
        "qps_series_per_app": qps.groupby("app_id").series_id.nunique().to_dict(),
        "last_48h_by_app": {},
    }
    gpu_counts = raw.loc[raw.metric.eq("gpu_count")].groupby(["app_id", "time_s"]).value.sum()
    for app in pivot.columns:
        test_qps = pivot[app].iloc[VALID_END:]
        gpu = gpu_counts.loc[app] if app in gpu_counts.index.get_level_values("app_id") else None
        diagnostics["last_48h_by_app"][app] = {
            "qps_zero_hours": int(test_qps.eq(0).sum()),
            "test_hours": int(len(test_qps)),
            "gpu_count_observed_hours": int(gpu.reindex(test_qps.index).notna().sum()) if gpu is not None else 0,
            "test_gpu_count_mean": float(gpu.reindex(test_qps.index).mean()) if gpu is not None else None,
        }
    return pivot, labels, diagnostics


def build_examples(qps: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Use QPS only through decision_idx = target_idx - horizon."""
    if not 0 < horizon < 24:
        raise ValueError("This daily-seasonal design requires 1 <= horizon < 24")
    rows: list[dict] = []
    apps = list(qps.columns)
    for app_i, app in enumerate(apps):
        values = qps[app].to_numpy(float)
        # Three same-hour-relative observations from yesterday need index t-26.
        for target in range(26 + horizon, len(values)):
            decision = target - horizon
            recent = float(values[decision - 2:decision + 1].mean())
            previous = float(values[decision - 26:decision - 23].mean())
            yesterday_target = float(values[target - 24])
            relative_level = float(np.clip((recent + 0.01) / (previous + 0.01), 0.0, 2.0))
            row = {
                "app_id": app,
                "target_idx": target,
                "decision_idx": decision,
                "target_time_s": int(qps.index[target]),
                "observed_qps_norm": float(values[target]),
                "last_value": float(values[decision]),
                "same_hour_previous_day": yesterday_target,
                "last_24h_mean": float(values[decision - 23:decision + 1].mean()),
                "level_corrected_seasonal": float(np.clip(yesterday_target * relative_level, 0, 1)),
                "level_ratio": relative_level,
                "recent_3h_mean": recent,
                "previous_day_3h_mean": previous,
                "target_hour_sin": float(np.sin(2 * np.pi * target / 24)),
                "target_hour_cos": float(np.cos(2 * np.pi * target / 24)),
                "rolling_24h": float(values[decision - 23:decision + 1].mean()),
            }
            for lag in (0, 1, 2, 3, 6, 23, 24):
                row[f"lag_{lag}"] = float(values[decision - lag])
            for i in range(len(apps)):
                row[f"app_{i}"] = float(i == app_i)
            rows.append(row)
    return pd.DataFrame(rows)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    return [
        col for col in frame
        if (
            col.startswith("lag_")
            or (col.startswith("app_") and col != "app_id")
            or col in ("recent_3h_mean", "rolling_24h", "target_hour_sin", "target_hour_cos")
        )
    ]


def mae(observed: pd.Series | np.ndarray, predicted: pd.Series | np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(observed) - np.asarray(predicted))))


def select_static_by_app(validation: pd.DataFrame) -> dict[str, str]:
    """Choose each app's static baseline using validation labels only."""
    selected: dict[str, str] = {}
    for app, group in validation.groupby("app_id", sort=True):
        selected[app] = min(
            STATIC_BASELINES,
            key=lambda name: (mae(group.observed_qps_norm, group[name]), STATIC_BASELINES.index(name)),
        )
    return selected


def fit_ridge(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[object, float, float]:
    columns = feature_columns(train)
    best: tuple[float, float, object] | None = None
    for alpha in RIDGE_ALPHAS:
        model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
        model.fit(train[columns], train.observed_qps_norm)
        prediction = np.clip(model.predict(validation[columns]), 0, 1)
        result = (mae(validation.observed_qps_norm, prediction), alpha, model)
        if best is None or result[:2] < best[:2]:
            best = result
    assert best is not None
    return best[2], best[1], best[0]


def summarize(frame: pd.DataFrame, predictions: list[str]) -> list[dict]:
    output: list[dict] = []
    groups = [("all_apps", frame),
              ("positive_qps_hours", frame.loc[frame.observed_qps_norm.gt(0)])]
    groups += [(app, group) for app, group in frame.groupby("app_id")]
    for app, group in groups:
        y = group.observed_qps_norm.to_numpy(float)
        for name in predictions:
            p = group[name].to_numpy(float)
            output.append({
                "app_id": app,
                "method": name,
                "n": int(len(group)),
                "mae": float(np.abs(y - p).mean()),
                "rmse": float(np.sqrt(np.mean((y - p) ** 2))),
                "bias_pred_minus_obs": float(np.mean(p - y)),
            })
    return output


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Start selection/evaluation only after previous labels are observable."""
    train = frame.loc[frame.target_idx < TRAIN_END]
    validation = frame.loc[(frame.decision_idx >= TRAIN_END) &
                           (frame.target_idx < VALID_END)]
    test = frame.loc[frame.decision_idx >= VALID_END].copy()
    if min(len(train), len(validation), len(test)) < 20:
        raise ValueError("Insufficient observations in a chronological split")
    if (int(train.target_idx.max()) >= TRAIN_END
            or int(validation.decision_idx.min()) < TRAIN_END
            or int(test.decision_idx.min()) < VALID_END):
        raise AssertionError("A forecast predates the preceding split's labels")
    return train, validation, test


def evaluate(qps: pd.DataFrame, labels: dict[str, str], diagnostics: dict, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    all_predictions = []
    all_methods = list(STATIC_BASELINES) + [
        "level_corrected_seasonal", "ridge", "validation_selected_static_by_app"
    ]
    for horizon in HORIZONS:
        frame = build_examples(qps, horizon)
        train, validation, test = chronological_split(frame)
        selected = select_static_by_app(validation)
        model, alpha, validation_mae = fit_ridge(train, validation)
        test["ridge"] = np.clip(model.predict(test[feature_columns(frame)]), 0, 1)
        test["validation_selected_static_by_app"] = [
            test.loc[idx, selected[app]] for idx, app in test.app_id.items()
        ]
        test["horizon_hours"] = horizon
        test["app_label"] = test.app_id.map(labels)
        all_predictions.append(test[
            ["app_id", "app_label", "target_time_s", "target_idx", "decision_idx",
             "horizon_hours", "observed_qps_norm"] + all_methods
        ])
        joblib.dump(model, output / f"ridge_h{horizon}.joblib")
        results.append({
            "horizon_hours": horizon,
            "split_rows": {
                "train": int(len(train)), "validation": int(len(validation)), "test": int(len(test))
            },
            "ridge_alpha_chosen_on_validation": alpha,
            "ridge_validation_mae": validation_mae,
            "static_baseline_chosen_by_app": selected,
            "validation_mae": {
                name: mae(validation.observed_qps_norm, validation[name])
                for name in list(STATIC_BASELINES) + ["level_corrected_seasonal"]
            },
            "test_scores": summarize(test, all_methods),
        })
    predictions = pd.concat(all_predictions, ignore_index=True)
    predictions.to_parquet(output / "test_predictions.parquet", index=False)
    metadata = {
        "experiment": "observational_app_normalized_qps_forecast",
        "source_commit": "8a6295f9779ddc019add669eeca49c5f9f77b3a7",
        "source_sha256": verify_sources(SOURCE),
        "evaluation_script_sha256": sha256(Path(__file__).resolve()),
        "sklearn_version": sklearn.__version__,
        "train_end_hour_exclusive": TRAIN_END,
        "validation_end_hour_exclusive": VALID_END,
        "diagnostics": diagnostics,
        "results": results,
        "limits": [
            "Normalized QPS, not absolute requests per second",
            "Only three application cases and seven days",
            "No scale-action times, counterfactual VM response, or user-specified SLO",
            "The level-corrected method was explored on this same trace; test scores are hypothesis-generating",
        ],
    }
    (output / "metrics.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.source.resolve() != SOURCE.resolve():
        raise ValueError("Only the pinned public release is supported by this command")
    verify_sources(args.source)
    qps, labels, diagnostics = load_qps(args.source)
    report = evaluate(qps, labels, diagnostics, args.output)
    for item in report["results"]:
        values = {row["method"]: row["mae"] for row in item["test_scores"] if row["app_id"] == "all_apps"}
        print(f"h={item['horizon_hours']} test MAE: {values}")


if __name__ == "__main__":
    main()
