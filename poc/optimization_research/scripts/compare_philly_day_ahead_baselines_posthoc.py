"""Challenge the scored Philly day-ahead method with simple causal baselines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/philly_day_ahead_baselines_posthoc_v1/summary.json"
COHORTS = {
    "machine_bucket_5_15": (ROOT / "artifacts/philly_machine_holdout_score_v1/test_predictions.parquet",
                            "3907dcfde07f7127157ac5c4a5705df7d1790e11a197b726aa66aed06f3aed6c",
                            "pooled_quality"),
    "machine_bucket_15_25": (ROOT / "artifacts/philly_machine_calibration_v1/test_predictions.parquet",
                             "5e570375ed7bd8639f38ba75301c34880e9772fb62b9cc7b580d8e6e42fe375b",
                             "pooled"),
    "machine_bucket_25_35": (ROOT / "artifacts/philly_day_ahead_calibration_holdout_v1/test_predictions.parquet",
                             "fa0340ebc60fb8cb2acbcad3494cd856deee614a0ea1be78f5c72cacb301d356",
                             "frozen_pooled"),
}
METHODS = ("p0", "previous_day_newton", "previous_day_exact",
           "previous_two_days_newton", "previous_day_rate")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def load(path: Path, expected: str, probability: str) -> pd.DataFrame:
    if sha256(path) != expected:
        raise ValueError(f"source predictions changed: {path}")
    fields = ["machine_hash", "gpu_index", "decision_utc_s", "utc_day", "y", probability]
    if probability != "frozen_pooled":
        fields.extend(["cohort", "current_state"])
    frame = pd.read_parquet(path, columns=fields)
    if probability != "frozen_pooled":
        frame = frame.loc[frame.cohort.eq("complete") & frame.current_state.eq("below_80")]
    frame = frame.rename(columns={probability: "p0"})[
        ["machine_hash", "gpu_index", "decision_utc_s", "utc_day", "y", "p0"]]
    frame = frame.sort_values(["decision_utc_s", "machine_hash", "gpu_index"],
                              kind="mergesort").reset_index(drop=True)
    if (frame.empty or frame[["machine_hash", "gpu_index", "decision_utc_s"]].duplicated().any()
            or not frame.y.isin([0, 1]).all()
            or not (frame.utc_day == frame.decision_utc_s // 86400).all()
            or not np.isfinite(frame.p0).all()
            or not frame.p0.between(0, 1).all()):
        raise ValueError("invalid cohort")
    return frame


def exact_shift(y: np.ndarray, p: np.ndarray) -> float:
    z = logit(np.clip(p, 1e-9, 1 - 1e-9))
    def derivative(delta: float) -> float:
        return float(np.sum(y - expit(z + delta)) - 20.0 * delta)
    if derivative(-.5) <= 0:
        return -.5
    if derivative(.5) >= 0:
        return .5
    return float(brentq(derivative, -.5, .5, xtol=1e-12))


def predict(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    base = frame.p0.to_numpy(float)
    output = {name: base.copy() for name in METHODS}
    hourly = frame.assign(hour=frame.decision_utc_s // 3600).drop_duplicates(
        ["machine_hash", "gpu_index", "hour"], keep="first")
    days = sorted(int(x) for x in frame.utc_day.unique())
    for day in days:
        today = frame.utc_day.eq(day).to_numpy()
        eligible = hourly.loc[hourly.utc_day.between(day - 2, day - 1)
                              & hourly.decision_utc_s.add(1800).le(day * 86400)]
        if eligible.empty:
            continue
        by_day = eligible.loc[eligible.utc_day.eq(day - 1)]
        shifts = {name: {} for name in METHODS if name != "p0"}
        for machine, one in eligible.groupby("machine_hash"):
            yesterday = by_day.loc[by_day.machine_hash.eq(machine)]
            if not yesterday.empty:
                p = yesterday.p0.to_numpy(float)
                y = yesterday.y.to_numpy(float)
                delta = np.clip((y - p).sum() / ((p * (1 - p)).sum() + 20), -.5, .5)
                shifts["previous_day_newton"][machine] = float(delta)
                shifts["previous_day_exact"][machine] = exact_shift(y, p)
                # A machine's previous-day prevalence, smoothed toward its p0 mean.
                shifts["previous_day_rate"][machine] = float((y.sum() + 20 * p.mean())
                                                               / (len(y) + 20))
            p2 = one.p0.to_numpy(float)
            y2 = one.y.to_numpy(float)
            shifts["previous_two_days_newton"][machine] = float(np.clip(
                (y2 - p2).sum() / ((p2 * (1 - p2)).sum() + 20), -.5, .5))
        today_machine = frame.loc[today, "machine_hash"]
        z = logit(np.clip(base[today], 1e-9, 1 - 1e-9))
        for name in ("previous_day_newton", "previous_day_exact", "previous_two_days_newton"):
            delta = today_machine.map(shifts[name]).fillna(0).to_numpy(float)
            output[name][today] = expit(z + delta)
        rate = today_machine.map(shifts["previous_day_rate"])
        output["previous_day_rate"][today] = rate.fillna(pd.Series(base[today],
                                                 index=rate.index)).to_numpy(float)
    return output


def assess(frame: pd.DataFrame, predictions: dict[str, np.ndarray]) -> dict:
    y = frame.y.to_numpy(float)
    daily = frame.utc_day.to_numpy(int)
    machine = frame.machine_hash.to_numpy(str)
    mid = sorted(np.unique(daily))[7]
    metrics = {}
    errors = {}
    for name, p in predictions.items():
        loss = (y - p)**2
        errors[name] = loss
        metrics[name] = {
            "row_brier": float(loss.mean()),
            "machine_equal_brier": float(pd.Series(loss).groupby(machine).mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
            "first_seven_days_brier": float(loss[daily < mid].mean()),
            "last_eight_days_brier": float(loss[daily >= mid].mean()),
        }
    for name in METHODS[1:]:
        daily_pair = pd.DataFrame({"utc_day": daily, "candidate": errors[name],
                                   "p0": errors["p0"],
                                   "current": errors["previous_day_newton"]}).groupby("utc_day").mean()
        metrics[name]["days_better_than_p0"] = int((daily_pair.candidate < daily_pair.p0).sum())
        if name != "previous_day_newton":
            metrics[name]["days_better_than_current"] = int(
                (daily_pair.candidate < daily_pair.current).sum())
    return metrics


def run() -> dict:
    result = {"status": "POST_HOC_EXPOSED_MACHINE_BASELINE_CHALLENGE",
              "scope": "three previously scored cohorts; no new independent validation",
              "methods": {
                  "previous_day_newton": "frozen one-Newton-step logit shift, penalty 20, clip ±0.5",
                  "previous_day_exact": "exact penalized logistic intercept with same penalty and clip",
                  "previous_two_days_newton": "same Newton formula using two prior completed UTC days",
                  "previous_day_rate": "previous-day machine y rate with 20 pseudo-counts at previous-day mean p0",
              }, "cohorts": {}}
    for name, (path, expected, column) in COHORTS.items():
        frame = load(path, expected, column)
        predictions = predict(frame)
        metrics = assess(frame, predictions)
        if name == "machine_bucket_25_35":
            saved = pd.read_parquet(path, columns=["previous_day_machine"])
            # The same input was time sorted before the new one-time score was saved.
            if np.max(np.abs(predictions["previous_day_newton"] -
                             saved.previous_day_machine.to_numpy(float))) > 1e-12:
                raise ValueError("new-machine frozen forecast not reproduced")
        result["cohorts"][name] = {"predictions_sha256": expected,
                                    "rows": len(frame),
                                    "machines": int(frame.machine_hash.nunique()),
                                    "metrics": metrics}
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("saved baseline challenge differs from fresh replay")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=False)
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps({name: value["metrics"] for name, value in result["cohorts"].items()},
                     ensure_ascii=False, indent=2))
