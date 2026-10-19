"""Post-hoc day-ahead GPU-risk calibration on two already scored Philly cohorts.

This analysis cannot validate a newly chosen method. It uses exposed labels only
to decide whether the candidate warrants a separate, unused-machine test.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "first_new_machine_holdout": (
        ROOT / "artifacts/philly_machine_holdout_score_v1/test_predictions.parquet",
        "3907dcfde07f7127157ac5c4a5705df7d1790e11a197b726aa66aed06f3aed6c",
        "pooled_quality"),
    "second_new_machine_holdout": (
        ROOT / "artifacts/philly_machine_calibration_v1/test_predictions.parquet",
        "5e570375ed7bd8639f38ba75301c34880e9772fb62b9cc7b580d8e6e42fe375b",
        "pooled"),
}
OUTPUT = ROOT / "artifacts/philly_day_ahead_calibration_exploratory_v1/summary.json"
PENALTY = 20.0
MAX_ABS_LOGIT_SHIFT = .5
OUTCOME_HORIZON_SECONDS = 1800


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_cohort(path: Path, expected_sha: str, probability_column: str) -> pd.DataFrame:
    if file_sha256(path) != expected_sha:
        raise ValueError(f"exposed Philly predictions changed: {path}")
    columns = ["machine_hash", "gpu_index", "decision_utc_s", "utc_day",
               "cohort", "current_state", "y", probability_column]
    frame = pd.read_parquet(path, columns=columns)
    frame = frame.loc[(frame.cohort == "complete") &
                      (frame.current_state == "below_80"),
                      ["machine_hash", "gpu_index", "decision_utc_s",
                       "utc_day", "y", probability_column]].copy()
    frame = frame.rename(columns={probability_column: "p0"})
    frame = frame.sort_values(["decision_utc_s", "machine_hash", "gpu_index"],
                              kind="mergesort").reset_index(drop=True)
    if (frame.empty or frame[["machine_hash", "gpu_index", "decision_utc_s"]]
            .duplicated().any() or not frame.y.isin([0, 1]).all()
            or not np.isfinite(frame.p0).all()
            or not frame.p0.between(0, 1).all()
            or not (frame.utc_day == frame.decision_utc_s // 86400).all()):
        raise ValueError("invalid exposed Philly decision rows")
    return frame


def shift_probability(probability: np.ndarray, delta: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probability, dtype=float), 1e-9, 1-1e-9)
    logit = np.log(clipped/(1-clipped))
    return 1/(1+np.exp(-(logit+delta)))


def predict_day_ahead(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """Use only one hourly label/GPU from the *completed* preceding UTC day."""
    ordered = frame.sort_values(["decision_utc_s", "machine_hash", "gpu_index"],
                                kind="mergesort").reset_index(drop=True)
    if not frame.reset_index(drop=True).equals(ordered):
        raise ValueError("day-ahead input must be sorted")
    p0 = frame.p0.to_numpy(float)
    machine_prediction = p0.copy()
    global_prediction = p0.copy()
    sampled = frame.assign(utc_hour=frame.decision_utc_s // 3600).drop_duplicates(
        ["machine_hash", "gpu_index", "utc_hour"], keep="first")
    days = sorted(int(day) for day in frame.utc_day.unique())
    daily = {}
    for day in days:
        positions = (frame.utc_day == day).to_numpy()
        previous = sampled.loc[(sampled.utc_day == day-1) &
                               (sampled.decision_utc_s + OUTCOME_HORIZON_SECONDS
                                <= day*86400)].copy()
        if previous.empty:
            daily[str(day)] = {"calibration_rows": 0,
                               "corrected_test_rows": 0,
                               "machine_count_with_history": 0,
                               "global_logit_shift": 0.}
            continue
        previous["residual"] = previous.y - previous.p0
        previous["variance"] = previous.p0*(1-previous.p0)
        by_machine = previous.groupby("machine_hash")[["residual", "variance"]].sum()
        shift = (by_machine.residual/(by_machine.variance + PENALTY)).clip(
            -MAX_ABS_LOGIT_SHIFT, MAX_ABS_LOGIT_SHIFT)
        test_machine = frame.loc[positions, "machine_hash"]
        machine_shift = test_machine.map(shift).fillna(0.).to_numpy(float)
        global_shift = float(np.clip(
            previous.residual.sum()/(previous.variance.sum()+PENALTY),
            -MAX_ABS_LOGIT_SHIFT, MAX_ABS_LOGIT_SHIFT))
        machine_prediction[positions] = shift_probability(p0[positions], machine_shift)
        global_prediction[positions] = shift_probability(
            p0[positions], np.full(positions.sum(), global_shift))
        daily[str(day)] = {
            "calibration_rows": int(len(previous)),
            "corrected_test_rows": int(np.count_nonzero(machine_shift)),
            "machine_count_with_history": int(len(shift)),
            "global_logit_shift": global_shift,
        }
    return machine_prediction, global_prediction, daily


def metrics(frame: pd.DataFrame, probabilities: dict[str, np.ndarray]) -> dict:
    y = frame.y.to_numpy(float)
    machine_id = frame.machine_hash.to_numpy(str)
    dates = sorted(int(day) for day in frame.utc_day.unique())
    mid = dates[7]
    result = {}
    for name, estimate in probabilities.items():
        row_brier = (y-estimate)**2
        machine_brier = pd.Series(row_brier).groupby(machine_id).mean()
        result[name] = {
            "row_brier": float(row_brier.mean()),
            "machine_equal_brier": float(machine_brier.mean()),
            "average_precision": float(average_precision_score(y, estimate)),
            "first_seven_days_row_brier": float(row_brier[frame.utc_day < mid].mean()),
            "last_eight_days_row_brier": float(row_brier[frame.utc_day >= mid].mean()),
        }
    p0_err=(y-probabilities["frozen_pooled"])**2
    adapted_err=(y-probabilities["previous_day_machine"])**2
    by_machine = pd.DataFrame({"machine": machine_id,
                               "base": p0_err,
                               "adapted": adapted_err}).groupby("machine").mean()
    result["machine_win_count"] = int((by_machine.adapted < by_machine.base).sum())
    result["machine_count"] = int(len(by_machine))
    return result


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    cohorts = {}
    for name, (path, digest, field) in SOURCES.items():
        frame = load_cohort(path, digest, field)
        machine, global_, daily = predict_day_ahead(frame)
        scores = metrics(frame, {"frozen_pooled": frame.p0.to_numpy(float),
                                 "previous_day_global": global_,
                                 "previous_day_machine": machine})
        cohorts[name] = {"decision_rows": int(len(frame)),
                         "machine_count": int(frame.machine_hash.nunique()),
                         "days": int(frame.utc_day.nunique()),
                         "source_sha256": digest,
                         "scores": scores, "calibration_support_by_day": daily}
    result = {"status": "POST_HOC_EXPLORATORY_ONLY",
              "script_sha256": file_sha256(Path(__file__)),
              "method": {"sample": "first eligible decision per machine/GPU/UTC hour",
                         "calibration": "previous UTC day labels with full 30min outcome before current day",
                         "formula": "clip(sum(y-p0)/(sum(p0*(1-p0))+20),-.5,.5) added to p0 logit",
                         "fallback": "p0 if no prior-day labels"},
              "cohorts": cohorts,
              "interpretation_limit": (
                  "Both machine cohorts had exposed labels before this method was chosen. "
                  "Use a disjoint machine cohort for an independent check.")}
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({name: cohort["scores"] for name, cohort in cohorts.items()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
