"""Frozen earlier-period replication of the BurstGPT rolling-bin p90 rule.

`prepare` fits on BurstGPT_1 days 0–59 and freezes the primary method before
`test` reads BurstGPT_2 outcomes. The trace has no verified feedback time,
infrastructure allocation, or deployment-action outcome.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data/external/burstgpt_v2_12"
SOURCE_1 = SOURCE_DIR / "BurstGPT_1.csv"
SOURCE_2 = SOURCE_DIR / "BurstGPT_2.csv"
PROTOCOL = ROOT / "reports/burstgpt_12_output_tokens_protocol.md"
OUTPUT = ROOT / "artifacts/burstgpt_12_output_tokens_v1"
PINNED = {
    "BurstGPT_1.csv": (52_283_111, "4bb3783693d0a435686fbfc885615d2349bd067239079fa4b749f2e679e12122", 1_429_737, (0, 60)),
    "BurstGPT_2.csv": (144_819_209, "44bf5942b03fca42c01a226a545ad3a750ec62688faecd67b6831c56a8928bf7", 3_858_436, (61, 120)),
}
COLUMNS = ("Timestamp", "Model", "Request tokens", "Response tokens", "Total tokens", "Log Type")
Q = 0.9
BIN_EDGES = (64, 256, 1024, 4096)
MIN_SUPPORT = 100
TRAIN_LAST_DAY = 59
TEST_FIRST_DAY = 61
TEST_LAST_DAY = 120
PRIMARY = "rolling_bin"
METHODS = ("rolling_bin", "rolling_cohort", "static_cohort", "static_global", "hgb_base")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_hashes(source_dir: Path = SOURCE_DIR) -> dict[str, str]:
    observed = {}
    for name, (size, expected, _, _) in PINNED.items():
        path = source_dir / name
        if path.stat().st_size != size:
            raise ValueError(f"Unexpected size for {path}")
        digest = sha256(path)
        if digest != expected:
            raise ValueError(f"Unexpected SHA256 for {path}")
        observed[name] = digest
    return observed


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def insist_absent(paths: list[Path]) -> None:
    found = [str(path) for path in paths if path.exists()]
    if found:
        raise FileExistsError(f"Frozen artifact exists; use a new version: {found}")


def eligible_request_mask(frame: pd.DataFrame) -> pd.Series:
    """Use request-known input only; do not condition on the output label."""
    return frame["Request tokens"] > 0


def load_file(path: Path) -> pd.DataFrame:
    name = path.name
    if name not in PINNED:
        raise ValueError(f"Unexpected source {name}")
    with path.open(newline="") as handle:
        if tuple(next(csv.reader(handle))) != COLUMNS:
            raise ValueError(f"Unexpected columns in {name}")
    frame = pd.read_csv(path, dtype={
        "Model": "category", "Log Type": "category",
        "Request tokens": "int64", "Response tokens": "int64", "Total tokens": "int64",
    })
    expected_n, expected_days = PINNED[name][2:]
    if len(frame) != expected_n:
        raise ValueError(f"Unexpected row count in {name}")
    ts = frame["Timestamp"].to_numpy(float)
    if not np.isfinite(ts).all() or np.any(np.diff(ts) < 0):
        raise ValueError(f"Invalid timestamp order in {name}")
    if frame.isna().any().any():
        raise ValueError(f"Missing values in {name}")
    if (frame[["Request tokens", "Response tokens", "Total tokens"]] < 0).any().any():
        raise ValueError(f"Negative tokens in {name}")
    if not (frame["Total tokens"] == frame["Request tokens"] + frame["Response tokens"]).all():
        raise ValueError(f"Token sum mismatch in {name}")
    frame["day"] = np.floor(ts / 86400.0).astype(np.int16)
    if (int(frame.day.min()), int(frame.day.max())) != expected_days:
        raise ValueError(f"Unexpected day range in {name}")
    frame = frame.loc[eligible_request_mask(frame)].copy()
    frame["file_row"] = frame.index.to_numpy(np.int64)
    frame["source_file"] = name
    frame["cohort"] = frame.Model.astype(str) + " | " + frame["Log Type"].astype(str)
    frame["input_bin"] = np.searchsorted(BIN_EDGES, frame["Request tokens"].to_numpy(np.int64), side="left").astype(np.int8)
    return frame


def empirical_p90(values: np.ndarray) -> float:
    if len(values) == 0:
        raise ValueError("Empty quantile group")
    return float(np.quantile(values, Q, method="higher"))


def feature_state(train: pd.DataFrame) -> dict:
    return {
        key: {str(value): i for i, value in enumerate(sorted(train[key].unique()))}
        for key in ("Model", "Log Type")
    }


def encode(frame: pd.DataFrame, state: dict) -> np.ndarray:
    model = frame.Model.astype("string").map(state["Model"]).fillna(-1).to_numpy(float)
    kind = frame["Log Type"].astype("string").map(state["Log Type"]).fillna(-1).to_numpy(float)
    length = np.log1p(frame["Request tokens"].to_numpy(float))
    return np.column_stack((model, kind, length))


def fit_fixed(train: pd.DataFrame) -> dict:
    if train.empty or train.day.min() != 0 or train.day.max() != TRAIN_LAST_DAY:
        raise ValueError("Fixed training data must span days 0–59")
    state = feature_state(train)
    estimator = HistGradientBoostingRegressor(
        loss="quantile", quantile=Q, categorical_features=[0, 1],
        max_iter=140, max_leaf_nodes=15, min_samples_leaf=60,
        l2_regularization=10, learning_rate=0.05,
        early_stopping=False, random_state=42,
    )
    estimator.fit(encode(train, state), train["Response tokens"].to_numpy(float))
    return {
        "estimator": estimator,
        "feature_state": state,
        "static_global_p90": empirical_p90(train["Response tokens"].to_numpy()),
        "static_cohort_p90": {
            str(c): empirical_p90(g["Response tokens"].to_numpy())
            for c, g in train.groupby("cohort", observed=True, sort=True)
        },
        "train_rows": int(len(train)),
        "train_days": [0, TRAIN_LAST_DAY],
    }


def history_window(day: int) -> tuple[int, int]:
    return day - 8, day - 2


def group_p90(frame: pd.DataFrame, by: str | list[str]) -> tuple[dict, dict]:
    result, count = {}, {}
    for key, group in frame.groupby(by, observed=True, sort=True):
        if isinstance(by, str) and isinstance(key, tuple):
            key = key[0]
        result[key] = empirical_p90(group["Response tokens"].to_numpy())
        count[key] = len(group)
    return result, count


def day_predictions(current: pd.DataFrame, history: pd.DataFrame, bundle: dict) -> dict[str, np.ndarray]:
    if history.empty:
        raise ValueError("Missing rolling history")
    cohort_q, _ = group_p90(history, "cohort")
    bin_q, bin_n = group_p90(history, ["cohort", "input_bin"])
    global_q = float(bundle["static_global_p90"])
    static_q = bundle["static_cohort_p90"]
    cohorts = current.cohort.to_numpy(str)
    bins = current.input_bin.to_numpy(np.int8)
    static_cohort = np.array([static_q.get(c, global_q) for c in cohorts], float)
    rolling_cohort = np.array([cohort_q.get(c, static_q.get(c, global_q)) for c in cohorts], float)
    rolling_bin = np.array([
        bin_q[(c, int(b))] if bin_n.get((c, int(b)), 0) >= MIN_SUPPORT
        else cohort_q.get(c, static_q.get(c, global_q))
        for c, b in zip(cohorts, bins)
    ], float)
    hgb = np.maximum(0.0, bundle["estimator"].predict(encode(current, bundle["feature_state"])))
    return {
        "rolling_bin": rolling_bin,
        "rolling_cohort": rolling_cohort,
        "static_cohort": static_cohort,
        "static_global": np.full(len(current), global_q, float),
        "hgb_base": hgb,
    }


def predict_all(eligible: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    day_map = {int(day): part for day, part in eligible.groupby("day", observed=True, sort=True)}
    parts = []
    for day in range(TEST_FIRST_DAY, TEST_LAST_DAY + 1):
        current = day_map.get(day)
        if current is None or current.empty:
            raise ValueError(f"Missing eligible test day {day}")
        low, high = history_window(day)
        history = pd.concat([day_map[j] for j in range(low, high + 1)], ignore_index=True)
        methods = day_predictions(current, history, bundle)
        out = current[[
            "source_file", "file_row", "Timestamp", "day", "Model", "Log Type",
            "cohort", "Request tokens", "input_bin", "Response tokens",
        ]].copy()
        for name in METHODS:
            out[name] = methods[name]
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


def score_arrays(actual: np.ndarray, prediction: np.ndarray) -> dict:
    error = actual - prediction
    loss = np.where(error >= 0.0, Q * error, (Q - 1.0) * error)
    return {
        "rows": int(len(actual)),
        "pinball_tokens": float(np.mean(loss)),
        "coverage": float(np.mean(actual <= prediction)),
        "mean_budget_tokens": float(np.mean(prediction)),
    }


def summarize(predictions: pd.DataFrame) -> dict:
    result = {}
    y = predictions["Response tokens"].to_numpy(float)
    for method in METHODS:
        p = predictions[method].to_numpy(float)
        row = score_arrays(y, p)
        days = {
            str(int(day)): score_arrays(part["Response tokens"].to_numpy(float), part[method].to_numpy(float))
            for day, part in predictions.groupby("day", observed=True, sort=True)
        }
        cohorts = {}
        for cohort, part in predictions.groupby("cohort", observed=True, sort=True):
            scored = score_arrays(part["Response tokens"].to_numpy(float), part[method].to_numpy(float))
            day_losses = [
                score_arrays(by_day["Response tokens"].to_numpy(float), by_day[method].to_numpy(float))["pinball_tokens"]
                for _, by_day in part.groupby("day", observed=True, sort=True)
            ]
            scored["equal_day_pinball_tokens"] = float(np.mean(day_losses))
            scored["days_present"] = len(day_losses)
            cohorts[str(cohort)] = scored
        blocks = {}
        for start, part in predictions.assign(
            block_start=TEST_FIRST_DAY + (predictions.day.to_numpy(int) - TEST_FIRST_DAY) // 10 * 10
        ).groupby("block_start", observed=True, sort=True):
            scored = score_arrays(part["Response tokens"].to_numpy(float), part[method].to_numpy(float))
            scored["equal_day_pinball_tokens"] = float(np.mean([
                days[str(day)]["pinball_tokens"] for day in range(int(start), int(start) + 10)
            ]))
            blocks[f"{start}-{start+9}"] = scored
        row["equal_day_pinball_tokens"] = float(np.mean([v["pinball_tokens"] for v in days.values()]))
        row["by_day"] = days
        row["by_cohort"] = cohorts
        row["by_10_day_block"] = blocks
        result[method] = row
    return result


def gates(scores: dict) -> dict:
    main = scores[PRIMARY]
    comparators = ("rolling_cohort", "static_cohort", "hgb_base")
    overall_loss = all(main["equal_day_pinball_tokens"] < scores[c]["equal_day_pinball_tokens"] for c in comparators)
    block_wins = sum(
        value["equal_day_pinball_tokens"]
        < scores["rolling_cohort"]["by_10_day_block"][block]["equal_day_pinball_tokens"]
        for block, value in main["by_10_day_block"].items()
    )
    cohort_wins = all(
        value["equal_day_pinball_tokens"]
        < scores["rolling_cohort"]["by_cohort"][cohort]["equal_day_pinball_tokens"]
        for cohort, value in main["by_cohort"].items()
    )
    coverage_pass = (
        main["coverage"] >= Q
        and all(v["coverage"] >= Q for v in main["by_10_day_block"].values())
        and all(v["coverage"] >= Q for v in main["by_cohort"].values())
    )
    return {
        "relative_loss_transfer": bool(overall_loss and block_wins >= 5 and cohort_wins),
        "relative_loss_components": {
            "equal_day_beats_all_three": bool(overall_loss),
            "ten_day_blocks_beating_rolling_cohort": int(block_wins),
            "cohorts_all_beating_rolling_cohort": bool(cohort_wins),
        },
        "safety_upper_bound": bool(coverage_pass),
    }


def prepare(source_dir: Path = SOURCE_DIR, output: Path = OUTPUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "model.joblib"
    fit_path = output / "fit_info.json"
    manifest_path = output / "selection_manifest.json"
    insist_absent([model_path, fit_path, manifest_path])
    asset_hashes = source_hashes(source_dir)
    train_all = load_file(source_dir / "BurstGPT_1.csv")
    train = train_all.loc[train_all.day <= TRAIN_LAST_DAY].copy()
    bundle = fit_fixed(train)
    joblib.dump(bundle, model_path)
    fit_info = {
        "status": "trained_without_test_outcomes",
        "eligible_file_1_rows": int(len(train_all)),
        "fixed_train_rows": bundle["train_rows"],
        "excluded_day_60_rows": int((train_all.day == 60).sum()),
        "static_global_p90": bundle["static_global_p90"],
        "static_cohort_p90": bundle["static_cohort_p90"],
        "software": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__},
    }
    write_json(fit_path, fit_info)
    manifest = {
        "status": "primary_method_frozen_before_test",
        "source_sha256": asset_hashes,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(model_path),
        "fit_info_sha256": sha256(fit_path),
        "primary_method": PRIMARY,
        "comparators": [method for method in METHODS if method != PRIMARY],
        "fixed_train_days": [0, TRAIN_LAST_DAY],
        "excluded_fixed_train_day": 60,
        "test_days": [TEST_FIRST_DAY, TEST_LAST_DAY],
        "feedback_rule": "for prediction day d, request days d-8 through d-2 are assumed delivered",
        "selection_rule": "rolling_bin fixed from BurstGPT_3 post-hoc hypothesis; no validation selection on files 1 or 2",
    }
    write_json(manifest_path, manifest)
    return {"manifest_sha256": sha256(manifest_path), "train_rows": bundle["train_rows"]}


def frozen_test(source_dir: Path = SOURCE_DIR, output: Path = OUTPUT) -> dict:
    model_path = output / "model.joblib"
    fit_path = output / "fit_info.json"
    manifest_path = output / "selection_manifest.json"
    prediction_path = output / "test_predictions.parquet"
    metrics_path = output / "test_metrics.json"
    insist_absent([prediction_path, metrics_path])
    if not all(path.exists() for path in (model_path, fit_path, manifest_path)):
        raise FileNotFoundError("Prepare and freeze the primary method before test")
    manifest = json.loads(manifest_path.read_text())
    current = {
        "source_sha256": source_hashes(source_dir),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(model_path),
        "fit_info_sha256": sha256(fit_path),
    }
    if any(manifest.get(key) != value for key, value in current.items()):
        raise ValueError("Frozen experiment hash mismatch")
    if manifest.get("status") != "primary_method_frozen_before_test" or manifest.get("primary_method") != PRIMARY:
        raise ValueError("Primary method is not frozen")
    bundle = joblib.load(model_path)
    part1 = load_file(source_dir / "BurstGPT_1.csv")
    part2 = load_file(source_dir / "BurstGPT_2.csv")
    if part1.Timestamp.max() >= part2.Timestamp.min():
        raise ValueError("Source periods overlap")
    eligible = pd.concat((part1, part2), ignore_index=True)
    predictions = predict_all(eligible, bundle)
    if len(predictions) != len(part2):
        raise ValueError("Test prediction count differs from eligible file 2")
    predictions.to_parquet(prediction_path, index=False)
    scores = summarize(predictions)
    metrics = {
        "status": "test",
        "primary_method": PRIMARY,
        "manifest_sha256": sha256(manifest_path),
        "test_days": [TEST_FIRST_DAY, TEST_LAST_DAY],
        "test_rows": int(len(predictions)),
        "test_predictions_sha256": sha256(prediction_path),
        "source_sha256": current["source_sha256"],
        "methods": scores,
        "predeclared_gates": gates(scores),
    }
    write_json(metrics_path, metrics)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "test"))
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = prepare(args.source_dir, args.output) if args.phase == "prepare" else frozen_test(args.source_dir, args.output)
    print(json.dumps({
        "phase": args.phase,
        "rows": result.get("train_rows", result.get("test_rows")),
        "manifest_sha256": result.get("manifest_sha256"),
        "primary_method": result.get("primary_method", PRIMARY),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
