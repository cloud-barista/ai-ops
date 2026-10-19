"""Read-only, independent replay of saved Philly day-ahead holdout scores."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/philly_day_ahead_calibration_holdout_v1"
PREDICTIONS = ARTIFACT / "test_predictions.parquet"
RESULT = ARTIFACT / "result.json"
SUPPORT = ARTIFACT / "support_audit.json"
FREEZE = ARTIFACT / "freeze.json"
AUDIT = ARTIFACT / "independent_reproduction.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def same(actual: float, recorded: float, name: str) -> None:
    if not math.isclose(actual, recorded, rel_tol=0, abs_tol=1e-12):
        raise ValueError(f"{name}: independent {actual} != recorded {recorded}")


def run() -> dict:
    recorded = json.loads(RESULT.read_text(encoding="utf-8"))
    support = json.loads(SUPPORT.read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if (recorded["predictions_sha256"] != digest(PREDICTIONS)
            or recorded["support_sha256"] != digest(SUPPORT)
            or recorded["freeze_sha256"] != digest(FREEZE)
            or support["freeze_sha256"] != digest(FREEZE)
            or support["status"] != "SCORE_FREE_SUPPORT_PASS"
            or freeze["status"] != "FROZEN_BEFORE_BUCKET_25_35_COLLECTION"):
        raise ValueError("saved holdout provenance mismatch")
    rows = pd.read_parquet(PREDICTIONS)
    if (len(rows) != recorded["test_rows"]
            or rows.machine_hash.nunique() != recorded["test_machines"]
            or len(rows) != support["support"]["complete_decisions"]
            or not rows.y.isin([0, 1]).all()
            or not (rows.utc_day == rows.decision_utc_s // 86400).all()
            or sorted(rows.utc_day.unique().tolist()) != list(range(17501, 17516))
            or rows[["machine_hash", "gpu_index", "decision_utc_s"]].duplicated().any()):
        raise ValueError("saved decision support mismatch")
    rows = rows.sort_values(["decision_utc_s", "machine_hash", "gpu_index"],
                            kind="mergesort").reset_index(drop=True)
    y = rows.y.to_numpy(float)
    base = rows.frozen_pooled.to_numpy(float)
    if not (np.isfinite(base).all() and ((base > 0) & (base < 1)).all()):
        raise ValueError("invalid original probability")
    sampled = rows.assign(utc_hour=rows.decision_utc_s // 3600).drop_duplicates(
        ["machine_hash", "gpu_index", "utc_hour"], keep="first")
    replay_machine = base.copy()
    replay_global = base.copy()
    daily = {}
    for day in range(17501, 17516):
        today = rows.utc_day.eq(day).to_numpy()
        previous = sampled.loc[sampled.utc_day.eq(day - 1)
                               & sampled.decision_utc_s.add(1800).le(day * 86400)]
        if previous.empty:
            daily[str(day)] = {"calibration_rows": 0, "corrected_test_rows": 0,
                               "machine_count_with_history": 0,
                               "global_logit_shift": 0.0}
            continue
        residual = previous.y.to_numpy(float) - previous.frozen_pooled.to_numpy(float)
        variance = (previous.frozen_pooled.to_numpy(float)
                    * (1 - previous.frozen_pooled.to_numpy(float)))
        sums = pd.DataFrame({"machine_hash": previous.machine_hash.to_numpy(),
                             "residual": residual, "variance": variance}).groupby(
                                 "machine_hash").sum()
        shifts = (sums.residual / (sums.variance + 20.0)).clip(-0.5, 0.5)
        local = rows.loc[today, "machine_hash"].map(shifts).fillna(0).to_numpy(float)
        global_shift = float(np.clip(residual.sum() / (variance.sum() + 20.0),
                                     -0.5, 0.5))
        odds_log = np.log(base[today] / (1 - base[today]))
        replay_machine[today] = 1 / (1 + np.exp(-(odds_log + local)))
        replay_global[today] = 1 / (1 + np.exp(-(odds_log + global_shift)))
        daily[str(day)] = {"calibration_rows": int(len(previous)),
                           "corrected_test_rows": int(np.count_nonzero(local)),
                           "machine_count_with_history": int(len(shifts)),
                           "global_logit_shift": global_shift}
    max_abs = {}
    for name, derived in (("previous_day_machine", replay_machine),
                          ("previous_day_global", replay_global)):
        difference = float(np.max(np.abs(derived - rows[name].to_numpy(float))))
        if difference > 1e-12:
            raise ValueError(f"saved {name} differs from past-day-only replay: {difference}")
        max_abs[name] = difference
    for day, values in daily.items():
        expected = recorded["calibration_support_by_day"][day]
        for key, value in values.items():
            if isinstance(value, float):
                same(value, expected[key], f"day {day} {key}")
            elif value != expected[key]:
                raise ValueError(f"day {day} {key} differs")
    by_machine = {}
    metrics = {}
    for name in ("frozen_pooled", "previous_day_global", "previous_day_machine"):
        prediction = rows[name].to_numpy(float)
        error = (y - prediction) ** 2
        group = pd.DataFrame({"machine_hash": rows.machine_hash, "error": error}).groupby(
            "machine_hash").error.mean()
        by_machine[name] = group
        measured = {"row_brier": float(error.mean()),
                    "machine_equal_brier": float(group.mean()),
                    "average_precision": float(average_precision_score(y, prediction)),
                    "first_seven_days_row_brier": float(error[rows.utc_day.lt(17508)].mean()),
                    "last_eight_days_row_brier": float(error[rows.utc_day.ge(17508)].mean())}
        for key, value in measured.items():
            same(value, recorded["assessment"]["methods"][name][key], f"{name}.{key}")
        metrics[name] = measured
    wins = int((by_machine["previous_day_machine"] < by_machine["frozen_pooled"]).sum())
    if wins != recorded["assessment"]["methods"]["machine_win_count"]:
        raise ValueError("machine improvement count differs")
    errors = pd.DataFrame({"machine_hash": rows.machine_hash,
                           "base": (y - base) ** 2,
                           "adapted": (y - replay_machine) ** 2}).groupby(
                               "machine_hash").agg(n=("base", "size"),
                                                   base_sum=("base", "sum"),
                                                   adapted_sum=("adapted", "sum"))
    counts = errors.n.to_numpy(float)
    differences = (errors.adapted_sum - errors.base_sum).to_numpy(float)
    draw = np.random.default_rng(43).integers(0, len(counts), size=(2000, len(counts)))
    bootstrap = differences[draw].sum(axis=1) / counts[draw].sum(axis=1)
    interval = np.quantile(bootstrap, [.025, .975])
    saved_boot = recorded["assessment"]["machine_bootstrap"]
    same(float(differences.sum() / counts.sum()),
         saved_boot["candidate_minus_base_row_brier"], "bootstrap point")
    for index in range(2):
        same(float(interval[index]), saved_boot["percentile_95"][index],
             f"bootstrap interval {index}")
    p0 = metrics["frozen_pooled"]
    global_ = metrics["previous_day_global"]
    local = metrics["previous_day_machine"]
    checks = {
        "row_brier_3pct_below_frozen_pooled": local["row_brier"] <= .97 * p0["row_brier"],
        "row_brier_1pct_below_global_day_ahead": local["row_brier"] <= .99 * global_["row_brier"],
        "machine_equal_brier_below_both": local["machine_equal_brier"] < min(
            p0["machine_equal_brier"], global_["machine_equal_brier"]),
        "both_calendar_halves_better_than_frozen": (
            local["first_seven_days_row_brier"] < p0["first_seven_days_row_brier"]
            and local["last_eight_days_row_brier"] < p0["last_eight_days_row_brier"]),
        "at_least_60pct_machines_improve": wins >= math.ceil(.6 * len(counts)),
        "average_precision_not_worse_than_frozen": local["average_precision"] >= p0["average_precision"],
        "machine_bootstrap_upper_below_zero": float(interval[1]) < 0,
    }
    if checks != recorded["assessment"]["gate"]["checks"] or not all(checks.values()):
        raise ValueError("independent gate differs or failed")
    audit = {"status": "INDEPENDENT_SAVED_PREDICTION_REPLAY_PASS",
             "scope": "saved decision rows and forecasts; raw collection is checked by frozen evaluator",
             "predictions_sha256": digest(PREDICTIONS),
             "rows": int(len(rows)), "machines": int(len(counts)),
             "max_abs_forecast_difference": max_abs,
             "metrics": metrics, "improved_machines": wins,
             "bootstrap_95": [float(x) for x in interval],
             "checks": checks}
    if AUDIT.exists():
        if json.loads(AUDIT.read_text(encoding="utf-8")) != audit:
            raise ValueError("stored independent reproduction differs from fresh replay")
    else:
        AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
