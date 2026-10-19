"""Model-held-out TP2-to-TP4 serving response on pinned Scherm B200 rows."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/scherm_onprem_2026/serving_ALL.csv"
PROTOCOL = ROOT / "reports/scherm_b200_tp24_protocol.md"
OUTPUT = ROOT / "artifacts/scherm_b200_tp24_v1"
SOURCE_SHA256 = "d7f677cf0c4ccfd8c702c8458c4e6c3d324e90292bf13e5f09e065431e8d5f9e"
MODEL_RE = re.compile(r"^Qwen/Qwen2\.5-(Coder-)?(\d+)B-Instruct-AWQ$")
METHODS = ("persistence", "gpu_linear", "donor_median", "ridge")
TARGETS = ("output_tok_s_median", "ttft_ms_p95")
FEATURES = ("log_concurrency", "log_anchor_output", "log_anchor_ttft",
            "log_model_size_b", "is_coder")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_pairs() -> pd.DataFrame:
    source = SOURCE.read_bytes()
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("pinned serving aggregate changed")
    data = pd.read_csv(io.BytesIO(source))
    if len(data) != 528 or len(data.columns) != 15:
        raise ValueError("unexpected consolidated serving shape")
    selected = data.loc[data.gpu.eq("NVIDIA B200")
                        & data.model.astype(str).map(lambda x: MODEL_RE.fullmatch(x) is not None)].copy()
    selected["tp"] = pd.to_numeric(selected.shard.str.extract(r"_tp(\d+)_", expand=False),
                                     errors="coerce")
    selected = selected.loc[selected.tp.isin((2, 4))].copy()
    if (selected.empty or selected[["model", "tp", "concurrency"]].duplicated().any()
            or not selected.n_ok.ge(10).all()
            or not selected.concurrency.gt(0).all()
            or any(not np.isfinite(selected[col]).all() or not selected[col].gt(0).all()
                   for col in TARGETS)):
        raise ValueError("B200 TP2/TP4 eligibility or unique keys failed")
    left = selected.loc[selected.tp.eq(2)].drop(columns=["tp", "gpu_count"])
    right = selected.loc[selected.tp.eq(4)].drop(columns=["tp", "gpu_count"])
    pairs = left.merge(right, on=["model", "concurrency"], how="inner",
                       suffixes=("_tp2", "_tp4"), validate="one_to_one")
    pairs = pairs.sort_values(["model", "concurrency"], kind="mergesort").reset_index(drop=True)
    if len(pairs) != 64 or pairs.model.nunique() != 7:
        raise ValueError("declared 64 pairs / 7 models missing")
    for col in TARGETS:
        pairs[f"log_ratio_{col}"] = np.log(pairs[f"{col}_tp4"] / pairs[f"{col}_tp2"])
    parsed = pairs.model.map(lambda x: MODEL_RE.fullmatch(x))
    pairs["log_model_size_b"] = np.log(parsed.map(lambda m: int(m.group(2))).to_numpy(float))
    pairs["is_coder"] = parsed.map(lambda m: 1 if m.group(1) else 0).to_numpy(int)
    pairs["log_concurrency"] = np.log(pairs.concurrency.to_numpy(float))
    pairs["log_anchor_output"] = np.log(pairs.output_tok_s_median_tp2.to_numpy(float))
    pairs["log_anchor_ttft"] = np.log(pairs.ttft_ms_p95_tp2.to_numpy(float))
    return pairs


def evaluate(pairs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    forecasts = []
    for held_model in sorted(pairs.model.unique()):
        train = pairs.loc[pairs.model.ne(held_model)]
        test = pairs.loc[pairs.model.eq(held_model)]
        if train.model.nunique() != 6 or test.empty or set(train.model) & set(test.model):
            raise ValueError("model holdout leakage")
        for target in TARGETS:
            label = f"log_ratio_{target}"
            by_concurrency = train.groupby("concurrency")[label].median()
            fallback = float(train[label].median())
            donor = test.concurrency.map(by_concurrency).fillna(fallback).to_numpy(float)
            model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
            model.fit(train[list(FEATURES)].to_numpy(float), train[label].to_numpy(float))
            ridge = model.predict(test[list(FEATURES)].to_numpy(float))
            raw = {"persistence": np.zeros(len(test)),
                   "gpu_linear": np.full(len(test), math.log(2) if target == TARGETS[0] else 0.0),
                   "donor_median": donor,
                   "ridge": ridge}
            truth = test[label].to_numpy(float)
            for method, estimate in raw.items():
                for index, row in enumerate(test.itertuples(index=False)):
                    forecasts.append({"held_out_model": held_model,
                                      "concurrency": int(row.concurrency),
                                      "target": target,
                                      "method": method,
                                      "true_log_ratio": float(truth[index]),
                                      "predicted_log_ratio": float(estimate[index]),
                                      "abs_log_error": float(abs(truth[index]-estimate[index])),
                                      "direction_correct": bool(np.sign(truth[index])
                                                                == np.sign(estimate[index]))})
    result = pd.DataFrame(forecasts).sort_values(
        ["held_out_model", "concurrency", "target", "method"], kind="mergesort").reset_index(drop=True)
    expected = len(pairs) * len(TARGETS) * len(METHODS)
    if len(result) != expected or result[["held_out_model", "concurrency", "target", "method"]].duplicated().any():
        raise ValueError("prediction coverage differs")
    per_model = result.groupby(["target", "method", "held_out_model"]).agg(
        male=("abs_log_error", "mean"),
        direction_accuracy=("direction_correct", "mean"),
        pairs=("abs_log_error", "size")).reset_index()
    summary = {}
    for target in TARGETS:
        one = per_model.loc[per_model.target.eq(target)]
        summary[target] = {}
        for method in METHODS:
            group = one.loc[one.method.eq(method)]
            rows = result.loc[result.target.eq(target) & result.method.eq(method)]
            summary[target][method] = {
                "model_equal_male": float(group.male.mean()),
                "row_male": float(rows.abs_log_error.mean()),
                "model_equal_direction_accuracy": float(group.direction_accuracy.mean()),
            }
    best = {}
    for target in TARGETS:
        best[target] = min(METHODS[:-1],
                           key=lambda method: summary[target][method]["model_equal_male"])
    fold_wins = 0
    worst_ratio = 0.0
    for held_model in sorted(pairs.model.unique()):
        both = True
        for target in TARGETS:
            one = per_model.loc[per_model.target.eq(target) & per_model.held_out_model.eq(held_model)]
            baseline = float(one.loc[one.method.eq(best[target]), "male"].iloc[0])
            candidate = float(one.loc[one.method.eq("ridge"), "male"].iloc[0])
            both &= candidate <= baseline
            worst_ratio = max(worst_ratio, candidate / baseline if baseline > 0 else math.inf)
        fold_wins += int(both)
    checks = {
        "both_model_equal_male_at_least_5pct_better": all(
            summary[t]["ridge"]["model_equal_male"] <= .95 * summary[t][best[t]]["model_equal_male"]
            for t in TARGETS),
        "at_least_5_of_7_models_better_on_both": fold_wins >= 5,
        "no_model_metric_more_than_20pct_worse": worst_ratio <= 1.2,
    }
    assessment = {"methods": summary,
                  "best_fixed_baseline_by_target": best,
                  "models_better_on_both": fold_wins,
                  "worst_model_loss_ratio_to_best_baseline": worst_ratio,
                  "gate": {"all_passed": bool(all(checks.values())), "checks": checks}}
    return result, assessment


def save_checked(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"stored artifact differs: {path}")
    else:
        path.write_bytes(content)


def main() -> None:
    pairs = source_pairs()
    forecasts, assessment = evaluate(pairs)
    output = {
        "status": "EXPLORATORY_GO" if assessment["gate"]["all_passed"] else "EXPLORATORY_NO_GO",
        "source_revision": "e19edbaccec8add876073a3b0668e84115a59d0f",
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": sha256(PROTOCOL.read_bytes()),
        "script_sha256": sha256(Path(__file__).read_bytes()),
        "pairs": int(len(pairs)), "models": int(pairs.model.nunique()),
        "scope": "B200 Qwen2.5 AWQ tensor-parallel warm serving response, not VM action or user p95",
        "outcomes_seen_before_protocol": True,
        "assessment": assessment,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    save_checked(OUTPUT / "selected_pairs.csv", pairs.to_csv(index=False, float_format="%.17g").encode())
    save_checked(OUTPUT / "predictions.csv", forecasts.to_csv(index=False, float_format="%.17g").encode())
    save_checked(OUTPUT / "summary.json", (json.dumps(output, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"status": output["status"], "pairs": output["pairs"],
                      "models": output["models"], "assessment": assessment},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
