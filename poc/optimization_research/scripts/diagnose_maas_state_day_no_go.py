"""Post-hoc decomposition of the already exposed MaaS ID buckets.

This script does not change the frozen new-ID trial or select a deployable model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

import explore_maas_state_day_calibration as development


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "artifacts/maas_state_day_calibration_holdout_v1"
OUTPUT = ROOT / "artifacts/maas_state_day_no_go_diagnostic_v1/results.json"
METHODS = ("pooled", "global_exact", "last_beta", "state_exact", "model_last", "high_mix50")
TRAIN_BETA = {0: 0.033061253175105165, 1: 0.7215373459028281}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def posthoc_metrics(rows: pd.DataFrame) -> dict:
    y = rows.y.to_numpy(float)
    answer = {"rows": len(rows), "events": int(y.sum()),
              "ids": int(rows.instance_hash.nunique()), "brier": {}}
    for name in METHODS:
        p = rows[name].to_numpy(float)
        answer["brier"][name] = float(np.mean((y - p) ** 2))
    answer["high_mix50_ap"] = float(average_precision_score(y, rows.high_mix50))
    return answer


def add_posthoc_mix(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    out["high_mix50"] = out.model_last.to_numpy(float)
    high = out.any_recent_high.eq(1)
    out.loc[high, "high_mix50"] = .5 * out.loc[high, "model_last"].to_numpy(float) + .5 * (
        out.loc[high, "last_beta"].to_numpy(float))
    return out


def make_exposed_predictions(bucket: str, model: object, medians: dict) -> pd.DataFrame:
    featured = development.load_cohort(bucket, model, medians)
    daily = []
    for day in (145, 146):
        today = featured.loc[featured.day.eq(day)]
        previous = development.matured_previous_day(featured, day)
        predicted, _ = development.predict_day(today, previous, TRAIN_BETA)
        daily.append(predicted)
    return pd.concat(daily, ignore_index=True)


def id_gain_summary(rows: pd.DataFrame) -> dict:
    y = rows.y.to_numpy(float)
    gain = (y - rows.pooled.to_numpy(float)) ** 2 - (
        y - rows.model_last.to_numpy(float)) ** 2
    by_id = pd.DataFrame({"id": rows.instance_hash.to_numpy(str), "gain": gain}).groupby("id").gain.sum()
    by_state = pd.DataFrame({"state": rows.any_recent_high.to_numpy(int),
                             "gain": gain}).groupby("state").gain.sum()
    total = float(gain.sum())
    return {"id_wins": int((by_id > 0).sum()), "id_losses": int((by_id < 0).sum()),
            "total_squared_error_gain": total,
            "gain_by_recent_high_state": {str(int(k)): float(v) for k, v in by_state.items()},
            "high_state_share_of_net_gain": float(by_state[1] / total),
            "top_10_positive_id_gain_sum": float(by_id.nlargest(10).sum()),
            "top_10_absolute_id_gain_share": float(by_id.abs().nlargest(10).sum() /
                                                   by_id.abs().sum())}


def run() -> dict:
    holdout = json.loads((HOLDOUT / "results.json").read_text(encoding="utf-8"))
    if holdout["status"] != "NEW_ID_STATE_DAY_NO_GO":
        raise ValueError("holdout is not a recorded NO-GO")
    pred_path = HOLDOUT / "test_predictions.parquet"
    if holdout["predictions_sha256"] != sha256(pred_path):
        raise ValueError("holdout predictions differ from frozen score")
    development_result = json.loads(development.RESULT.read_text(encoding="utf-8"))
    state_metrics = json.loads((development.STATE / "metrics.json").read_text(encoding="utf-8"))
    model = joblib.load(development.STATE / "new_pooled.joblib")
    buckets = {}
    for bucket in ("bucket0", "bucket1", "bucket2"):
        if bucket == "bucket2":
            rows = pd.read_parquet(pred_path)
            expected = holdout["overall"]
        else:
            rows = make_exposed_predictions(bucket, model,
                                            state_metrics["train_only_imputation_medians"])
            expected = development_result["cohorts"][bucket]["overall"]
        fixed_score = development.scores(rows)
        if (fixed_score["rows"] != expected["rows"]
                or fixed_score["events"] != expected["events"]
                or any(not np.isclose(fixed_score["methods"][name]["brier"],
                                       expected["methods"][name]["brier"], atol=1e-12, rtol=0)
                       for name in development.METHODS)):
            raise ValueError(f"{bucket} saved score was not reproduced")
        rows = add_posthoc_mix(rows)
        overall = posthoc_metrics(rows)
        by_day_state = {f"{int(day)}_{int(state)}": posthoc_metrics(group)
                        for (day, state), group in rows.groupby(["day", "any_recent_high"])}
        mix_boot_rows = rows.copy()
        mix_boot_rows["model_last"] = rows.high_mix50
        buckets[bucket] = {
            "overall": overall, "by_day_recent_high_state": by_day_state,
            "model_last_vs_pooled": id_gain_summary(rows),
            "posthoc_mix50_vs_pooled_id_bootstrap": development.bootstrap(
                mix_boot_rows, "pooled"),
            "posthoc_mix50_relative_brier_gain_vs_pooled": float(
                1 - overall["brier"]["high_mix50"] / overall["brier"]["pooled"]),
        }
    answer = {
        "status": "EXPOSED_COHORT_POSTHOC_ONLY",
        "selection_warning": "The 50% high-state mix was inspected after all three bucket outcomes; no independent acceptance inference.",
        "holdout_result_sha256": sha256(HOLDOUT / "results.json"),
        "holdout_predictions_sha256": sha256(pred_path),
        "development_result_sha256": sha256(development.RESULT),
        "buckets": buckets,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != answer:
            raise ValueError("post-hoc diagnostic artifact changed")
    else:
        OUTPUT.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return answer


if __name__ == "__main__":
    result = run()
    for bucket, one in result["buckets"].items():
        print(bucket, one["overall"]["rows"],
              one["posthoc_mix50_relative_brier_gain_vs_pooled"],
              one["posthoc_mix50_vs_pooled_id_bootstrap"]["percentile_95"])
