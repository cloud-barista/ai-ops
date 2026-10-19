"""Exploratory GPU high-utilization alert rules on already-exposed Philly scores.

No VM actions are issued. Both Parquet prediction files have previously had
their outcomes examined, so this is a method-development analysis only.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "development": (
        ROOT / "artifacts/philly_gpu_risk_v1/test_predictions.parquet",
        "04ce609b36c468cfbec021c3a4900a3a08cc818b9f6c3cf95a0efc8f58bd5d4f",
    ),
    "exposed_machine_followup": (
        ROOT / "artifacts/philly_machine_holdout_score_v1/test_predictions.parquet",
        "3907dcfde07f7127157ac5c4a5705df7d1790e11a197b726aa66aed06f3aed6c",
    ),
}
COOLDOWN_S = 1800
STEP_S = 300


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_groups(path: Path, expected_sha: str) -> list[dict]:
    actual = digest(path)
    if actual != expected_sha:
        raise ValueError(f"Prediction source hash changed: {path}: {actual}")
    columns = ["machine_hash", "gpu_index", "decision_utc_s", "split", "cohort",
               "current_state", "current_util", "y", "pooled_quality"]
    frame = pq.read_table(path, columns=columns).to_pandas()
    if set(frame.split) != {"test"} or set(frame.cohort) != {"complete"}:
        raise ValueError("Expected only complete-past test predictions")
    if frame[columns].isna().any().any():
        raise ValueError("Missing prediction field")
    if not frame.pooled_quality.between(0, 1).all() or not frame.current_util.between(0, 100).all():
        raise ValueError("Probability or utilization outside the expected range")
    if not frame.y.isin([0, 1]).all():
        raise ValueError("Outcome must be binary")
    if not ((frame.current_util >= 80).to_numpy() ==
            (frame.current_state == "at_least_80").to_numpy()).all():
        raise ValueError("Current state and utilization disagree")
    frame = frame.sort_values(["machine_hash", "gpu_index", "decision_utc_s"],
                              kind="mergesort")
    if frame.duplicated(["machine_hash", "gpu_index", "decision_utc_s"]).any():
        raise ValueError("Duplicate GPU decision key")
    result = []
    for (machine, slot), chunk in frame.groupby(["machine_hash", "gpu_index"], sort=False):
        times = chunk.decision_utc_s.to_numpy(np.int64)
        if len(times) > 1 and np.any(np.diff(times) != STEP_S):
            raise ValueError(f"Noncontinuous stored score series: {machine}/{slot}")
        result.append({"machine": machine, "slot": int(slot), "times": times,
                       "scores": chunk.pooled_quality.to_numpy(float),
                       "high": (chunk.current_util.to_numpy(float) >= 80),
                       "y": chunk.y.to_numpy(np.int8)})
    return result


def issue_indices(times: np.ndarray, scores: np.ndarray, high: np.ndarray,
                  threshold: float, confirmations: int, cooldown_s: int = COOLDOWN_S) -> list[int]:
    """Emit at decision time using only prior/current score and current state."""
    if confirmations < 1 or not (0 <= threshold <= 1) or cooldown_s < 0:
        raise ValueError("Invalid alert rule")
    if len(times) != len(scores) or len(times) != len(high):
        raise ValueError("Unequal input lengths")
    alerts: list[int] = []
    streak, last_alert, last_time = 0, None, None
    for i, (t, score, current_high) in enumerate(zip(times, scores, high)):
        t = int(t)
        if last_time is not None and t - last_time != STEP_S:
            streak = 0
            last_time = t
            continue
        last_time = t
        if current_high or not np.isfinite(score) or score < threshold:
            streak = 0
            continue
        streak += 1
        if streak >= confirmations and (last_alert is None or t - last_alert >= cooldown_s):
            alerts.append(i)
            last_alert = t
    return alerts


def eligible_event_starts(times: np.ndarray, high: np.ndarray) -> list[int]:
    """Actual five-minute window starts, with a fully observed pre-alert horizon."""
    starts = []
    for i in range(1, len(times)):
        if not high[i] or high[i - 1] or times[i] - times[i - 1] != STEP_S:
            continue
        actual_start = int(times[i]) - 600
        if actual_start - 1800 < int(times[0]):
            continue
        starts.append(actual_start)
    return starts


def summarize(groups: list[dict], threshold: float, confirmations: int) -> dict:
    alerts = positives = events = covered_events = 0
    lead_minutes = []
    machines = set()
    slot_days = 0.0
    machine_results = []
    for g in groups:
        times, scores, high, y = g["times"], g["scores"], g["high"], g["y"]
        issued = issue_indices(times, scores, high, threshold, confirmations)
        alert_times = [int(times[i]) for i in issued]
        starts = eligible_event_starts(times, high)
        hit_count = 0
        for start in starts:
            j = bisect.bisect_left(alert_times, start)
            if j and alert_times[j - 1] >= start - 1800:
                hit_count += 1
                lead_minutes.append((start - alert_times[j - 1]) / 60)
        hit_alerts = int(sum(y[i] == 1 for i in issued))
        alerts += len(issued)
        positives += hit_alerts
        events += len(starts)
        covered_events += hit_count
        machines.add(g["machine"])
        slot_days += len(times) * STEP_S / 86400
        machine_results.append({"machine_hash": g["machine"], "gpu_index": g["slot"],
                                "eligible_windows": len(times), "alerts": len(issued),
                                "positive_alerts": hit_alerts, "eligible_starts": len(starts),
                                "detected_starts": hit_count})
    return {"threshold": float(threshold), "confirmations": confirmations,
            "machines": len(machines), "slots": len(groups), "slot_days": slot_days,
            "alerts": alerts, "positive_alerts": positives,
            "alert_precision": positives / alerts if alerts else None,
            "alerts_per_slot_day": alerts / slot_days if slot_days else None,
            "eligible_high_starts": events, "detected_high_starts": covered_events,
            "event_recall": covered_events / events if events else None,
            "median_lead_minutes": float(np.median(lead_minutes)) if lead_minutes else None,
            "per_slot": machine_results}


def choose_threshold(groups: list[dict]) -> tuple[dict, dict, list[dict]]:
    baseline = summarize(groups, 0.5, 1)
    grid = [summarize(groups, i / 20, 2) for i in range(1, 20)]
    selected = min(grid, key=lambda result: (abs(result["alerts"] - baseline["alerts"]),
                                            -result["threshold"]))
    return baseline, selected, grid


def exploratory_gate(baseline: dict, candidate: dict, alert_difference: float | None) -> bool:
    if alert_difference is None or abs(alert_difference) > 0.05:
        return False
    for key in ("alert_precision", "event_recall"):
        if baseline[key] is None or candidate[key] is None:
            return False
        if candidate[key] < baseline[key]:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path,
                        default=ROOT / "artifacts/philly_alert_state_machine_v0/results.json")
    args = parser.parse_args()
    development = load_groups(*SOURCES["development"])
    exposed = load_groups(*SOURCES["exposed_machine_followup"])
    dev_base, dev_candidate, grid = choose_threshold(development)
    threshold = dev_candidate["threshold"]
    test_base = summarize(exposed, 0.5, 1)
    test_candidate = summarize(exposed, threshold, 2)
    alert_difference = ((test_candidate["alerts"] / test_base["alerts"] - 1)
                        if test_base["alerts"] else None)
    gate = exploratory_gate(test_base, test_candidate, alert_difference)
    result = {
        "scope": "exploratory_already_exposed_philly_machines_no_vm_action",
        "source_sha256": {key: sha for key, (_, sha) in SOURCES.items()},
        "rule": {"cooldown_seconds": COOLDOWN_S, "baseline_threshold": 0.5,
                 "candidate_confirmations": 2, "candidate_threshold": threshold,
                 "threshold_selected_by": "closest_development_alert_count_without_labels"},
        "development": {"baseline": dev_base, "candidate": dev_candidate,
                        "candidate_grid": [{k: v for k, v in row.items() if k != "per_slot"}
                                           for row in grid]},
        "exposed_machine_followup": {"baseline": test_base, "candidate": test_candidate,
                                      "candidate_alert_relative_difference": alert_difference,
                                      "exploratory_gate_pass": gate},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                   indent=2) + "\n")
    print(json.dumps({"candidate_threshold": threshold,
                      "development_alerts_baseline": dev_base["alerts"],
                      "development_alerts_candidate": dev_candidate["alerts"],
                      "exposed_baseline": {k: v for k, v in test_base.items()
                                           if k not in {"per_slot"}},
                      "exposed_candidate": {k: v for k, v in test_candidate.items()
                                            if k not in {"per_slot"}},
                      "exploratory_gate_pass": gate}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
