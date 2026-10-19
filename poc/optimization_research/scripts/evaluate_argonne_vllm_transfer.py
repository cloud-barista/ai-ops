"""Held-out-model latency transfer within observed Argonne vLLM shapes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from audit_argonne_vllm import OUT as AUDIT_OUT, ROOT, WORKLOAD, audit, sha256


PROTOCOL = ROOT / "reports/argonne_vllm_shape_transfer_protocol.md"
OUT = ROOT / "artifacts/argonne_vllm_transfer_v1"
PARAMETERS_B = {
    "Deci/DeciLM-7B": 7,
    "Qwen/Qwen2-72B": 72,
    "Qwen/Qwen2-7B": 7,
    "meta-llama/Llama-2-70b-hf": 70,
    "meta-llama/Llama-2-7b-hf": 7,
    "meta-llama/Meta-Llama-3-70B": 70,
    "meta-llama/Meta-Llama-3-8B": 8,
    "mistralai/Mistral-7B-v0.1": 7,
    "mistralai/Mixtral-8x7B-v0.1": 47,
}
METHODS = ("anchor_copy", "direct", "anchor_conditioned", "anchor_residual")


def add_anchors(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach one anchor per workload using shape metadata, never latency order."""
    if frame.duplicated(WORKLOAD + ["Hardware", "Num of Hardware"]).any():
        raise ValueError("ambiguous duplicate shape")
    selected = (frame.sort_values(["Num of Hardware", "Hardware", "source_row_index"])
                .drop_duplicates(WORKLOAD))
    anchor = selected[WORKLOAD + ["source_row_index", "Hardware",
                                   "Num of Hardware", "Latency"]].rename(columns={
        "source_row_index": "anchor_source_row_index",
        "Hardware": "anchor_hardware",
        "Num of Hardware": "anchor_count",
        "Latency": "anchor_latency",
    })
    result = frame.merge(anchor, on=WORKLOAD, how="left", validate="many_to_one")
    result["is_anchor"] = result.source_row_index == result.anchor_source_row_index
    if result.anchor_latency.isna().any() or result.groupby(WORKLOAD).is_anchor.sum().ne(1).any():
        raise ValueError("missing or repeated anchor")
    return result.sort_values("source_row_index").reset_index(drop=True)


def features(frame: pd.DataFrame, hardware_names: tuple[str, ...],
             with_anchor: bool = False) -> np.ndarray:
    """Build features without ever reading target-row Latency."""
    if not set(frame.Model).issubset(PARAMETERS_B):
        raise ValueError("unmapped model nominal size")
    numeric = np.column_stack((
        np.log2(frame.Model.map(PARAMETERS_B).to_numpy(dtype=float)),
        np.log2(frame["Input Output Length"].to_numpy(dtype=float)),
        np.log2(frame["Batch Size"].to_numpy(dtype=float)),
        np.log2(frame["Num of Hardware"].to_numpy(dtype=float)),
    ))
    target_hardware = (frame.Hardware.to_numpy()[:, None]
                       == np.asarray(hardware_names)[None, :]).astype(float)
    base = np.column_stack((numeric, target_hardware))
    if not with_anchor:
        return base
    anchor_hardware = (frame.anchor_hardware.to_numpy()[:, None]
                       == np.asarray(hardware_names)[None, :]).astype(float)
    return np.column_stack((base, np.log2(frame.anchor_count.to_numpy(dtype=float)),
                            anchor_hardware, np.log(frame.anchor_latency.to_numpy(dtype=float))))


def estimator() -> ExtraTreesRegressor:
    return ExtraTreesRegressor(n_estimators=160, min_samples_leaf=2,
                               random_state=42, n_jobs=1)


def score_rows(rows: pd.DataFrame) -> tuple[dict, dict]:
    model_scores: dict[str, dict] = {}
    for model, part in rows.groupby("Model", sort=True):
        observed = np.log(part.Latency.to_numpy(dtype=float))
        anchor = np.log(part.anchor_latency.to_numpy(dtype=float))
        score: dict[str, dict] = {"candidate_rows": len(part)}
        for method in METHODS:
            predicted = part[f"pred_{method}"].to_numpy(dtype=float)
            pred_log = np.log(predicted)
            score[method] = {
                "male": float(np.abs(pred_log - observed).mean()),
                "direction_accuracy": float((np.sign(anchor - pred_log)
                                             == np.sign(anchor - observed)).mean()),
            }
        model_scores[str(model)] = score
    summary = {}
    for method in METHODS:
        summary[method] = {
            "model_equal_male": float(np.mean([item[method]["male"]
                                               for item in model_scores.values()])),
            "row_male": float(np.abs(np.log(rows[f"pred_{method}"].to_numpy(dtype=float))
                                     - np.log(rows.Latency.to_numpy(dtype=float))).mean()),
            "row_direction_accuracy": float((
                np.sign(np.log(rows.anchor_latency) - np.log(rows[f"pred_{method}"]))
                == np.sign(np.log(rows.anchor_latency) - np.log(rows.Latency))).mean()),
        }
    return summary, model_scores


