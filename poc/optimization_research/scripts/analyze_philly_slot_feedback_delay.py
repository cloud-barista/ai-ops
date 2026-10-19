"""Post-hoc feedback-delay replay on the already scored Philly slot cohort."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score

from compare_philly_day_ahead_baselines_posthoc import exact_shift


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/philly_slot_day_holdout_v1"
PROTOCOL = ROOT / "reports/philly_slot_feedback_delay_protocol.md"
OUT = ARTIFACT / "feedback_delay_sensitivity_posthoc.json"
DELAYS = (0, 300, 3600, 21600, 43200, 86400)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def thin_hourly(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.assign(hour=frame.decision_utc_s // 3600).drop_duplicates(
        ["machine_hash", "gpu_index", "hour"], keep="first")


def replay(frame: pd.DataFrame, hourly: pd.DataFrame,
           delay_s: int) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    if delay_s < 0 or int(delay_s) != delay_s:
        raise ValueError("delay must be a nonnegative integer seconds")
    base = frame.p0.to_numpy(dtype=float)
    machine_only = base.copy()
    machine_slot = base.copy()
    counts = []
    for day in sorted(int(x) for x in frame.utc_day.unique()):
        today = frame.utc_day.eq(day).to_numpy()
        # Pick the input-time first row per hour before this maturity filter.
        previous = hourly.loc[hourly.utc_day.eq(day - 1)
                              & hourly.decision_utc_s.add(1800 + delay_s)
                              .le(day * 86400)]
        counts.append({"utc_day": day, "completed_previous_day_hourly_labels": len(previous)})
        if previous.empty:
            continue
        machine_shifts: dict[str, float] = {}
        slot_shifts: dict[tuple[str, int], float] = {}
        for machine, one in previous.groupby("machine_hash", sort=True):
            machine = str(machine)
            p0 = one.p0.to_numpy(dtype=float)
            y = one.y.to_numpy(dtype=float)
            delta = exact_shift(y, p0)
            machine_shifts[machine] = delta
            adjusted = expit(logit(np.clip(p0, 1e-9, 1 - 1e-9)) + delta)
            for gpu, positions in one.groupby("gpu_index", sort=True).indices.items():
                chosen = np.asarray(positions, dtype=int)
                p = adjusted[chosen]
                slot_shifts[(machine, int(gpu))] = float(np.clip(
                    (y[chosen] - p).sum() / ((p * (1 - p)).sum() + 20.0),
                    -0.5, 0.5))
        current = frame.loc[today, ["machine_hash", "gpu_index"]]
        m = current.machine_hash.map(machine_shifts).fillna(0).to_numpy(float)
        keys = pd.Series(list(zip(current.machine_hash, current.gpu_index.astype(int))),
                         index=current.index)
        g = keys.map(slot_shifts).fillna(0).to_numpy(float)
        z = logit(np.clip(base[today], 1e-9, 1 - 1e-9))
        machine_only[today] = expit(z + m)
        machine_slot[today] = expit(z + m + g)
    return machine_only, machine_slot, counts


def metrics(frame: pd.DataFrame, probability: np.ndarray) -> dict:
    y = frame.y.to_numpy(dtype=float)
    days = frame.utc_day.to_numpy(dtype=int)
    machines = frame.machine_hash.to_numpy(str)
    middle = sorted(np.unique(days))[7]
    loss = (y - probability) ** 2
    return {
        "row_brier": float(loss.mean()),
        "machine_equal_brier": float(pd.Series(loss).groupby(machines).mean().mean()),
        "average_precision": float(average_precision_score(y, probability)),
        "first_seven_days_brier": float(loss[days < middle].mean()),
        "last_eight_days_brier": float(loss[days >= middle].mean()),
    }


def run() -> dict:
    result = json.loads((ARTIFACT / "result.json").read_text(encoding="utf-8"))
    source = ARTIFACT / "test_predictions.parquet"
    if result.get("status") != "NEW_MACHINE_EXPLORATORY_GO" or sha256(source) != result["predictions_sha256"]:
        raise ValueError("scored cohort artifact differs")
    frame = pd.read_parquet(source)
    if (len(frame) != result["test_rows"] or frame.machine_hash.nunique() != result["test_machines"]
            or frame.duplicated(["machine_hash", "gpu_index", "decision_utc_s"]).any()
            or not frame.utc_day.eq(frame.decision_utc_s // 86400).all()):
        raise ValueError("scored decision keys differ")
    hourly = thin_hourly(frame)
    summaries = {}
    for delay in DELAYS:
        exact, slot, counts = replay(frame, hourly, delay)
        if delay == 0:
            for name, values in (("previous_day_exact", exact),
                                 ("previous_day_machine_slot", slot)):
                difference = float(np.max(np.abs(values - frame[name].to_numpy(dtype=float))))
                if difference > 1e-12:
                    raise ValueError(f"zero-delay {name} does not replay")
        if delay == 86400:
            if not (np.array_equal(exact, frame.p0.to_numpy(dtype=float))
                    and np.array_equal(slot, frame.p0.to_numpy(dtype=float))):
                raise ValueError("24h delay should use no previous-day feedback")
        exact_score = metrics(frame, exact)
        slot_score = metrics(frame, slot)
        y = frame.y.to_numpy(dtype=float)
        machine_losses = pd.DataFrame({
            "machine": frame.machine_hash,
            "exact": (y - exact) ** 2,
            "slot": (y - slot) ** 2,
        }).groupby("machine").mean()
        summaries[str(delay)] = {
            "additional_feedback_delay_s": delay,
            "completed_previous_day_hourly_labels_total": sum(
                item["completed_previous_day_hourly_labels"] for item in counts),
            "completed_previous_day_hourly_labels_by_day": counts,
            "exact_machine": exact_score,
            "machine_plus_slot": slot_score,
            "slot_relative_brier_improvement_vs_exact":
                1.0 - slot_score["row_brier"] / exact_score["row_brier"],
            "machines_slot_better_than_exact": int(
                (machine_losses.slot < machine_losses.exact).sum()),
            "exact_prediction_float64_sha256": hashlib.sha256(
                exact.tobytes()).hexdigest(),
            "slot_prediction_float64_sha256": hashlib.sha256(
                slot.tobytes()).hexdigest(),
        }
    zero = summaries["0"]
    for name, saved in (("exact_machine", result["scores"]["previous_day_exact"]),
                        ("machine_plus_slot", result["scores"]["previous_day_machine_slot"])):
        for metric, value in saved.items():
            if abs(zero[name][metric] - value) > 1e-12:
                raise ValueError(f"zero-delay {name}.{metric} score changed")
    answer = {
        "status": "POST_HOC_DELAY_SENSITIVITY",
        "scope": "same already scored Philly machine cohort, assumed feedback arrival times",
        "source_predictions_sha256": result["predictions_sha256"],
        "source_result_sha256": sha256(ARTIFACT / "result.json"),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "rows": len(frame), "machines": frame.machine_hash.nunique(),
        "delays": summaries,
        "descriptive_5min_relative_improvement_at_least_1pct":
            summaries["300"]["slot_relative_brier_improvement_vs_exact"] >= .01,
    }
    if OUT.exists():
        if json.loads(OUT.read_text(encoding="utf-8")) != answer:
            raise ValueError("saved delay sensitivity changed")
    else:
        OUT.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n")
    return answer


if __name__ == "__main__":
    result = run()
    print(json.dumps({"status": result["status"], "five_minute": result["delays"]["300"],
                      "descriptive_5min_relative_improvement_at_least_1pct":
                      result["descriptive_5min_relative_improvement_at_least_1pct"]}, sort_keys=True))
