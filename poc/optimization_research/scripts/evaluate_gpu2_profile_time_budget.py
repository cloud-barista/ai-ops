"""Frozen offline GPU1 -> GPU2 *profiling time* budget experiment.

The label is a local static profiling envelope, not VM readiness or scale action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_pinball_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
PROTOCOL = ROOT / "reports/gpu2_profile_time_budget_protocol_v1.md"
SNAPSHOT = ROOT / "data/audit_v1/measurements.parquet"
RAW_DB = Path("/path/to/aiops/runs/khu-hf-local-exhaustive/executions.sqlite3")
OUT = ROOT / "artifacts/gpu2_profile_time_budget_v1"
FREEZE = OUT / "freeze.json"
SNAPSHOT_SHA = "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906"
CUT = pd.Timestamp("2026-09-11T13:00:00Z")
KEY = ("model_id", "model_revision", "workload_group", "cpu_core_limit")
SAME = ("batch_size", "max_length", "request_concurrency", "repeats", "dataset",
        "cpu_threads", "workload_task", "input_encoding", "device_name", "profile_name")
FEATURES = ("log1p_e1", "log1p_wall1", "log1p_load1", "log1p_compute1",
            "log1p_parameter_mib", "log_batch", "log_length", "log1p_concurrency",
            "log_repeats", "log_cores")
METHODS = ("donor_median_delta", "donor_p90_delta", "donor_p90_ratio",
           "ridge_p90_residual", "hgb_p90_delta")
P90_METHODS = ("donor_p90_delta", "donor_p90_ratio", "ridge_p90_residual")
HGB = dict(loss="quantile", quantile=0.9, max_iter=100, learning_rate=0.05,
           max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=10.0,
           early_stopping=False, random_state=42)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def positive(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int, np.floating, np.integer)):
        raise ValueError(f"{label}: expected numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label}: expected positive finite")
    return number


def elapsed(start: object, end: object) -> float:
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if first.tzinfo is None or last.tzinfo is None:
        raise ValueError("timestamps must include UTC offset")
    return positive((last - first).total_seconds(), "elapsed")


def pair_record(one: dict, two: dict) -> dict:
    """Read GPU2 only for pairing and the elapsed-time target."""
    if any(one[k] != two[k] for k in KEY + SAME):
        raise ValueError("GPU1/GPU2 profile mismatch")
    if (one["campaign"] != "khu-hf-local-exhaustive" or two["campaign"] != one["campaign"]
            or one["host"] != "icns" or two["host"] != "icns"
            or one["device"] != "cuda" or two["device"] != "cuda"
            or one["replica_count"] != 1 or two["replica_count"] != 2):
        raise ValueError("unsupported source profile")
    if json.loads(one["gpu_indices_json"]) != [0] or json.loads(two["gpu_indices_json"]) != [0, 1]:
        raise ValueError("unexpected physical GPU indices")
    start1, end1 = pd.Timestamp(one["started_at"]), pd.Timestamp(one["finished_at"])
    start2, end2 = pd.Timestamp(two["started_at"]), pd.Timestamp(two["finished_at"])
    if end1 >= start2:
        raise ValueError("GPU1 profile must finish before GPU2 begins")
    e1 = elapsed(start1, end1)
    e2 = elapsed(start2, end2)
    result1 = json.loads(one["result_json"])
    if not isinstance(result1, dict):
        raise ValueError("GPU1 result_json must be object")
    parameter_mib = positive(result1.get("model_parameter_bytes"), "GPU1 parameter bytes") / 2**20
    record = {
        **{k: one[k] for k in KEY},
        "workload_task": one["workload_task"],
        "gpu1_execution_id": one["execution_id"],
        "gpu2_execution_id": two["execution_id"],
        "gpu1_started_at": one["started_at"],
        "gpu1_finished_at": one["finished_at"],
        "gpu2_started_at": two["started_at"],
        "gpu2_finished_at": two["finished_at"],
        "e1": e1,
        "e2": e2,
        "wall1": positive(one["wall_seconds"], "GPU1 wall_seconds"),
        "load1": positive(one["load_seconds"], "GPU1 load_seconds"),
        "compute1": positive(one["compute_seconds"], "GPU1 compute_seconds"),
        "parameter_mib": parameter_mib,
        **{k: positive(one[k], k) for k in
           ("batch_size", "max_length", "request_concurrency", "repeats", "cpu_core_limit")},
    }
    return record


def extract_pairs(snapshot: pd.DataFrame) -> pd.DataFrame:
    rows = snapshot.loc[(snapshot.campaign == "khu-hf-local-exhaustive") &
                        snapshot.eligible & (snapshot.success == 1) &
                        (snapshot.host == "icns") & (snapshot.device == "cuda") &
                        snapshot.replica_count.isin([1, 2])]
    groups: dict[tuple, dict[int, dict]] = {}
    for row in rows.to_dict("records"):
        key = tuple(row[k] for k in KEY)
        candidate = groups.setdefault(key, {})
        replica = int(row["replica_count"])
        if replica in candidate:
            raise ValueError("duplicate replica in profile pair")
        candidate[replica] = row
    records = []
    for key in sorted(groups):
        if set(groups[key]) != {1, 2}:
            raise ValueError("incomplete profile pair")
        records.append(pair_record(groups[key][1], groups[key][2]))
    pairs = pd.DataFrame(records)
    if len(pairs) != 2300 or len(pairs[pairs.workload_task == "inference"]) != 2048:
        raise ValueError("unexpected physical pair count")
    qwen = pairs[pairs.workload_task == "causal_lm_forward"]
    if len(qwen) != 252 or qwen.model_id.nunique() != 1:
        raise ValueError("unexpected causal LM pair count")
    return pairs


def split(pairs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = pairs[(pairs.workload_task == "inference") &
                  (pd.to_datetime(pairs.gpu2_finished_at, utc=True, format="mixed") <= CUT)].copy()
    test = pairs[(pairs.workload_task == "inference") &
                 (pd.to_datetime(pairs.gpu1_started_at, utc=True, format="mixed") >= CUT)].copy()
    abstain = pairs[(pairs.workload_task == "causal_lm_forward") &
                    (pd.to_datetime(pairs.gpu1_started_at, utc=True, format="mixed") >= CUT)].copy()
    if (len(train), len(test), len(abstain)) != (1044, 1004, 252):
        raise ValueError("unexpected temporal split support")
    if train.model_id.nunique() != 8 or test.model_id.nunique() != 8:
        raise ValueError("expected eight encoder models")
    return train, test, abstain


def feature_matrix(rows: pd.DataFrame) -> pd.DataFrame:
    raw = rows[["e1", "wall1", "load1", "compute1", "parameter_mib", "batch_size",
                "max_length", "request_concurrency", "repeats", "cpu_core_limit"]].to_numpy(dtype=float)
    if not np.isfinite(raw).all() or (raw <= 0).any():
        raise ValueError("features must be positive finite")
    values = np.column_stack((np.log1p(raw[:, :5]), np.log(raw[:, 5]),
                              np.log(raw[:, 6]), np.log1p(raw[:, 7]),
                              np.log(raw[:, 8]), np.log(raw[:, 9])))
    return pd.DataFrame(values, index=rows.index, columns=FEATURES)


def predict_fold(donor: pd.DataFrame, target: pd.DataFrame) -> dict[str, np.ndarray]:
    if set(donor.model_id) & set(target.model_id):
        raise ValueError("held-out model leaked into training")
    if donor.model_id.nunique() != 7 or target.model_id.nunique() != 1:
        raise ValueError("one held model and seven donor models required")
    if (pd.to_datetime(donor.gpu2_finished_at, utc=True, format="mixed") > CUT).any():
        raise ValueError("future donor label in training")
    if (pd.to_datetime(target.gpu1_started_at, utc=True, format="mixed") < CUT).any():
        raise ValueError("pre-cut target in test")
    delta = donor.e2.to_numpy(float) - donor.e1.to_numpy(float)
    donor_e1 = donor.e1.to_numpy(float)
    target_e1 = target.e1.to_numpy(float)
    x_train, x_test = feature_matrix(donor), feature_matrix(target)
    ridge = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    ridge.fit(x_train, delta)
    ridge_p90 = ridge.predict(x_test) + float(np.quantile(delta - ridge.predict(x_train), 0.9))
    hgb = HistGradientBoostingRegressor(**HGB)
    hgb.fit(x_train, delta)
    preds = {
        "donor_median_delta": target_e1 + float(np.median(delta)),
        "donor_p90_delta": target_e1 + float(np.quantile(delta, 0.9)),
        "donor_p90_ratio": target_e1 * float(np.quantile(donor.e2.to_numpy(float) / donor_e1, 0.9)),
        "ridge_p90_residual": target_e1 + ridge_p90,
        "hgb_p90_delta": target_e1 + hgb.predict(x_test),
    }
    return {name: np.maximum(0.001, np.asarray(pred, dtype=float)) for name, pred in preds.items()}


def score_predictions(predictions: pd.DataFrame) -> dict:
    per_model = []
    for model, group in predictions.groupby("model_id", sort=True):
        for name in METHODS:
            actual, estimated = group.e2.to_numpy(float), group[name].to_numpy(float)
            per_model.append({"model_id": model, "method": name, "n": len(group),
                              "pinball_seconds": float(mean_pinball_loss(actual, estimated, alpha=0.9)),
                              "coverage": float(np.mean(actual <= estimated)),
                              "overbudget_seconds": float(np.mean(np.maximum(estimated - actual, 0))),
                              "approved_5s": int(np.sum(estimated <= 5.0)),
                              "false_feasible_5s": int(np.sum((estimated <= 5.0) & (actual > 5.0)))})
    pm = pd.DataFrame(per_model)
    summary = {}
    for name in METHODS:
        group = pm[pm.method == name]
        rows = predictions
        actual, estimated = rows.e2.to_numpy(float), rows[name].to_numpy(float)
        summary[name] = {"model_macro_pinball_seconds": float(group.pinball_seconds.mean()),
                         "pooled_coverage": float(np.mean(actual <= estimated)),
                         "pooled_overbudget_seconds": float(np.mean(np.maximum(estimated - actual, 0))),
                         "approved_5s": int(np.sum(estimated <= 5.0)),
                         "false_feasible_5s": int(np.sum((estimated <= 5.0) & (actual > 5.0)))}
    strongest = min(P90_METHODS, key=lambda m: summary[m]["model_macro_pinball_seconds"])
    base, new = summary[strongest], summary["hgb_p90_delta"]
    b_pm = pm[pm.method == strongest].set_index("model_id").pinball_seconds
    n_pm = pm[pm.method == "hgb_p90_delta"].set_index("model_id").pinball_seconds
    improvements = n_pm < b_pm
    max_ratio = float((n_pm / b_pm).max())
    gates = {
        "pinball_5pct_better": new["model_macro_pinball_seconds"] <= 0.95 * base["model_macro_pinball_seconds"],
        "coverage_at_least_88pct": new["pooled_coverage"] >= 0.88,
        "at_least_six_models_better": int(improvements.sum()) >= 6,
        "each_model_within_20pct": max_ratio <= 1.20,
        "false_feasible_at_most_four": new["false_feasible_5s"] <= 4,
    }
    return {"methods": summary, "per_model": per_model, "strongest_p90_baseline": strongest,
            "models_better": int(improvements.sum()), "worst_model_loss_ratio": max_ratio,
            "adoption_gates": gates, "adopt": all(gates.values())}


def inputs() -> dict:
    paths = {"snapshot": SNAPSHOT, "raw_sqlite": RAW_DB, "protocol": PROTOCOL, "script": SCRIPT}
    if sha256(SNAPSHOT) != SNAPSHOT_SHA:
        raise ValueError("frozen extraction SHA changed")
    return {name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()}


def freeze() -> None:
    if FREEZE.exists():
        raise FileExistsError(f"freeze already exists: {FREEZE}")
    sources = inputs()
    with sqlite3.connect(f"file:{RAW_DB}?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        count = con.execute("SELECT COUNT(*) FROM execution_measurements").fetchone()[0]
    if count != 4856:
        raise ValueError("physical source row count changed")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"experiment": "gpu2_profile_time_budget_v1", "inputs": sources,
                "raw_execution_rows": count, "cutoff_utc": CUT.isoformat(),
                "model": HGB, "features": FEATURES, "methods": METHODS}
    FREEZE.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(FREEZE)


def score() -> None:
    manifest = json.loads(FREEZE.read_text())
    if manifest["inputs"] != inputs() or manifest["model"] != HGB or tuple(manifest["features"]) != FEATURES:
        raise ValueError("frozen inputs or model changed")
    pairs = extract_pairs(pd.read_parquet(SNAPSHOT))
    train, test, abstain = split(pairs)
    predictions = []
    folds = []
    for held in sorted(test.model_id.unique()):
        donor = train[train.model_id != held]
        target = test[test.model_id == held]
        result = predict_fold(donor, target)
        rows = target[["model_id", "model_revision", "workload_group", "cpu_core_limit",
                       "gpu1_execution_id", "gpu2_execution_id", "gpu1_started_at", "e1", "e2"]].copy()
        for name in METHODS:
            rows[name] = result[name]
        predictions.append(rows)
        folds.append({"held_model_id": held, "donor_models": sorted(donor.model_id.unique().tolist()),
                      "train_rows": len(donor), "test_rows": len(target)})
    scored = pd.concat(predictions, ignore_index=True).sort_values(
        ["model_id", "workload_group", "cpu_core_limit"])
    if len(scored) != 1004 or scored.gpu1_execution_id.duplicated().any():
        raise ValueError("unexpected scored row count")
    result = score_predictions(scored)
    pred_path = OUT / "predictions.csv"
    scored.to_csv(pred_path, index=False, float_format="%.12g")
    metrics = {"experiment": "gpu2_profile_time_budget_v1", "scope": "local_gpu2_profile_time_after_gpu1",
               "freeze_sha256": sha256(FREEZE), "prediction_sha256": sha256(pred_path),
               "library_versions": {"python": platform.python_version(), "numpy": np.__version__,
                                    "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
               "support": {"pairs": len(pairs), "encoder_pairs": len(train) + len(test),
                           "train_pool": len(train), "test_encoder": len(test),
                           "qwen_abstained": len(abstain)}, "folds": folds, **result}
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"adopt": result["adopt"], "strongest_p90_baseline": result["strongest_p90_baseline"],
                      "methods": result["methods"], "adoption_gates": result["adoption_gates"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "score"))
    args = parser.parse_args()
    freeze() if args.stage == "freeze" else score()