def run() -> dict:
    audit_result, source_frame = audit()
    saved_audit = json.loads((AUDIT_OUT / "audit.json").read_text())
    saved_csv = AUDIT_OUT / "eligible_vllm.csv"
    if sha256(saved_csv) != saved_audit["eligible_csv_sha256"]:
        raise ValueError("eligible snapshot hash changed")
    if (set(source_frame.source_row_index)
            != set(pd.read_csv(saved_csv).source_row_index)):
        raise ValueError("eligible snapshot rows changed")
    frame = add_anchors(source_frame)
    if (len(frame) != 1567 or frame.Model.nunique() != 9 or
            frame.is_anchor.sum() != 169 or len(frame) - frame.is_anchor.sum() != 1398):
        raise ValueError("pre-scored support counts changed")
    hardware_names = tuple(sorted(frame.Hardware.unique()))
    base = features(frame, hardware_names)
    conditioned = features(frame, hardware_names, with_anchor=True)
    labels = np.log(frame.Latency.to_numpy(dtype=float))
    predictions = []
    folds = []
    for held_model in sorted(frame.Model.unique()):
        train = (frame.Model != held_model).to_numpy()
        test_target = ((frame.Model == held_model) & ~frame.is_anchor).to_numpy()
        test_anchor = ((frame.Model == held_model) & frame.is_anchor).to_numpy()
        if train.sum() == 0 or test_target.sum() == 0:
            raise ValueError("empty held-out fold")
        direct = estimator().fit(base[train], labels[train])
        conditioned_model = estimator().fit(conditioned[train & ~frame.is_anchor.to_numpy()],
                                            labels[train & ~frame.is_anchor.to_numpy()])
        test = frame.loc[test_target].copy()
        direct_log = direct.predict(base[test_target])
        anchor_frame = frame.loc[test_anchor, WORKLOAD + ["source_row_index"]].copy()
        anchor_frame["direct_anchor_log"] = direct.predict(base[test_anchor])
        test = test.merge(anchor_frame[WORKLOAD + ["direct_anchor_log"]],
                          on=WORKLOAD, how="left", validate="many_to_one")
        if test.direct_anchor_log.isna().any():
            raise ValueError("unmatched test anchor")
        conditioned_log = conditioned_model.predict(conditioned[test_target])
        test["pred_anchor_copy"] = test.anchor_latency.to_numpy(dtype=float)
        test["pred_direct"] = np.exp(direct_log)
        test["pred_anchor_conditioned"] = np.exp(conditioned_log)
        test["pred_anchor_residual"] = np.exp(direct_log +
                                               np.log(test.anchor_latency.to_numpy(dtype=float))
                                               - test.direct_anchor_log.to_numpy(dtype=float))
        predictions.append(test)
        folds.append({"held_model": held_model, "train_models": sorted(frame.loc[train, "Model"].unique()),
                      "train_rows": int(train.sum()), "test_groups": int(test_anchor.sum()),
                      "test_candidate_rows": int(test_target.sum())})
    predicted = pd.concat(predictions, ignore_index=True).sort_values("source_row_index")
    scores, by_model = score_rows(predicted)
    candidate = scores["anchor_residual"]
    comparator_scores = {name: scores[name]["model_equal_male"]
                         for name in METHODS if name != "anchor_residual"}
    beats_by_five = all(candidate["model_equal_male"] <= 0.95 * value
                        for value in comparator_scores.values())
    wins = sum(by_model[model]["anchor_residual"]["male"]
               < by_model[model]["anchor_conditioned"]["male"]
               for model in by_model)
    direction_ok = (candidate["row_direction_accuracy"]
                    >= scores["anchor_conditioned"]["row_direction_accuracy"])
    result = {
        "status": "EXPLORATORY_GO" if beats_by_five and wins >= 6 and direction_ok
                  else "DO_NOT_ADOPT",
        "source_sha256": audit_result["source_sha256"],
        "eligible_csv_sha256": saved_audit["eligible_csv_sha256"],
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "hardware_names": hardware_names,
        "groups": 169, "candidate_rows": len(predicted), "models": 9,
        "scores": scores, "by_model": by_model, "folds": folds,
        "gate": {"beats_every_comparator_by_five_percent": beats_by_five,
                 "wins_vs_conditioned_models": wins,
                 "wins_vs_conditioned_required": 6,
                 "direction_no_worse_than_conditioned": bool(direction_ok)},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    prediction_path = OUT / "held_model_predictions.csv"
    predicted.to_csv(prediction_path, index=False)
    result["predictions_sha256"] = sha256(prediction_path)
    (OUT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    output = run()
    print(json.dumps({"status": output["status"], "scores": output["scores"],
                      "gate": output["gate"]}, sort_keys=True))
