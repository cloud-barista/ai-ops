"""Exploratory day-ahead calibration of a saved MaaS model on exposed ID cohorts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score

import evaluate_maas_instance_risk as original
import evaluate_maas_state_followup as state
from compare_philly_day_ahead_baselines_posthoc import exact_shift


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "artifacts/maas_state_followup_v1"
BUCKET1 = ROOT / "data/external/alibaba_maas_2026_bucket1"
BUCKET0_ROWS = STATE / "cohort_rows.parquet"
BUCKET1_RAW = BUCKET1 / "instance_week_5min_bucket1.parquet"
BUCKET1_AUDIT = BUCKET1 / "instance_week_5min_bucket1_audit.json"
BUCKET0_PRED = STATE / "test_predictions.parquet"
BUCKET1_PRED = ROOT / "artifacts/maas_existing_model_new_id_v1/test_predictions.parquet"
OUT = ROOT / "artifacts/maas_state_day_calibration_dev_v1"
RESULT = OUT / "results.json"
METHODS = ("frozen_beta", "pooled", "last_beta", "global_exact", "state_exact", "model_last")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matured_previous_day(rows: pd.DataFrame, day: int) -> pd.DataFrame:
    """Only labels fully available by 00:00 of target UTC day."""
    return rows.loc[rows.day.eq(day - 1) & rows.decision_s.add(2100).le(day * 86400)]


def rate_by_recent_state(rows: pd.DataFrame) -> dict[int, float]:
    rates = {int(h): float((group.y.sum() + 1) / (len(group) + 2))
             for h, group in rows.groupby("any_recent_high")}
    if set(rates) != {0, 1}:
        raise ValueError("both previous-day recent states are required")
    return rates


def predict_day(today: pd.DataFrame, previous: pd.DataFrame,
                train_beta: dict[int, float]) -> tuple[pd.DataFrame, dict]:
    if previous.empty or previous.decision_s.max() + 2100 > today.day.iloc[0] * 86400:
        raise ValueError("unmatured day-ahead label")
    out = today[["instance_hash", "decision_s", "day", "y", "any_recent_high"]].copy()
    out["pooled"] = today.pooled.to_numpy(float)
    h = out.any_recent_high.to_numpy(int)
    base = np.where(h == 1, train_beta[1], train_beta[0])
    rates = rate_by_recent_state(previous)
    prior_rate = np.where(h == 1, rates[1], rates[0])
    out["frozen_beta"] = base
    out["last_beta"] = prior_rate
    z = logit(np.clip(out.pooled.to_numpy(float), 1e-9, 1 - 1e-9))
    out["model_last"] = expit(z + logit(prior_rate) - logit(base))
    global_shift = exact_shift(previous.y.to_numpy(float), previous.pooled.to_numpy(float))
    out["global_exact"] = expit(z + global_shift)
    state_shifts = {int(state_id): exact_shift(group.y.to_numpy(float), group.pooled.to_numpy(float))
                    for state_id, group in previous.groupby("any_recent_high")}
    out["state_exact"] = expit(z + np.where(h == 1, state_shifts[1], state_shifts[0]))
    return out, {"previous_day_mature_rows": len(previous),
                 "previous_day_rates": {str(k): v for k, v in rates.items()},
                 "global_exact_shift": global_shift,
                 "state_exact_shifts": {str(k): v for k, v in state_shifts.items()}}


def scores(rows: pd.DataFrame) -> dict:
    y = rows.y.to_numpy(dtype=int)
    ids = rows.instance_hash.to_numpy(str)
    output = {"rows": len(rows), "events": int(y.sum()), "ids": len(np.unique(ids)), "methods": {}}
    for name in METHODS:
        p = rows[name].to_numpy(float)
        squared = (y - p) ** 2
        output["methods"][name] = {
            "brier": float(squared.mean()),
            "id_equal_brier": float(pd.Series(squared).groupby(ids).mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
        }
    return output


def bootstrap(rows: pd.DataFrame, comparator: str, *, seed: int = 43) -> dict:
    y = rows.y.to_numpy(float)
    p = rows.model_last.to_numpy(float)
    q = rows[comparator].to_numpy(float)
    group = pd.DataFrame({"id": rows.instance_hash, "n": 1,
                          "delta": (y - p) ** 2 - (y - q) ** 2}).groupby("id").agg(
        n=("n", "sum"), delta=("delta", "sum"))
    n = group.n.to_numpy(int)
    delta = group.delta.to_numpy(float)
    rng = np.random.default_rng(seed)
    samples = np.empty(2000)
    for i in range(len(samples)):
        take = rng.integers(0, len(n), len(n))
        samples[i] = delta[take].sum() / n[take].sum()
    return {"point": float(delta.sum() / n.sum()),
            "percentile_95": [float(x) for x in np.percentile(samples, [2.5, 97.5])],
            "seed": seed, "draws": len(samples), "ids": len(n)}


def load_cohort(bucket: str, model: object, medians: dict[str, float]) -> pd.DataFrame:
    if bucket == "bucket0":
        rows = pd.read_parquet(BUCKET0_ROWS)
        saved = pd.read_parquet(BUCKET0_PRED)
        saved = saved.loc[saved.cohort.eq("complete") & saved.current_high.eq(0),
                          ["instance_hash", "decision_s", "y", "pred_new_pooled"]]
        prediction_name = "pred_new_pooled"
    else:
        audit = json.loads(BUCKET1_AUDIT.read_text(encoding="utf-8"))
        if (not audit["integrity_pass"] or audit["compact_sha256"] != sha256(BUCKET1_RAW)):
            raise ValueError("bucket1 source differs from verified extraction")
        rows, _ = state.construct_cohorts(original.load_compact(BUCKET1_RAW))
        saved = pd.read_parquet(BUCKET1_PRED)
        saved = saved[["instance_hash", "decision_s", "y", "pooled"]].rename(
            columns={"pooled": "saved_pooled"})
        prediction_name = "saved_pooled"
    rows = rows.loc[rows.cohort.eq("complete") & rows.current_high.eq(0)
                    & rows.day.between(144, 146)].copy()
    featured = state.make_features(rows, medians)
    featured["pooled"] = model.predict_proba(featured[list(state.FULL_FEATURES)])[:, 1]
    test = featured.loc[featured.day.ge(145)]
    paired = test[["instance_hash", "decision_s", "y", "pooled"]].merge(
        saved, on=["instance_hash", "decision_s", "y"], how="left", validate="one_to_one")
    if (len(paired) != len(saved) or paired[prediction_name].isna().any()
            or np.max(np.abs(paired.pooled - paired[prediction_name])) > 1e-12):
        raise ValueError("saved model predictions did not match exposed cohort")
    return featured


def run() -> dict:
    state_metrics = json.loads((STATE / "metrics.json").read_text(encoding="utf-8"))
    if (state_metrics["fitted_models"]["new_pooled"]["sha256"]
            != sha256(STATE / "new_pooled.joblib")):
        raise ValueError("pooled model changed")
    model = joblib.load(STATE / "new_pooled.joblib")
    medians = state_metrics["train_only_imputation_medians"]
    train_beta = {0: 0.033061253175105165, 1: 0.7215373459028281}
    output = {"status": "EXPOSED_COHORT_DEVELOPMENT_ONLY",
              "model_sha256": sha256(STATE / "new_pooled.joblib"),
              "bucket0_rows_sha256": sha256(BUCKET0_ROWS),
              "bucket1_raw_sha256": sha256(BUCKET1_RAW),
              "script_sha256": sha256(Path(__file__)), "cohorts": {}}
    for bucket in ("bucket0", "bucket1"):
        featured = load_cohort(bucket, model, medians)
        daily = []
        anchors = {}
        for day in (145, 146):
            today = featured.loc[featured.day.eq(day)]
            previous = matured_previous_day(featured, day)
            predictions, info = predict_day(today, previous, train_beta)
            daily.append(predictions)
            anchors[str(day)] = info
        scored = pd.concat(daily, ignore_index=True)
        overall = scores(scored)
        by_day = {str(int(day)): scores(group) for day, group in scored.groupby("day")}
        boot = {name: bootstrap(scored, name) for name in ("pooled", "global_exact", "last_beta")}
        output["cohorts"][bucket] = {"overall": overall, "by_day": by_day,
                                     "matured_prior": anchors, "id_bootstrap": boot}
    OUT.mkdir(parents=True, exist_ok=True)
    if RESULT.exists():
        if json.loads(RESULT.read_text(encoding="utf-8")) != output:
            raise ValueError("development artifact changed")
    else:
        RESULT.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    result = run()
    print(json.dumps({name: {m: x["brier"] for m, x in group["overall"]["methods"].items()}
                      for name, group in result["cohorts"].items()}, sort_keys=True))
