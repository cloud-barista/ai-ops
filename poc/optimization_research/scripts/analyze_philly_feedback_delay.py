"""Post-hoc sensitivity of Philly day-ahead calibration to label delivery delay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/philly_day_ahead_calibration_holdout_v1"
PREDICTIONS = ARTIFACT / "test_predictions.parquet"
RESULT = ARTIFACT / "result.json"
OUTPUT = ARTIFACT / "feedback_delay_sensitivity_exploratory.json"
DELAYS_SECONDS = (0, 300, 3600, 21600, 43200, 86400)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def forecast(frame: pd.DataFrame, delay_seconds: int) -> tuple[np.ndarray, dict]:
    baseline = frame.frozen_pooled.to_numpy(float)
    estimate = baseline.copy()
    hourly = frame.assign(hour=frame.decision_utc_s // 3600).drop_duplicates(
        ["machine_hash", "gpu_index", "hour"], keep="first")
    counts = {}
    for day in range(17501, 17516):
        today = frame.utc_day.eq(day).to_numpy()
        prior = hourly.loc[hourly.utc_day.eq(day - 1) &
                           hourly.decision_utc_s.add(1800 + delay_seconds).le(day * 86400)]
        counts[str(day)] = int(len(prior))
        if prior.empty:
            continue
        residual = prior.y.to_numpy(float) - prior.frozen_pooled.to_numpy(float)
        variance = (prior.frozen_pooled.to_numpy(float)
                    * (1 - prior.frozen_pooled.to_numpy(float)))
        offsets = pd.DataFrame({"machine": prior.machine_hash.to_numpy(),
                                "residual": residual, "variance": variance}).groupby(
                                    "machine").sum()
        offset = (offsets.residual / (offsets.variance + 20)).clip(-.5, .5)
        shift = frame.loc[today, "machine_hash"].map(offset).fillna(0).to_numpy(float)
        z = np.log(baseline[today] / (1 - baseline[today])) + shift
        estimate[today] = 1 / (1 + np.exp(-z))
    return estimate, counts


def run() -> dict:
    original = json.loads(RESULT.read_text(encoding="utf-8"))
    if original["predictions_sha256"] != sha256(PREDICTIONS):
        raise ValueError("saved scored rows have changed")
    frame = pd.read_parquet(PREDICTIONS).sort_values(
        ["decision_utc_s", "machine_hash", "gpu_index"], kind="mergesort").reset_index(drop=True)
    if (len(frame) != original["test_rows"] or frame.machine_hash.nunique() != original["test_machines"]
            or sorted(frame.utc_day.unique().tolist()) != list(range(17501, 17516))):
        raise ValueError("saved support has changed")
    y = frame.y.to_numpy(float)
    base = frame.frozen_pooled.to_numpy(float)
    base_brier = float(np.mean((y - base)**2))
    results = {}
    for delay in DELAYS_SECONDS:
        estimate, counts = forecast(frame, delay)
        if delay == 0 and not np.allclose(
                estimate, frame.previous_day_machine.to_numpy(float), atol=1e-12, rtol=0):
            raise ValueError("zero-delay forecast does not reproduce frozen score")
        errors = (y - estimate)**2
        daily = frame[["utc_day"]].assign(error=errors, base_error=(y - base)**2).groupby(
            "utc_day").mean()
        machine = frame[["machine_hash"]].assign(error=errors, base_error=(y - base)**2).groupby(
            "machine_hash").mean()
        results[str(delay)] = {
            "feedback_delay_seconds_after_30min_outcome": delay,
            "row_brier": float(errors.mean()),
            "relative_brier_change_vs_p0_pct": float(100 * (errors.mean() / base_brier - 1)),
            "machine_equal_brier": float(machine.error.mean()),
            "average_precision": float(average_precision_score(y, estimate)),
            "days_better_than_p0": int((daily.error < daily.base_error).sum()),
            "days_equal_p0": int((daily.error == daily.base_error).sum()),
            "machines_better_than_p0": int((machine.error < machine.base_error).sum()),
            "calibration_rows_by_day": counts,
        }
    reference = original["assessment"]["methods"]["previous_day_machine"]
    if abs(results["0"]["row_brier"] - reference["row_brier"]) > 1e-12:
        raise ValueError("zero-delay Brier differs from frozen result")
    output = {
        "status": "POST_HOC_FEEDBACK_DELAY_SENSITIVITY_ONLY",
        "scope": "same exposed Philly cohort and already scored rows; no new validation",
        "source_predictions_sha256": sha256(PREDICTIONS),
        "baseline_row_brier": base_brier,
        "delays": results,
    }
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != output:
            raise ValueError("stored feedback sensitivity differs from fresh replay")
    else:
        OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": result["status"],
                      "summary": {delay: {key: record[key] for key in (
                          "row_brier", "relative_brier_change_vs_p0_pct",
                          "days_better_than_p0", "machines_better_than_p0")}
                          for delay, record in result["delays"].items()}},
                     ensure_ascii=False, indent=2))
