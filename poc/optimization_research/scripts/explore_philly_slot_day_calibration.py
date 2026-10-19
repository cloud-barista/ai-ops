"""Exploratory previous-day GPU-slot residual calibration on exposed cohorts."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score

import compare_philly_day_ahead_baselines_posthoc as baseline


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/philly_slot_day_calibration_exploratory_protocol.md"
OUT = ROOT / "artifacts/philly_slot_day_calibration_exploratory_v1/results.json"


def slot_day_predictions(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Predict each day from completed, hourly-thinned previous-day labels."""
    base = frame.p0.to_numpy(dtype=float)
    machine_only = base.copy()
    machine_slot = base.copy()
    hourly = frame.assign(hour=frame.decision_utc_s // 3600).drop_duplicates(
        ["machine_hash", "gpu_index", "hour"], keep="first")
    for day in sorted(int(x) for x in frame.utc_day.unique()):
        today = frame.utc_day.eq(day).to_numpy()
        previous = hourly.loc[hourly.utc_day.eq(day - 1)
                              & hourly.decision_utc_s.add(1800).le(day * 86400)]
        if previous.empty:
            continue
        machine_shifts: dict[str, float] = {}
        slot_shifts: dict[tuple[str, int], float] = {}
        for machine, one in previous.groupby("machine_hash", sort=True):
            machine = str(machine)
            probability = one.p0.to_numpy(dtype=float)
            observed = one.y.to_numpy(dtype=float)
            delta = baseline.exact_shift(observed, probability)
            machine_shifts[machine] = delta
            machine_adjusted = expit(logit(np.clip(probability, 1e-9, 1 - 1e-9)) + delta)
            for gpu, positions in one.groupby("gpu_index", sort=True).indices.items():
                selected = np.asarray(positions, dtype=int)
                p = machine_adjusted[selected]
                y = observed[selected]
                slot_shifts[(machine, int(gpu))] = float(np.clip(
                    (y - p).sum() / ((p * (1 - p)).sum() + 20.0), -0.5, 0.5))
        chosen = frame.loc[today, ["machine_hash", "gpu_index"]]
        m = chosen.machine_hash.map(machine_shifts).fillna(0).to_numpy(float)
        slot_keys = pd.Series(list(zip(chosen.machine_hash,
                                       chosen.gpu_index.astype(int))), index=chosen.index)
        g = slot_keys.map(slot_shifts).fillna(0).to_numpy(float)
        z = logit(np.clip(base[today], 1e-9, 1 - 1e-9))
        machine_only[today] = expit(z + m)
        machine_slot[today] = expit(z + m + g)
    return machine_only, machine_slot


def metrics(frame: pd.DataFrame, predictions: dict[str, np.ndarray]) -> dict:
    observed = frame.y.to_numpy(dtype=float)
    machine = frame.machine_hash.to_numpy(str)
    days = frame.utc_day.to_numpy(int)
    midpoint = sorted(np.unique(days))[7]
    return {
        name: {
            "row_brier": float(np.mean((observed - p) ** 2)),
            "machine_equal_brier": float(pd.Series((observed - p) ** 2)
                                          .groupby(machine).mean().mean()),
            "average_precision": float(average_precision_score(observed, p)),
            "first_seven_days_brier": float(np.mean((observed[days < midpoint]
                                                      - p[days < midpoint]) ** 2)),
            "last_eight_days_brier": float(np.mean((observed[days >= midpoint]
                                                     - p[days >= midpoint]) ** 2)),
        }
        for name, p in predictions.items()
    }


def run() -> dict:
    cohort_metrics = {}
    pooled = []
    for name, (path, expected, probability) in baseline.COHORTS.items():
        frame = baseline.load(path, expected, probability)
        basic = baseline.predict(frame)
        independently_exact, slot = slot_day_predictions(frame)
        if not np.allclose(independently_exact, basic["previous_day_exact"],
                           rtol=0, atol=1e-12):
            raise ValueError("machine-only exact comparator not reproduced")
        selected = {
            "p0": basic["p0"],
            "previous_day_newton": basic["previous_day_newton"],
            "previous_day_exact": basic["previous_day_exact"],
            "previous_day_machine_slot": slot,
        }
        scores = metrics(frame, selected)
        exact_loss = (frame.y.to_numpy(dtype=float) - selected["previous_day_exact"]) ** 2
        slot_loss = (frame.y.to_numpy(dtype=float) - slot) ** 2
        per_machine = (pd.DataFrame({"machine": frame.machine_hash, "exact": exact_loss,
                                     "slot": slot_loss})
                       .groupby("machine").mean())
        cohort_metrics[name] = {
            "rows": len(frame), "machines": frame.machine_hash.nunique(),
            "source_predictions_sha256": expected,
            "scores": scores,
            "machines_slot_better_than_exact": int((per_machine.slot < per_machine.exact).sum()),
        }
        pooled.append((frame, selected))
    pooled_y = np.concatenate([frame.y.to_numpy(float) for frame, _ in pooled])
    pooled_machine = np.concatenate([frame.machine_hash.to_numpy(str) for frame, _ in pooled])
    pooled_scores = {}
    for method in ("p0", "previous_day_newton", "previous_day_exact",
                   "previous_day_machine_slot"):
        probability = np.concatenate([predictions[method] for _, predictions in pooled])
        loss = (pooled_y - probability) ** 2
        pooled_scores[method] = {
            "row_brier": float(loss.mean()),
            "machine_equal_brier": float(pd.Series(loss).groupby(pooled_machine).mean().mean()),
            "average_precision": float(average_precision_score(pooled_y, probability)),
        }
    exact = pooled_scores["previous_day_exact"]
    slot = pooled_scores["previous_day_machine_slot"]
    improvements = {
        name: 1.0 - item["scores"]["previous_day_machine_slot"]["row_brier"]
                    / item["scores"]["previous_day_exact"]["row_brier"]
        for name, item in cohort_metrics.items()
    }
    gate = {
        "pooled_relative_brier_improvement_at_least_1pct":
            slot["row_brier"] <= 0.99 * exact["row_brier"],
        "cohorts_improved_at_least_two_of_three":
            sum(value > 0 for value in improvements.values()) >= 2,
        "no_cohort_worse_by_1pct_or_more": min(improvements.values()) > -0.01,
        "pooled_machine_equal_brier_lower":
            slot["machine_equal_brier"] < exact["machine_equal_brier"],
        "pooled_ap_drop_less_than_0p005":
            slot["average_precision"] > exact["average_precision"] - 0.005,
    }
    result = {
        "status": "GO_TO_NEW_MACHINE_COLLECTION" if all(gate.values())
                  else "NO_GO_NEW_MACHINE_COLLECTION",
        "scope": "three already exposed machine cohorts; exploratory only",
        "protocol_sha256": baseline.sha256(PROTOCOL),
        "script_sha256": baseline.sha256(Path(__file__)),
        "cohorts": cohort_metrics,
        "pooled_rows": len(pooled_y),
        "pooled_machines": len(set(pooled_machine)),
        "pooled_scores": pooled_scores,
        "cohort_relative_brier_improvement_vs_exact": improvements,
        "gate": gate,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": result["status"], "gate": result["gate"],
                      "pooled_scores": result["pooled_scores"]}, sort_keys=True))
