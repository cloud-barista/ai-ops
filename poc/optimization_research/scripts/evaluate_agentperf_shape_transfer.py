#!/usr/bin/env python3
"""Frozen, CPU-only shape-transfer evaluation on pinned AgentPerfBench summaries.

See reports/agentperfbench_shape_transfer_protocol.md. The target execution's
outcome is used only for labels, evaluation-population filters, and retrospective
synthetic constraints. It is never a prediction feature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/external/agentperfbench/trace_replay/summary.parquet"
DEFAULT_OUTPUT = ROOT / "artifacts/agentperf_shape_transfer_v1"
SOURCE_SHA256 = "568efbdae473a830ba792317fb09cfba0ea4f667db67b93d28e72637a5c71d1e"
SOURCE_REVISION = "722f2b9d8a6acd222811f4e210a96865f774f6ee"
GROUP_KEYS = ["model", "engine", "profile", "concurrency", "num_requests"]
REQUIRED_POSITIVE = [
    "duration_s", "num_requests", "request_throughput", "input_token_throughput",
    "output_token_throughput", "p90_ttft_ms", "p90_tpot_ms", "p90_e2el_ms",
]
GPU_TYPES = ("2080Ti", "3090", "A100-40GB", "H100")
NUMERIC_FEATURES = [
    "model_size_b", "concurrency", "num_requests", "anchor_gpu_count",
    "target_gpu_count", "anchor_log_e2el", "anchor_log_rps",
]
CATEGORICAL_FEATURES = [
    "model_family", "engine", "profile", "anchor_gpu_type", "target_gpu_type",
]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
METRICS = {"e2el": "p90_e2el_ms", "rps": "request_throughput"}
METHODS = ("anchor_copy", "donor_median", "direct_et", "ratio_et")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_source(path: Path) -> None:
    actual = sha256(path)
    if actual != SOURCE_SHA256:
        raise ValueError(f"source SHA-256 mismatch: {actual} != {SOURCE_SHA256}")


def hardware_parts(hardware: str) -> tuple[str, int]:
    match = re.fullmatch(r"(2080Ti|3090|A100-40GB|H100)(?:x([248]))?", hardware)
    if not match:
        raise ValueError(f"unknown hardware: {hardware}")
    return match.group(1), int(match.group(2) or 1)


def hardware_order(hardware: str) -> tuple[int, int]:
    gpu_type, count = hardware_parts(hardware)
    return count, GPU_TYPES.index(gpu_type)


def nominal_size_b(model: str) -> float:
    mixture = re.search(r"(\d+)x(\d+)B", model, flags=re.IGNORECASE)
    if mixture:
        return float(int(mixture.group(1)) * int(mixture.group(2)))
    dense = re.search(r"(\d+(?:\.\d+)?)B", model, flags=re.IGNORECASE)
    return float(dense.group(1)) if dense else float("nan")


def clean_source(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required = set(GROUP_KEYS + ["model_family", "hardware", "run_id"] + REQUIRED_POSITIVE)
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"missing source columns: {missing}")
    if rows.duplicated(GROUP_KEYS + ["hardware"]).any():
        raise ValueError("duplicate group/hardware rows")
    if rows.run_id.duplicated().any():
        raise ValueError("duplicate run_id")
    for hardware in rows.hardware.unique():
        hardware_parts(str(hardware))
    values = rows[REQUIRED_POSITIVE].astype(float)
    positive = pd.Series(np.isfinite(values.to_numpy()).all(axis=1) & (values.to_numpy() > 0).all(axis=1), index=rows.index)
    complete = ((rows.request_throughput.astype(float) * rows.duration_s.astype(float) - rows.num_requests.astype(float)).abs() <= 1e-5)
    clean = rows.loc[positive & complete].copy()
    clean["input_tokens_total"] = clean.input_token_throughput * clean.duration_s
    clean["output_tokens_total"] = clean.output_token_throughput * clean.duration_s
    stats = {
        "source_rows": int(len(rows)),
        "excluded_nonpositive_or_nonfinite": int((~positive).sum()),
        "excluded_not_all_requests_successful_after_positive": int((positive & ~complete).sum()),
        "all_success_positive_rows": int(len(clean)),
    }
    return clean, stats


def select_comparable_groups(clean: pd.DataFrame, input_ratio: float | None, output_ratio: float | None) -> tuple[pd.DataFrame, dict]:
    summary = clean.groupby(GROUP_KEYS, dropna=False).agg(
        shape_count=("hardware", "nunique"),
        input_min=("input_tokens_total", "min"), input_max=("input_tokens_total", "max"),
        output_min=("output_tokens_total", "min"), output_max=("output_tokens_total", "max"),
    )
    two = summary.shape_count >= 2
    selected = two.copy()
    if input_ratio is not None:
        selected &= summary.input_max / summary.input_min <= input_ratio
    if output_ratio is not None:
        selected &= summary.output_max / summary.output_min <= output_ratio
    keys = summary.loc[selected].index
    paired = clean.set_index(GROUP_KEYS).loc[keys].reset_index() if len(keys) else clean.iloc[0:0].copy()
    stats = {
        "all_success_groups": int(len(summary)),
        "groups_with_two_or_more_shapes": int(two.sum()),
        "groups_excluded_for_token_mismatch": int((two & ~selected).sum()),
        "selected_groups": int(selected.sum()),
        "selected_rows": int(len(paired)),
        "selected_models": int(paired.model.nunique()),
        "input_ratio_limit": input_ratio,
        "output_ratio_limit": output_ratio,
    }
    return paired, stats


def build_pairs(paired: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    for key, part in paired.groupby(GROUP_KEYS, sort=True, dropna=False):
        part = part.sort_values("hardware", key=lambda col: col.map(hardware_order)).reset_index(drop=True)
        anchor = part.iloc[0]
        anchor_type, anchor_count = hardware_parts(str(anchor.hardware))
        group_id = json.dumps([str(v) for v in key], separators=(",", ":"))
        anchor_logs = {"e2el": float(np.log(anchor.p90_e2el_ms)), "rps": float(np.log(anchor.request_throughput))}
        for _, target in part.iloc[1:].iterrows():
            target_type, target_count = hardware_parts(str(target.hardware))
            record = {
                "group_id": group_id, "model": str(target.model),
                "model_family": str(target.model_family), "engine": str(target.engine),
                "profile": str(target.profile), "concurrency": int(target.concurrency),
                "num_requests": int(target.num_requests),
                "model_size_b": nominal_size_b(str(target.model)),
                "anchor_hardware": str(anchor.hardware), "target_hardware": str(target.hardware),
                "anchor_gpu_type": anchor_type, "target_gpu_type": target_type,
                "anchor_gpu_count": anchor_count, "target_gpu_count": target_count,
                "anchor_raw_e2el": float(anchor.p90_e2el_ms),
                "anchor_raw_rps": float(anchor.request_throughput),
                "target_raw_e2el": float(target.p90_e2el_ms),
                "target_raw_rps": float(target.request_throughput),
                "anchor_log_e2el": anchor_logs["e2el"], "anchor_log_rps": anchor_logs["rps"],
                "target_log_e2el": float(np.log(target.p90_e2el_ms)),
                "target_log_rps": float(np.log(target.request_throughput)),
            }
            records.append(record)
    return pd.DataFrame.from_records(records)


def donor_predict(train: pd.DataFrame, test: pd.DataFrame, metric: str) -> np.ndarray:
    anchor = f"anchor_log_{metric}"
    target = f"target_log_{metric}"
    donors = train.assign(delta=train[target] - train[anchor])
    by_hardware = donors.groupby(["anchor_hardware", "target_hardware"]).delta.agg(["median", "count"])
    by_counts = donors.groupby(["anchor_gpu_count", "target_gpu_count"]).delta.agg(["median", "count"])
    global_median = float(donors.delta.median())
    values = []
    for _, row in test.iterrows():
        hardware_key = row.anchor_hardware, row.target_hardware
        count_key = row.anchor_gpu_count, row.target_gpu_count
        if hardware_key in by_hardware.index and by_hardware.loc[hardware_key, "count"] >= 5:
            delta = float(by_hardware.loc[hardware_key, "median"])
        elif count_key in by_counts.index and by_counts.loc[count_key, "count"] >= 5:
            delta = float(by_counts.loc[count_key, "median"])
        else:
            delta = global_median
        values.append(float(row[anchor]) + delta)
    return np.asarray(values)


def make_pipeline(train: pd.DataFrame) -> Pipeline:
    # Fold-specific category lists ensure an unseen family has an explicit unknown
    # representation. No held-out model row participates in preprocessing fit.
    encoder_categories = []
    for column in CATEGORICAL_FEATURES:
        categories = sorted(str(v) for v in train[column].dropna().unique())
        if column == "model_family" and "unknown" not in categories:
            categories.append("unknown")
        encoder_categories.append(categories)
    preprocess = ColumnTransformer([
        ("numeric", SimpleImputer(strategy="median"), NUMERIC_FEATURES),
        ("categorical", OneHotEncoder(categories=encoder_categories, handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ])
    return Pipeline([
        ("preprocess", preprocess),
        ("regressor", ExtraTreesRegressor(
            n_estimators=200, min_samples_leaf=5, max_features=1.0,
            random_state=42, n_jobs=1,
        )),
    ])


def _test_features(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    values = test[FEATURE_COLUMNS].copy()
    known = set(train.model_family)
    values.loc[~values.model_family.isin(known), "model_family"] = "unknown"
    return values


def predict_fold(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    if train.empty or test.empty:
        raise ValueError("empty train or test fold")
    if set(train.model) & set(test.model):
        raise ValueError("model leakage across fold")
    predictions = test[[
        "group_id", "model", "model_family", "anchor_hardware", "target_hardware",
        "anchor_gpu_type", "target_gpu_type", "anchor_gpu_count", "target_gpu_count",
        "anchor_raw_e2el", "anchor_raw_rps", "target_raw_e2el", "target_raw_rps",
        "anchor_log_e2el", "anchor_log_rps", "target_log_e2el", "target_log_rps",
    ]].copy().reset_index(drop=True)
    x_test = _test_features(train, test)
    for metric in METRICS:
        anchor = f"anchor_log_{metric}"
        target = f"target_log_{metric}"
        predictions[f"pred_anchor_copy_{metric}"] = test[anchor].to_numpy()
        predictions[f"pred_donor_median_{metric}"] = donor_predict(train, test, metric)
        for method in ("direct_et", "ratio_et"):
            pipeline = make_pipeline(train)
            y_train = train[target] if method == "direct_et" else train[target] - train[anchor]
            pipeline.fit(train[FEATURE_COLUMNS], y_train)
            values = pipeline.predict(x_test)
            if method == "ratio_et":
                values += test[anchor].to_numpy()
            predictions[f"pred_{method}_{metric}"] = values
    return predictions


def evaluate_folds(pairs: pd.DataFrame, fold_kind: str) -> pd.DataFrame:
    if fold_kind not in ("model", "family"):
        raise ValueError(fold_kind)
    columns = "model" if fold_kind == "model" else "model_family"
    parts = []
    for value in sorted(pairs[columns].unique()):
        train = pairs.loc[pairs[columns] != value]
        test = pairs.loc[pairs[columns] == value]
        prediction = predict_fold(train, test)
        prediction.insert(0, "fold", str(value))
        prediction.insert(0, "fold_kind", fold_kind)
        parts.append(prediction)
    return pd.concat(parts, ignore_index=True)


def fold_manifest(pairs: pd.DataFrame, fold_kind: str) -> list[dict]:
    column = "model" if fold_kind == "model" else "model_family"
    return [{
        "fold": str(value),
        "train_rows": int((pairs[column] != value).sum()),
        "test_rows": int((pairs[column] == value).sum()),
        "train_models": sorted(pairs.loc[pairs[column] != value, "model"].unique().tolist()),
        "test_models": sorted(pairs.loc[pairs[column] == value, "model"].unique().tolist()),
    } for value in sorted(pairs[column].unique())]


def score_predictions(predictions: pd.DataFrame, fold_kind: str) -> dict:
    grouped = predictions.loc[predictions.fold_kind == fold_kind]
    scores: dict[str, dict] = {}
    for metric in METRICS:
        target = grouped[f"target_log_{metric}"].to_numpy()
        anchor = grouped[f"anchor_log_{metric}"].to_numpy()
        scores[metric] = {}
        for method in METHODS:
            predicted = grouped[f"pred_{method}_{metric}"].to_numpy()
            errors = np.abs(predicted - target)
            per_fold = {
                str(fold): {"rows": int(len(part)), "male": float(np.mean(np.abs(part[f"pred_{method}_{metric}"] - part[f"target_log_{metric}"]))),
                            "direction_hit_rate": float(np.mean(np.sign(part[f"pred_{method}_{metric}"] - part[f"anchor_log_{metric}"]) == np.sign(part[f"target_log_{metric}"] - part[f"anchor_log_{metric}"])))}
                for fold, part in grouped.groupby("fold")
            }
            scores[metric][method] = {
                "equal_fold_male": float(np.mean([v["male"] for v in per_fold.values()])),
                "row_weighted_male": float(np.mean(errors)),
                "direction_hit_rate": float(np.mean(np.sign(predicted - anchor) == np.sign(target - anchor))),
                "per_fold": per_fold,
            }
    return scores


def synthetic_choices(predictions: pd.DataFrame, threshold_scale: str = "log") -> dict:
    """Retrospective median thresholds among observed, successful executions.

    Successful execution alone does not imply satisfaction of these synthetic
    latency/throughput thresholds. The raw-scale version is an interpretation
    sensitivity check for the protocol's otherwise ambiguous median wording.
    """
    if not (predictions.fold_kind == "model").all():
        raise ValueError("synthetic choices require model-fold predictions only")
    if threshold_scale not in ("log", "raw"):
        raise ValueError(threshold_scale)
    summary = {method: {"scenarios": 0, "predicted_approval": 0, "false_approval": 0,
                        "correct_approval": 0, "no_predicted_approval": 0}
               for method in METHODS}
    scenarios = []
    for group_id, part in predictions.groupby("group_id", sort=True):
        anchor = part.iloc[0]
        # Anchor appears once, even if the group has several target shapes.
        candidates = [{
            "hardware": anchor.anchor_hardware, "gpu_type": anchor.anchor_gpu_type,
            "count": int(anchor.anchor_gpu_count), "actual_e2el": float(anchor.anchor_log_e2el),
            "actual_rps": float(anchor.anchor_log_rps),
            "actual_raw_e2el": float(anchor.anchor_raw_e2el),
            "actual_raw_rps": float(anchor.anchor_raw_rps),
            **{f"pred_{method}_{metric}": float(anchor[f"anchor_log_{metric}"])
               for method in METHODS for metric in METRICS},
        }]
        for _, row in part.iterrows():
            candidates.append({
                "hardware": row.target_hardware, "gpu_type": row.target_gpu_type,
                "count": int(row.target_gpu_count),
                "actual_e2el": float(row.target_log_e2el), "actual_rps": float(row.target_log_rps),
                "actual_raw_e2el": float(row.target_raw_e2el),
                "actual_raw_rps": float(row.target_raw_rps),
                **{f"pred_{method}_{metric}": float(row[f"pred_{method}_{metric}"])
                   for method in METHODS for metric in METRICS},
            })
        for gpu_type in GPU_TYPES:
            same = sorted((c for c in candidates if c["gpu_type"] == gpu_type), key=lambda c: c["count"])
            if len(same) < 2:
                continue
            if threshold_scale == "log":
                latency_limit = float(np.median([c["actual_e2el"] for c in same]))
                rps_limit = float(np.median([c["actual_rps"] for c in same]))
            else:
                raw_latency_limit = float(np.median([c["actual_raw_e2el"] for c in same]))
                raw_rps_limit = float(np.median([c["actual_raw_rps"] for c in same]))
                latency_limit = float(np.log(raw_latency_limit))
                rps_limit = float(np.log(raw_rps_limit))
            if threshold_scale == "raw":
                actually_feasible = lambda candidate: (candidate["actual_raw_e2el"] <= raw_latency_limit
                                                      and candidate["actual_raw_rps"] >= raw_rps_limit)
            else:
                actually_feasible = lambda candidate: (candidate["actual_e2el"] <= latency_limit
                                                      and candidate["actual_rps"] >= rps_limit)
            item = {"group_id": group_id, "gpu_type": gpu_type, "candidate_count": len(same),
                    "latency_log_limit": latency_limit, "rps_log_limit": rps_limit,
                    "actual_any_feasible": bool(any(actually_feasible(c) for c in same))}
            for method in METHODS:
                approved = [c for c in same if c[f"pred_{method}_e2el"] <= latency_limit
                            and c[f"pred_{method}_rps"] >= rps_limit]
                chosen = approved[0] if approved else None
                totals = summary[method]
                totals["scenarios"] += 1
                if chosen is None:
                    totals["no_predicted_approval"] += 1
                    item[f"{method}_chosen"] = None
                    item[f"{method}_false_approval"] = False
                else:
                    totals["predicted_approval"] += 1
                    actual_feasible = actually_feasible(chosen)
                    totals["false_approval" if not actual_feasible else "correct_approval"] += 1
                    item[f"{method}_chosen"] = chosen["hardware"]
                    item[f"{method}_false_approval"] = bool(not actual_feasible)
            scenarios.append(item)
    return {"summary": summary, "scenarios": scenarios}


def adoption_decision(model_scores: dict, family_scores: dict, choices: dict) -> dict:
    checks: dict[str, dict | bool] = {}
    for metric in METRICS:
        transferred = model_scores[metric]["ratio_et"]["equal_fold_male"]
        for baseline in ("donor_median", "direct_et"):
            baseline_score = model_scores[metric][baseline]["equal_fold_male"]
            wins = sum(
                model_scores[metric]["ratio_et"]["per_fold"][model]["male"]
                < model_scores[metric][baseline]["per_fold"][model]["male"]
                for model in model_scores[metric][baseline]["per_fold"]
            )
            checks[f"{metric}_five_percent_better_than_{baseline}"] = {
                "pass": bool(transferred <= baseline_score * 0.95),
                "ratio_et_male": transferred, "baseline_male": baseline_score,
            }
            checks[f"{metric}_wins_six_models_vs_{baseline}"] = {"pass": bool(wins >= 6), "wins": wins, "total": 9}
    for family in sorted(next(iter(family_scores.values()))["ratio_et"]["per_fold"]):
        for metric in METRICS:
            ratio = family_scores[metric]["ratio_et"]["per_fold"][family]["male"]
            direct = family_scores[metric]["direct_et"]["per_fold"][family]["male"]
            checks[f"family_{family}_{metric}_within_ten_percent_of_direct"] = {
                "pass": bool(ratio <= direct * 1.10), "ratio_et_male": ratio,
                "direct_et_male": direct,
            }
    ratio_false = choices["summary"]["ratio_et"]["false_approval"]
    direct_false = choices["summary"]["direct_et"]["false_approval"]
    checks["synthetic_false_approvals_no_more_than_direct"] = {
        "pass": bool(ratio_false <= direct_false),
        "ratio_et_false_approvals": ratio_false, "direct_et_false_approvals": direct_false,
    }
    passed = sum(bool(value["pass"]) for value in checks.values())
    return {"decision": "adopt" if passed == len(checks) else "do_not_adopt",
            "passed": passed, "total": len(checks), "checks": checks}


def evaluate_population(clean: pd.DataFrame, name: str, input_ratio: float | None, output_ratio: float | None,
                        include_family: bool) -> tuple[dict, pd.DataFrame, list[dict], list[dict]]:
    selected, selection = select_comparable_groups(clean, input_ratio, output_ratio)
    pairs = build_pairs(selected)
    if pairs.model.nunique() != 9:
        raise ValueError(f"{name}: expected 9 models, got {pairs.model.nunique()}")
    if name == "primary" and (selection["selected_groups"], selection["selected_rows"]) != (485, 1526):
        raise ValueError(f"primary support drift: {selection}")
    model_predictions = evaluate_folds(pairs, "model")
    model_scores = score_predictions(model_predictions, "model")
    result = {"population": name, "selection": selection, "pairs": int(len(pairs)),
              "model_fold_manifest": fold_manifest(pairs, "model"),
              "model_holdout": model_scores}
    if include_family:
        family_predictions = evaluate_folds(pairs, "family")
        family_scores = score_predictions(family_predictions, "family")
        choices = synthetic_choices(model_predictions)
        raw_choices = synthetic_choices(model_predictions, threshold_scale="raw")
        result["family_holdout"] = family_scores
        result["family_fold_manifest"] = fold_manifest(pairs, "family")
        result["synthetic_choice_summary"] = choices["summary"]
        result["synthetic_raw_median_sensitivity_summary"] = raw_choices["summary"]
        result["synthetic_choice_support"] = {
            "scenarios": len(choices["scenarios"]),
            "models": sorted({json.loads(c["group_id"])[0] for c in choices["scenarios"]}),
            "candidate_rows_in_scenarios": int(sum(c["candidate_count"] for c in choices["scenarios"])),
            "actual_feasible_scenarios_log_median": int(sum(c["actual_any_feasible"] for c in choices["scenarios"])),
            "actual_feasible_scenarios_raw_median": int(sum(c["actual_any_feasible"] for c in raw_choices["scenarios"])),
        }
        result["adoption"] = adoption_decision(model_scores, family_scores, choices)
        predictions = pd.concat([model_predictions, family_predictions], ignore_index=True)
        return result, predictions, choices["scenarios"], raw_choices["scenarios"]
    return result, model_predictions, [], []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--primary-only", action="store_true", help="run primary before fixed sensitivity checks")
    args = parser.parse_args()
    verify_source(args.source)
    raw = pd.read_parquet(args.source)
    clean, cleaning = clean_source(raw)
    if len(clean) != 2172:
        raise ValueError(f"clean support drift: {len(clean)} != 2172")
    populations = [("primary", 1.00001, 1.05, True)]
    if not args.primary_only:
        populations.extend([
            ("output_ratio_1pct", 1.00001, 1.01, False),
            ("no_token_limit", None, None, False),
        ])
    args.output.mkdir(parents=True, exist_ok=True)
    output = {"source": {"path": str(args.source), "revision": SOURCE_REVISION,
                         "sha256": SOURCE_SHA256}, "cleaning": cleaning, "populations": {}}
    for name, input_ratio, output_ratio, family in populations:
        result, predictions, scenarios, raw_scenarios = evaluate_population(clean, name, input_ratio, output_ratio, family)
        output["populations"][name] = result
        predictions.to_csv(args.output / f"{name}_predictions.csv", index=False)
        if scenarios:
            pd.DataFrame(scenarios).to_csv(args.output / "primary_synthetic_choices.csv", index=False)
            pd.DataFrame(raw_scenarios).to_csv(args.output / "primary_synthetic_raw_median_sensitivity.csv", index=False)
        with (args.output / "summary.json").open("w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False, allow_nan=False)
        print(json.dumps({"population": name, "selection": result["selection"],
                          "model_holdout_equal_fold_male": {
                              metric: {method: values["equal_fold_male"] for method, values in methods.items()}
                              for metric, methods in result["model_holdout"].items()},
                          "adoption": result.get("adoption")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
