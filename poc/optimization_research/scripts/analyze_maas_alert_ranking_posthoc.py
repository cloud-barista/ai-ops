"""Aggregate-only, posthoc alert-ranking diagnostic on the scored MaaS cohort."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/maas_day_ahead_instance_transfer_v1"
PREDICTIONS = ARTIFACT / "test_normal_predictions.parquet"
RESULT = ARTIFACT / "results.json"
OUTPUT = ARTIFACT / "posthoc_alert_ranking.json"
STRONG_PREDICTIONS = ROOT / "artifacts/maas_state_followup_v1/test_predictions.parquet"
METHODS = ("p0", "previous_day_global_exact", "previous_day_global_instance",
           "pred_new_pooled", "pred_new_state_full")
FRACTIONS = (0.01, 0.05)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_hits_with_random_ties(y: np.ndarray, score: np.ndarray, k: int) -> float:
    """Expected true positives at k when boundary ties are shuffled uniformly."""
    if not 0 <= k <= len(y) or len(y) != len(score) or not np.isfinite(score).all():
        raise ValueError("Invalid top-k inputs")
    ordered = np.argsort(-score, kind="stable")
    sorted_score = score[ordered]
    sorted_y = y[ordered]
    ends = np.r_[np.flatnonzero(np.diff(sorted_score) != 0) + 1, len(y)]
    total, start = 0.0, 0
    for end in ends:
        taken = min(max(k - start, 0), end - start)
        if taken:
            total += float(sorted_y[start:end].sum()) * taken / (end - start)
        if end >= k:
            break
        start = end
    return total


def metrics(frame: pd.DataFrame) -> dict:
    y = frame.y.to_numpy(dtype=int)
    output = {"rows": len(frame), "events": int(y.sum()),
              "instances": int(frame.instance_hash.nunique()), "methods": {}}
    for method in METHODS:
        p = frame[method].to_numpy(dtype=float)
        output["methods"][method] = {
            "average_precision": float(average_precision_score(y, p)),
            "row_brier": float(np.mean((y - p) ** 2)),
            "distinct_scores": int(np.unique(p).size),
        }
    return output


def run() -> dict:
    previous = json.loads(RESULT.read_text(encoding="utf-8"))
    prediction_sha = file_sha256(PREDICTIONS)
    if previous["status"] != "EXPLORATORY_NO_GO" or prediction_sha != previous["predictions_sha256"]:
        raise ValueError("Original failed study or saved predictions changed")
    rows = pd.read_parquet(PREDICTIONS)
    if (len(rows) != previous["test_rows"] or rows.instance_hash.nunique() != previous["test_instances"]
            or rows.duplicated(["instance_hash", "decision_s"]).any()):
        raise ValueError("Saved cohort changed")
    strong = pd.read_parquet(STRONG_PREDICTIONS)
    strong = strong.loc[strong.cohort.eq("complete") & strong.current_high.eq(0),
                        ["instance_hash", "decision_s", "y", "pred_new_pooled",
                         "pred_new_state_full"]]
    rows = rows.merge(strong, on=["instance_hash", "decision_s", "y"],
                      how="left", validate="one_to_one")
    if rows[["pred_new_pooled", "pred_new_state_full"]].isna().any().any():
        raise ValueError("Existing strong-model predictions did not pair completely")
    answer = {
        "status": "POSTHOC_DIAGNOSTIC_ONLY",
        "original_result_sha256": file_sha256(RESULT),
        "predictions_sha256": prediction_sha,
        "strong_predictions_sha256": file_sha256(STRONG_PREDICTIONS),
        "whole_cohort": metrics(rows),
        "daily": {},
        "deduplicated": {},
    }
    for day, part in rows.groupby("day", sort=True):
        day_output = metrics(part)
        day_output["budgets"] = {}
        y = part.y.to_numpy(dtype=int)
        for fraction in FRACTIONS:
            k = int(np.ceil(len(part) * fraction))
            day_output["budgets"][str(fraction)] = {
                "alerts": k,
                "methods": {method: {
                    "expected_events_random_boundary_ties": expected_hits_with_random_ties(
                        y, part[method].to_numpy(dtype=float), k),
                } for method in METHODS},
            }
        answer["daily"][str(int(day))] = day_output

    ordered = rows.sort_values(["decision_s", "instance_hash"], kind="mergesort").copy()
    ordered["hour"] = ordered.decision_s // 3600
    for name, keys in (("first_instance_hour", ["instance_hash", "hour"]),
                       ("first_instance_day", ["instance_hash", "day"])):
        subset = ordered.drop_duplicates(keys, keep="first")
        answer["deduplicated"][name] = {
            "overall": metrics(subset),
            "daily": {str(int(day)): metrics(part)
                      for day, part in subset.groupby("day", sort=True)},
        }
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != answer:
            raise ValueError("Posthoc diagnostic differs from existing result")
    else:
        OUTPUT.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return answer


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": result["status"], "prediction_sha256": result["predictions_sha256"],
                      "whole_cohort": result["whole_cohort"]}, sort_keys=True))
