"""Forecast the next *recorded* GenTD26 pipeline-update latency quantile.

The log has no update command/start time. This is an observational metric
forecast conditional on a pipeline-update record, not an UPDATE action model.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/gentd26"
OUTPUT = ROOT / "artifacts/gentd26_update_latency_v1"
T0 = 1662858777.0
TRAIN_END = T0 + 8 * 3600
VALID_END = T0 + 12 * 3600
LAG_SECONDS = 300
STATE_MAX_AGE_SECONDS = 600
Q = 0.9
STATE_FILES = {
    "pod_gpu_duty_cycle_anon.csv": "gpu_duty",
    "pod_gpu_memory_used_bytes_anon.csv": "gpu_memory_gb",
    "pod_memory_util_anon.csv": "pod_memory_util",
}
HISTORY_FEATURES = (
    "global_n1", "global_median1", "global_q901",
    "global_n4", "global_median4", "global_q904",
    "ip_n4", "ip_median4", "ip_q904", "ip_last", "ip_age_seconds",
)
STATE_FEATURES = (
    "gpu_duty", "gpu_memory_gb", "pod_memory_util",
    "gpu_duty_age_seconds", "gpu_memory_gb_age_seconds",
    "pod_memory_util_age_seconds",
)
MODEL_KWARGS = dict(
    loss="quantile", quantile=Q, max_iter=120, max_leaf_nodes=15,
    min_samples_leaf=50, learning_rate=0.05, l2_regularization=10,
    random_state=42,
)
BASELINE_NAMES = (
    "train_fixed", "global_1h", "global_4h", "ip_4h_k20", "ip_4h_k100",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_updates(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"timestamp_anon", "value", "container_ip"}
    if not required.issubset(frame):
        raise ValueError(f"{path}: missing {sorted(required - set(frame))}")
    frame = frame.rename(columns={"timestamp_anon": "t", "value": "y_ms", "container_ip": "ip"})
    frame = frame[["t", "y_ms", "ip"]].copy()
    if (frame.ip.isna().any() or not np.isfinite(frame.t).all()
            or not np.isfinite(frame.y_ms).all() or frame.y_ms.lt(0).any()):
        raise ValueError("Invalid update timestamp, container, or latency")
    if frame.duplicated(["ip", "t"]).any():
        raise ValueError("Repeated container/timestamp update key")
    return frame.sort_values(["t", "ip"], kind="stable").reset_index(drop=True)


def _summary(values: list[float] | np.ndarray) -> tuple[float, float]:
    if len(values) == 0:
        return np.nan, np.nan
    return float(np.median(values)), float(np.quantile(values, Q))


def past_update_features(updates: pd.DataFrame) -> pd.DataFrame:
    """Only use labels timestamped at least five minutes before each row."""
    if not updates.t.is_monotonic_increasing:
        raise ValueError("Update rows must be ordered by timestamp")
    times = updates.t.to_numpy(float)
    values = updates.y_ms.to_numpy(float)
    ips = updates.ip.to_numpy(str)
    n = len(updates)
    result = {name: np.full(n, np.nan) for name in HISTORY_FEATURES}
    global_history: deque[tuple[float, float]] = deque()
    ip_histories: dict[str, deque[tuple[float, float]]] = defaultdict(deque)
    available = 0
    start = 0
    while start < n:
        t = times[start]
        stop = int(np.searchsorted(times, t, side="right"))
        cutoff = t - LAG_SECONDS
        while available < n and times[available] <= cutoff:
            event = (times[available], values[available])
            global_history.append(event)
            ip_histories[ips[available]].append(event)
            available += 1
        while global_history and global_history[0][0] < cutoff - 4 * 3600:
            global_history.popleft()
        g4_values = [value for _, value in global_history]
        g1_values = [value for time, value in global_history if time >= cutoff - 3600]
        g1_med, g1_q90 = _summary(g1_values)
        g4_med, g4_q90 = _summary(g4_values)
        for j in range(start, stop):
            own = ip_histories[ips[j]]
            while own and own[0][0] < cutoff - 4 * 3600:
                own.popleft()
            own_values = [value for _, value in own]
            own_med, own_q90 = _summary(own_values)
            result["global_n1"][j] = len(g1_values)
            result["global_median1"][j] = g1_med
            result["global_q901"][j] = g1_q90
            result["global_n4"][j] = len(g4_values)
            result["global_median4"][j] = g4_med
            result["global_q904"][j] = g4_q90
            result["ip_n4"][j] = len(own_values)
            result["ip_median4"][j] = own_med
            result["ip_q904"][j] = own_q90
            result["ip_last"][j] = own[-1][1] if own else np.nan
            result["ip_age_seconds"][j] = t - own[-1][0] if own else np.nan
        start = stop
    return pd.DataFrame(result)


def add_preobserved_state(
    features: pd.DataFrame, updates: pd.DataFrame, source: Path,
) -> pd.DataFrame:
    """As-of join at t−5min; old and missing readings remain missing."""
    if len(features) != len(updates):
        raise ValueError("Feature and update row counts differ")
    out = features.copy()
    left = pd.DataFrame({
        "row": np.arange(len(updates)), "cutoff": updates.t.to_numpy() - LAG_SECONDS,
        "ip": updates.ip.to_numpy(),
    }).sort_values("cutoff", kind="stable")
    for filename, name in STATE_FILES.items():
        state = pd.read_csv(source / filename)
        required = {"timestamp_anon", "value", "container_ip"}
        if not required.issubset(state):
            raise ValueError(f"{filename}: missing state columns")
        state = state.rename(columns={
            "timestamp_anon": "obs_t", "value": name, "container_ip": "ip",
        })[["obs_t", name, "ip"]]
        state = state.loc[
            state.ip.notna() & np.isfinite(state.obs_t) & np.isfinite(state[name])
        ].groupby(["ip", "obs_t"], as_index=False)[name].mean()
        if name == "gpu_memory_gb":
            state[name] = state[name] / 1e9
        joined = pd.merge_asof(
            left, state.sort_values("obs_t", kind="stable"),
            left_on="cutoff", right_on="obs_t", by="ip", direction="backward",
            tolerance=STATE_MAX_AGE_SECONDS,
        ).sort_values("row")
        out[name] = joined[name].to_numpy(float)
        out[name + "_age_seconds"] = (
            joined.cutoff.to_numpy(float) - joined.obs_t.to_numpy(float)
        )
    return out


def baseline_predictions(features: pd.DataFrame, fixed_q90: float) -> dict[str, np.ndarray]:
    n = len(features)
    fixed = np.full(n, fixed_q90, dtype=float)
    g1 = features.global_q901.fillna(fixed_q90).to_numpy(float)
    g4 = features.global_q904.fillna(fixed_q90).to_numpy(float)
    own_values = features.ip_q904.to_numpy(float)
    own = np.where(np.isfinite(own_values), own_values, g4)
    count = features.ip_n4.to_numpy(float)
    return {
        "train_fixed": fixed,
        "global_1h": g1,
        "global_4h": g4,
        "ip_4h_k20": (count * own + 20 * g4) / (count + 20),
        "ip_4h_k100": (count * own + 100 * g4) / (count + 100),
    }


def split_masks(updates: pd.DataFrame) -> dict[str, np.ndarray]:
    t = updates.t.to_numpy(float)
    if abs(float(t.min()) - T0) > 0.001:
        raise ValueError("Source start changed; the frozen split is invalid")
    return {
        "train": t < TRAIN_END,
        "train_fit": t <= TRAIN_END - LAG_SECONDS,
        "validation": (t >= TRAIN_END) & (t < VALID_END),
        "validation_selection": (t >= TRAIN_END) & (t <= VALID_END - LAG_SECONDS),
        "test": t >= VALID_END,
        "test_early": (t >= VALID_END) & (t < VALID_END + 4 * 3600),
        "test_late": t >= VALID_END + 4 * 3600,
    }


def score(y: np.ndarray, pred: np.ndarray) -> dict:
    if len(y) == 0 or len(y) != len(pred) or not np.isfinite(pred).all():
        raise ValueError("Invalid score input")
    return {
        "n": int(len(y)),
        "pinball_ms": float(mean_pinball_loss(y, pred, alpha=Q)),
        "coverage": float(np.mean(y <= pred)),
        "mean_prediction_ms": float(np.mean(pred)),
        "observed_p90_ms": float(np.quantile(y, Q)),
    }


def state_coverage(features: pd.DataFrame, mask: np.ndarray) -> dict[str, float]:
    return {name: float(features.loc[mask, name].notna().mean())
            for name in STATE_FILES.values()}


def _source_hashes(data: Path) -> dict[str, str]:
    names = ["pipeline_update_latency_anon.csv", *STATE_FILES]
    return {name: sha256(data / name) for name in names}


def _model(features: tuple[str, ...], train: pd.DataFrame, y: np.ndarray):
    model = HistGradientBoostingRegressor(**MODEL_KWARGS)
    model.fit(train.loc[:, list(features)], y)
    return model


def select(data: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    updates = load_updates(data / "pipeline_update_latency_anon.csv")
    masks = split_masks(updates)
    # Do not build history features from test labels before model selection.
    before_test = updates.loc[~masks["test"]].reset_index(drop=True)
    before_masks = split_masks(before_test)
    history = past_update_features(before_test)
    features = add_preobserved_state(history, before_test, data)
    # Coverage checks inspect only test timestamps, IPs, and telemetry.
    test_metadata = updates.loc[masks["test"], ["t", "ip"]].reset_index(drop=True)
    test_state = add_preobserved_state(
        pd.DataFrame(index=range(len(test_metadata))), test_metadata, data
    )
    coverage = state_coverage(test_state, np.ones(len(test_state), dtype=bool))
    dry_run = {
        "source_hashes": _source_hashes(data),
        "source_rows": len(updates),
        "train_rows": int(masks["train"].sum()),
        "train_fit_rows": int(before_masks["train_fit"].sum()),
        "validation_rows": int(before_masks["validation"].sum()),
        "validation_selection_rows": int(before_masks["validation_selection"].sum()),
        "test_rows": int(masks["test"].sum()),
        "test_early_rows": int(masks["test_early"].sum()),
        "test_late_rows": int(masks["test_late"].sum()),
        "test_state_coverage": coverage,
        "contains_test_target_scores": False,
    }
    (output / "dry_run.json").write_text(json.dumps(dry_run, indent=2) + "\n")
    if dry_run["test_rows"] < 1000 or min(coverage.values()) < 0.95:
        raise ValueError("Frozen data-quality gate failed; do not score test")
    fit_mask = before_masks["train_fit"]
    val_mask = before_masks["validation_selection"]
    y = before_test.y_ms.to_numpy(float)
    fixed_q90 = float(np.quantile(y[fit_mask], Q))
    baselines = baseline_predictions(features, fixed_q90)
    val_scores = {name: score(y[val_mask], preds[val_mask])
                  for name, preds in baselines.items()}
    chosen = min(BASELINE_NAMES, key=lambda name: val_scores[name]["pinball_ms"])
    history_model = _model(HISTORY_FEATURES, features.loc[fit_mask], y[fit_mask])
    state_model = _model(HISTORY_FEATURES + STATE_FEATURES, features.loc[fit_mask], y[fit_mask])
    model_paths = {
        "history": output / "history_model.joblib",
        "state": output / "state_model.joblib",
    }
    joblib.dump(history_model, model_paths["history"])
    joblib.dump(state_model, model_paths["state"])
    manifest = {
        "protocol": "reports/gentd26_update_latency_protocol.md",
        "protocol_sha256": sha256(ROOT / "reports/gentd26_update_latency_protocol.md"),
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": dry_run["source_hashes"],
        "dry_run_sha256": sha256(output / "dry_run.json"),
        "sklearn_version": sklearn.__version__,
        "t0": T0, "train_end": TRAIN_END, "test_start": VALID_END,
        "lag_seconds": LAG_SECONDS, "q": Q,
        "train_fit_rows": int(fit_mask.sum()),
        "validation_selection_rows": int(val_mask.sum()),
        "fixed_train_q90_ms": fixed_q90,
        "validation_baselines": val_scores,
        "chosen_baseline": chosen,
        "validation_models": {
            "history": score(
                y[val_mask], np.maximum(0, history_model.predict(
                    features.loc[val_mask, list(HISTORY_FEATURES)]))
            ),
            "state": score(
                y[val_mask], np.maximum(0, state_model.predict(
                    features.loc[val_mask, list(HISTORY_FEATURES + STATE_FEATURES)]))
            ),
        },
        "model_sha256": {name: sha256(path) for name, path in model_paths.items()},
        "features": {"history": HISTORY_FEATURES, "state": STATE_FEATURES},
        "model_kwargs": MODEL_KWARGS,
        "contains_test_target_scores": False,
    }
    (output / "selection_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _bootstrap_blocks(t: np.ndarray, first_loss: np.ndarray,
                      second_loss: np.ndarray, repeats: int = 2000) -> dict:
    blocks = np.floor((t - VALID_END) / 1800).astype(int)
    groups = [np.flatnonzero(blocks == block) for block in np.unique(blocks)]
    delta = first_loss - second_loss
    rng = np.random.default_rng(43)
    draws = np.empty(repeats)
    for index in range(repeats):
        picks = rng.integers(0, len(groups), size=len(groups))
        ids = np.concatenate([groups[j] for j in picks])
        draws[index] = float(delta[ids].mean())
    return {
        "point_difference_ms": float(delta.mean()),
        "interval_95_within_day_ms": np.quantile(draws, [0.025, 0.975]).tolist(),
        "block_minutes": 30, "blocks": len(groups), "repeats": repeats,
    }


def _pinball_each(y: np.ndarray, pred: np.ndarray) -> np.ndarray:
    d = y - pred
    return np.maximum(Q * d, (Q - 1) * d)


def score_test(data: Path, output: Path) -> dict:
    manifest = json.loads((output / "selection_manifest.json").read_text())
    if sha256(Path(__file__)) != manifest["script_sha256"]:
        raise ValueError("Scoring script changed after selection")
    if sha256(ROOT / manifest["protocol"]) != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed after selection")
    if _source_hashes(data) != manifest["source_hashes"]:
        raise ValueError("Source files changed after selection")
    if sha256(output / "dry_run.json") != manifest["dry_run_sha256"]:
        raise ValueError("Dry-run audit changed after selection")
    for name in ("history", "state"):
        if sha256(output / f"{name}_model.joblib") != manifest["model_sha256"][name]:
            raise ValueError(f"{name} model changed after selection")
    updates = load_updates(data / "pipeline_update_latency_anon.csv")
    masks = split_masks(updates)
    features = add_preobserved_state(past_update_features(updates), updates, data)
    dry_run = json.loads((output / "dry_run.json").read_text())
    if int(masks["test"].sum()) != dry_run["test_rows"]:
        raise ValueError("Test support changed")
    if state_coverage(features, masks["test"]) != dry_run["test_state_coverage"]:
        raise ValueError("Test-state coverage changed")
    test = updates.loc[masks["test"]].reset_index(drop=True)
    test_features = features.loc[masks["test"]].reset_index(drop=True)
    y = test.y_ms.to_numpy(float)
    preds = baseline_predictions(test_features, manifest["fixed_train_q90_ms"])
    history_model = joblib.load(output / "history_model.joblib")
    state_model = joblib.load(output / "state_model.joblib")
    preds["history"] = np.maximum(0, history_model.predict(test_features.loc[:, list(HISTORY_FEATURES)]))
    preds["state"] = np.maximum(0, state_model.predict(test_features.loc[:, list(HISTORY_FEATURES + STATE_FEATURES)]))
    chosen = manifest["chosen_baseline"]
    slices = {
        "all": np.ones(len(test), dtype=bool),
        "early": test.t.to_numpy() < VALID_END + 4 * 3600,
        "late": test.t.to_numpy() >= VALID_END + 4 * 3600,
    }
    by_slice = {
        label: {method: score(y[mask], values[mask]) for method, values in preds.items()}
        for label, mask in slices.items()
    }
    baseline_score = by_slice["all"][chosen]
    state_score = by_slice["all"]["state"]
    history_score = by_slice["all"]["history"]
    improvements = {
        "state_vs_selected_baseline_relative_pinball": (
            baseline_score["pinball_ms"] - state_score["pinball_ms"]
        ) / baseline_score["pinball_ms"],
        "state_vs_history_relative_pinball": (
            history_score["pinball_ms"] - state_score["pinball_ms"]
        ) / history_score["pinball_ms"],
    }
    gates = {
        "baseline_pinball_5pct": improvements["state_vs_selected_baseline_relative_pinball"] >= 0.05,
        "history_pinball_2pct": improvements["state_vs_history_relative_pinball"] >= 0.02,
        "overall_coverage_88pct": state_score["coverage"] >= 0.88,
        "each_half_coverage_85pct": all(by_slice[s]["state"]["coverage"] >= 0.85
                                         for s in ("early", "late")),
        "both_halves_pinball_win": all(
            by_slice[s]["state"]["pinball_ms"] < by_slice[s][chosen]["pinball_ms"]
            for s in ("early", "late")
        ),
    }
    block = _bootstrap_blocks(
        test.t.to_numpy(float), _pinball_each(y, preds["state"]),
        _pinball_each(y, preds[chosen]),
    )
    predictions = test.copy()
    for method, values in preds.items():
        predictions[method + "_p90_ms"] = values
    predictions.to_parquet(output / "test_predictions.parquet", index=False)
    results = {
        "selection_manifest_sha256": sha256(output / "selection_manifest.json"),
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": manifest["source_hashes"],
        "target": "next recorded pipeline update latency, ms, including zero values",
        "chosen_baseline": chosen,
        "by_slice": by_slice,
        "improvements": improvements,
        "gates": gates,
        "adopt_for_research_decision": all(gates.values()),
        "block_bootstrap_state_minus_baseline": block,
        "limitations": [
            "One anonymized serving trace day; update command and request start times are absent",
            "No model/version identity, user SLO, VM action, or causal update benefit",
            "Online feedback availability is assumed with a fixed five-minute embargo",
        ],
    }
    (output / "test_metrics.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("select", "score"), required=True)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = select(args.data, args.output) if args.stage == "select" else score_test(args.data, args.output)
    if args.stage == "select":
        print(json.dumps({
            "train_fit_rows": result["train_fit_rows"],
            "validation_selection_rows": result["validation_selection_rows"],
            "chosen_baseline": result["chosen_baseline"],
            "validation_baselines": result["validation_baselines"],
            "validation_models": result["validation_models"],
        }, indent=2))
    else:
        print(json.dumps({
            "chosen_baseline": result["chosen_baseline"],
            "by_slice": result["by_slice"],
            "improvements": result["improvements"],
            "gates": result["gates"],
            "adopt_for_research_decision": result["adopt_for_research_decision"],
        }, indent=2))
