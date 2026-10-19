#!/usr/bin/env python3
"""Frozen A100x4→x8 TTFT/TPOT component study on AgentPerfBench.

The score-before protocol is reports/agentperf_component_pair_protocol.md.
Only the A100x4 anchor is a prediction feature; A100x8 outcomes are labels or
post-evaluation sensitivity metadata. This is a CPU-only benchmark experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/agentperfbench/trace_replay/summary.parquet"
PROTOCOL = ROOT / "reports/agentperf_component_pair_protocol.md"
OUTPUT = ROOT / "artifacts/agentperf_component_pair_v1"
SOURCE_SHA256 = "568efbdae473a830ba792317fb09cfba0ea4f667db67b93d28e72637a5c71d1e"
PROTOCOL_SHA256 = "a984a30ae398f5c6652875076d38128a9f014339d760c600ec01e1223a93f216"
KEYS = ["model", "engine", "profile", "concurrency", "num_requests"]
ANCHOR = "A100-40GBx4"
TARGET = "A100-40GBx8"
MODEL_META = {
    "Llama-3.1-8B": (8.0, 8.0, 0),
    "Llama-3.1-70B": (70.0, 70.0, 0),
    "Mixtral-8x7B": (46.7, 12.9, 0),
    "Qwen3.5-9B": (9.0, 9.0, 0),
    "gpt-oss-20b": (21.0, 3.6, 1),
}
METRICS = ("ttft", "tpot")
NUMERIC_FEATURES = [
    "size_total_b", "size_active_b", "is_mxfp4", "concurrency", "num_requests",
    "anchor_log_ttft", "anchor_log_tpot", "anchor_log_input_tokens", "anchor_log_output_tokens",
]
CATEGORICAL_FEATURES = ["model_family", "profile"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
METHODS = (
    "anchor_copy", "half_gpu", "donor_global", "donor_profile", "direct_ridge",
    "direct_et", "ratio_ridge", "ratio_et", "phase_coupled",
)
LARGE_LOG = float(np.log(1.05))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_frozen(source: Path, protocol: Path) -> None:
    for path, expected in [(source, SOURCE_SHA256), (protocol, PROTOCOL_SHA256)]:
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"frozen SHA-256 mismatch for {path}: {actual} != {expected}")


def build_pairs(raw: pd.DataFrame) -> pd.DataFrame:
    needed = set(KEYS + [
        "run_id", "model_family", "hardware", "tensor_parallelism", "duration_s",
        "request_throughput", "input_token_throughput", "output_token_throughput",
        "p90_ttft_ms", "p90_tpot_ms", "p90_e2el_ms",
    ])
    if needed - set(raw.columns):
        raise ValueError(f"missing columns: {sorted(needed - set(raw.columns))}")
    if raw.run_id.duplicated().any() or raw.duplicated(KEYS + ["hardware"]).any():
        raise ValueError("duplicate run_id or context/hardware")
    selected = raw.loc[raw.hardware.isin([ANCHOR, TARGET])].copy()
    metrics = [
        "num_requests", "duration_s", "request_throughput", "input_token_throughput",
        "output_token_throughput", "p90_ttft_ms", "p90_tpot_ms", "p90_e2el_ms",
    ]
    vals = selected[metrics].astype(float).to_numpy()
    positive = np.isfinite(vals).all(axis=1) & (vals > 0).all(axis=1)
    completed = np.abs(
        selected.request_throughput.astype(float).to_numpy()
        * selected.duration_s.astype(float).to_numpy()
        - selected.num_requests.astype(float).to_numpy()
    ) <= 1e-5
    selected = selected.loc[positive & completed].copy()
    expected_tp = selected.hardware.map({ANCHOR: 4, TARGET: 8})
    if not np.array_equal(selected.tensor_parallelism.to_numpy(), expected_tp.to_numpy()):
        raise ValueError("tensor parallelism does not match GPU count")
    a = selected.loc[selected.hardware.eq(ANCHOR)].copy()
    b = selected.loc[selected.hardware.eq(TARGET)].copy()
    if a.duplicated(KEYS).any() or b.duplicated(KEYS).any():
        raise ValueError("duplicate anchor or target context")
    pairs = a.merge(b, on=KEYS, suffixes=("_a", "_b"), validate="one_to_one", sort=True)
    if len(pairs) != 130 or set(pairs.model) != set(MODEL_META) or set(pairs.engine) != {"vllm"}:
        raise ValueError("frozen pair support changed")
    if not (pairs.model_family_a == pairs.model_family_b).all():
        raise ValueError("model family mismatch")
    input_a = pairs.input_token_throughput_a * pairs.duration_s_a
    input_b = pairs.input_token_throughput_b * pairs.duration_s_b
    input_ratio = np.maximum(input_a / input_b, input_b / input_a)
    if not np.isfinite(input_ratio).all() or (input_ratio > 1.00001).any():
        raise ValueError("paired input-token totals differ")
    output_a = pairs.output_token_throughput_a * pairs.duration_s_a
    output_b = pairs.output_token_throughput_b * pairs.duration_s_b
    output_ratio = np.maximum(output_a / output_b, output_b / output_a)

    frame = pairs[KEYS].copy()
    frame["model_family"] = pairs.model_family_a.astype(str)
    sizes = np.array([MODEL_META[str(model)] for model in frame.model], dtype=float)
    frame[["size_total_b", "size_active_b", "is_mxfp4"]] = sizes
    frame["anchor_log_ttft"] = np.log(pairs.p90_ttft_ms_a.astype(float))
    frame["anchor_log_tpot"] = np.log(pairs.p90_tpot_ms_a.astype(float))
    frame["anchor_log_input_tokens"] = np.log(
        pairs.input_token_throughput_a.astype(float) / pairs.request_throughput_a.astype(float)
    )
    frame["anchor_log_output_tokens"] = np.log(
        pairs.output_token_throughput_a.astype(float) / pairs.request_throughput_a.astype(float)
    )
    for metric in METRICS:
        frame[f"target_log_{metric}"] = np.log(pairs[f"p90_{metric}_ms_b"].astype(float))
        frame[f"delta_{metric}"] = frame[f"target_log_{metric}"] - frame[f"anchor_log_{metric}"]
    frame["output_token_ratio"] = output_ratio.astype(float)
    if not np.isfinite(frame[NUMERIC_FEATURES + [
        "target_log_ttft", "target_log_tpot", "output_token_ratio",
    ]].to_numpy(dtype=float)).all():
        raise ValueError("nonfinite paired value")
    frame = frame.sort_values(KEYS, kind="mergesort").reset_index(drop=True)
    if int((frame.output_token_ratio <= 1.05).sum()) != 127:
        raise ValueError("5% sensitivity support changed")
    if int((frame.output_token_ratio <= 1.01).sum()) != 83:
        raise ValueError("1% sensitivity support changed")
    return frame


def _matrix(train: pd.DataFrame, test: pd.DataFrame, scaled: bool) -> tuple[np.ndarray, np.ndarray]:
    transform = ColumnTransformer([
        ("num", StandardScaler() if scaled else "passthrough", NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
    ], sparse_threshold=0)
    x_train = transform.fit_transform(train[FEATURES])
    x_test = transform.transform(test[FEATURES])
    return np.asarray(x_train, dtype=float), np.asarray(x_test, dtype=float)


def _donor(train: pd.DataFrame, test: pd.DataFrame, metric: str, by_profile: bool) -> np.ndarray:
    global_by_model = train.groupby("model")[f"delta_{metric}"].median()
    global_value = float(global_by_model.median())
    values = np.full(len(test), global_value, dtype=float)
    if by_profile:
        per_model_profile = train.groupby(["profile", "model"])[f"delta_{metric}"].median()
        for i, profile in enumerate(test.profile):
            if profile in per_model_profile.index.get_level_values("profile"):
                part = per_model_profile.loc[profile]
                if len(part) >= 3:
                    values[i] = float(part.median())
    return test[f"anchor_log_{metric}"].to_numpy(dtype=float) + values


def _ridge(x: np.ndarray, y: np.ndarray, x_test: np.ndarray, alpha: float = 10.0) -> np.ndarray:
    return Ridge(alpha=alpha, fit_intercept=True).fit(x, y).predict(x_test)


def _et(x: np.ndarray, y: np.ndarray, x_test: np.ndarray) -> np.ndarray:
    model = ExtraTreesRegressor(
        n_estimators=200, min_samples_leaf=5, max_features=1.0,
        random_state=42, n_jobs=1,
    )
    return model.fit(x, y).predict(x_test)


def predict_fold(train: pd.DataFrame, test: pd.DataFrame, heldout: str) -> pd.DataFrame:
    if train.empty or test.empty or set(train.model) & set(test.model):
        raise ValueError("empty or leaked model fold")
    x_ridge, z_ridge = _matrix(train, test, scaled=True)
    x_et, z_et = _matrix(train, test, scaled=False)
    result = test[KEYS + [
        "model_family", "anchor_log_ttft", "anchor_log_tpot", "target_log_ttft",
        "target_log_tpot", "delta_ttft", "delta_tpot", "output_token_ratio",
    ]].copy().reset_index(drop=True)
    result["heldout"] = heldout
    for metric in METRICS:
        anchor = test[f"anchor_log_{metric}"].to_numpy(dtype=float)
        target = train[f"target_log_{metric}"].to_numpy(dtype=float)
        delta = train[f"delta_{metric}"].to_numpy(dtype=float)
        result[f"pred_{metric}__anchor_copy"] = anchor
        result[f"pred_{metric}__half_gpu"] = anchor - np.log(2.0)
        result[f"pred_{metric}__donor_global"] = _donor(train, test, metric, False)
        result[f"pred_{metric}__donor_profile"] = _donor(train, test, metric, True)
        result[f"pred_{metric}__direct_ridge"] = _ridge(x_ridge, target, z_ridge)
        result[f"pred_{metric}__direct_et"] = _et(x_et, target, z_et)
        result[f"pred_{metric}__ratio_ridge"] = anchor + _ridge(x_ridge, delta, z_ridge)
        result[f"pred_{metric}__ratio_et"] = anchor + _et(x_et, delta, z_et)
    common = (train.delta_ttft.to_numpy(dtype=float) + train.delta_tpot.to_numpy(dtype=float)) / 2
    split = (train.delta_ttft.to_numpy(dtype=float) - train.delta_tpot.to_numpy(dtype=float)) / 2
    pred_common = _ridge(x_ridge, common, z_ridge, alpha=10.0)
    pred_split = _ridge(x_ridge, split, z_ridge, alpha=100.0)
    result["pred_ttft__phase_coupled"] = test.anchor_log_ttft.to_numpy(dtype=float) + pred_common + pred_split
    result["pred_tpot__phase_coupled"] = test.anchor_log_tpot.to_numpy(dtype=float) + pred_common - pred_split
    if not np.isfinite(result[[f"pred_{m}__{name}" for m in METRICS for name in METHODS]].to_numpy()).all():
        raise ValueError("nonfinite prediction")
    return result


def evaluate_lomo(pairs: pd.DataFrame) -> pd.DataFrame:
    folds = []
    for model in sorted(MODEL_META):
        folds.append(predict_fold(pairs.loc[pairs.model.ne(model)], pairs.loc[pairs.model.eq(model)], model))
    result = pd.concat(folds, ignore_index=True)
    if len(result) != len(pairs) or result.duplicated(KEYS).any():
        raise ValueError("LOMO prediction coverage mismatch")
    return result.sort_values(KEYS, kind="mergesort").reset_index(drop=True)


def evaluate_llama_family(pairs: pd.DataFrame) -> pd.DataFrame:
    held = pairs.model_family.eq("llama")
    return predict_fold(pairs.loc[~held], pairs.loc[held], "llama_family")


def male_scores(rows: pd.DataFrame, population: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    records = []
    for model, part in rows.groupby("model"):
        for method in METHODS:
            record = {"population": population, "model": model, "method": method, "rows": int(len(part))}
            for metric in METRICS:
                record[f"{metric}_male"] = float(np.mean(np.abs(
                    part[f"pred_{metric}__{method}"].to_numpy(dtype=float)
                    - part[f"target_log_{metric}"].to_numpy(dtype=float)
                )))
            records.append(record)
    per_model = pd.DataFrame(records).sort_values(["model", "method"]).reset_index(drop=True)
    macro = per_model.groupby(["population", "method"], as_index=False).agg(
        models=("model", "nunique"), rows=("rows", "sum"),
        ttft_male=("ttft_male", "mean"), tpot_male=("tpot_male", "mean"),
    ).sort_values("method").reset_index(drop=True)
    return per_model, macro


def conflict_scores(rows: pd.DataFrame, population: str) -> pd.DataFrame:
    records = []
    for model, part in list(rows.groupby("model")) + [("ALL", rows)]:
        truth_f = part.delta_ttft.to_numpy(dtype=float)
        truth_p = part.delta_tpot.to_numpy(dtype=float)
        for method in METHODS:
            pred_f = part[f"pred_ttft__{method}"].to_numpy(dtype=float) - part.anchor_log_ttft.to_numpy(dtype=float)
            pred_p = part[f"pred_tpot__{method}"].to_numpy(dtype=float) - part.anchor_log_tpot.to_numpy(dtype=float)
            for name, large in [("any", False), ("large", True)]:
                truth = (truth_f * truth_p < 0)
                pred = (pred_f * pred_p < 0)
                if large:
                    truth &= (np.abs(truth_f) > LARGE_LOG) & (np.abs(truth_p) > LARGE_LOG)
                    pred &= (np.abs(pred_f) > LARGE_LOG) & (np.abs(pred_p) > LARGE_LOG)
                tp = int(np.sum(truth & pred))
                fp = int(np.sum(~truth & pred))
                fn = int(np.sum(truth & ~pred))
                tn = int(np.sum(~truth & ~pred))
                records.append({
                    "population": population, "model": model, "method": method, "conflict_kind": name,
                    "rows": int(len(part)), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "recall": tp / (tp + fn) if tp + fn else np.nan,
                    "false_alarm_rate": fp / (fp + tn) if fp + tn else np.nan,
                })
    return pd.DataFrame(records).sort_values(["population", "model", "method", "conflict_kind"]).reset_index(drop=True)


def decision(primary_macro: pd.DataFrame, primary_model: pd.DataFrame,
             llama_macro: pd.DataFrame, one_macro: pd.DataFrame,
             conflicts: pd.DataFrame) -> dict:
    macro = primary_macro.set_index("method")
    candidate = macro.loc["phase_coupled"]
    baseline = macro.drop("phase_coupled")
    checks = {}
    for metric in METRICS:
        best = float(baseline[f"{metric}_male"].min())
        checks[f"macro_{metric}_5pct_better_than_best"] = bool(candidate[f"{metric}_male"] <= 0.95 * best)
        subset = primary_model.pivot(index="model", columns="method", values=f"{metric}_male")
        checks[f"{metric}_beats_direct_et_3of5"] = bool(
            (subset.phase_coupled < subset.direct_et).sum() >= 3
        )
        llama = llama_macro.set_index("method")
        one = one_macro.set_index("method")
        checks[f"llama_{metric}_within_10pct_direct_et"] = bool(
            llama.loc["phase_coupled", f"{metric}_male"] <= 1.10 * llama.loc["direct_et", f"{metric}_male"]
        )
        checks[f"output1pct_{metric}_within_10pct_direct_et"] = bool(
            one.loc["phase_coupled", f"{metric}_male"] <= 1.10 * one.loc["direct_et", f"{metric}_male"]
        )
    con = conflicts.loc[
        conflicts.population.eq("primary") & conflicts.model.eq("ALL")
        & conflicts.conflict_kind.eq("any")
    ].set_index("method")
    checks["conflict_recall_at_least_direct_et"] = bool(
        con.loc["phase_coupled", "recall"] >= con.loc["direct_et", "recall"]
    )
    checks["conflict_fpr_at_most_direct_et_plus_0_10"] = bool(
        con.loc["phase_coupled", "false_alarm_rate"]
        <= con.loc["direct_et", "false_alarm_rate"] + 0.10
    )
    return {
        "candidate": "phase_coupled", "checks": checks,
        "passed": int(sum(checks.values())), "total": len(checks),
        "status": "prioritize_followup" if all(checks.values()) else "do_not_prioritize",
        "scope": "exploratory next-data priority, not safety adoption",
    }


def run(source: Path, protocol: Path, output: Path) -> dict:
    verify_frozen(source, protocol)
    pairs = build_pairs(pd.read_parquet(source))
    lomo = evaluate_lomo(pairs)
    llama = evaluate_llama_family(pairs)
    populations = {
        "primary": lomo,
        "output5pct": lomo.loc[lomo.output_token_ratio.le(1.05)].copy(),
        "output1pct": lomo.loc[lomo.output_token_ratio.le(1.01)].copy(),
    }
    expected = {"primary": 130, "output5pct": 127, "output1pct": 83}
    if {k: len(v) for k, v in populations.items()} != expected:
        raise ValueError("sensitivity population changed")
    score_models = []
    score_macros = []
    conflict_tables = []
    for name, population in populations.items():
        sm, ma = male_scores(population, name)
        score_models.append(sm)
        score_macros.append(ma)
        conflict_tables.append(conflict_scores(population, name))
    llama_model, llama_macro = male_scores(llama, "llama_family")
    score_models.append(llama_model)
    score_macros.append(llama_macro)
    conflict_tables.append(conflict_scores(llama, "llama_family"))
    per_model = pd.concat(score_models, ignore_index=True)
    macro = pd.concat(score_macros, ignore_index=True)
    conflicts = pd.concat(conflict_tables, ignore_index=True)
    d = decision(
        macro.loc[macro.population.eq("primary")],
        per_model.loc[per_model.population.eq("primary")],
        macro.loc[macro.population.eq("llama_family")],
        macro.loc[macro.population.eq("output1pct")],
        conflicts,
    )
    output.mkdir(parents=True, exist_ok=True)
    lomo.to_csv(output / "lomo_predictions.csv", index=False)
    llama.to_csv(output / "llama_family_predictions.csv", index=False)
    per_model.to_csv(output / "per_model_male.csv", index=False)
    macro.to_csv(output / "macro_male.csv", index=False)
    conflicts.to_csv(output / "conflict_counts.csv", index=False)
    result = {
        "source_sha256": SOURCE_SHA256, "protocol_sha256": PROTOCOL_SHA256,
        "source_revision": "722f2b9d8a6acd222811f4e210a96865f774f6ee",
        "pair_count": int(len(pairs)), "model_count": int(pairs.model.nunique()),
        "population_rows": expected, "llama_family_rows": int(len(llama)),
        "decision": d,
        "primary_macro": macro.loc[macro.population.eq("primary")].to_dict(orient="records"),
        "output1pct_macro": macro.loc[macro.population.eq("output1pct")].to_dict(orient="records"),
        "llama_family_macro": macro.loc[macro.population.eq("llama_family")].to_dict(orient="records"),
    }
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = run(args.source, args.protocol, args.output)
    print(json.dumps({
        "pairs": result["pair_count"], "models": result["model_count"],
        "decision": result["decision"],
        "primary_macro": result["primary_macro"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
