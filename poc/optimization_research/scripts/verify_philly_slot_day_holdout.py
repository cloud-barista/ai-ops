"""Independent stored-score and causal-replay check after the one-time score."""

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
ARTIFACT = ROOT / "artifacts/philly_slot_day_holdout_v1"
OUT = ARTIFACT / "independent_verification.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_close(actual: float, expected: float, name: str) -> None:
    if not np.isclose(actual, expected, rtol=0, atol=1e-12):
        raise ValueError(f"{name} differs: {actual} vs {expected}")


def exact_shift(y: np.ndarray, p0: np.ndarray) -> float:
    z = logit(np.clip(p0, 1e-9, 1 - 1e-9))
    derivative = lambda delta: float(np.sum(y - expit(z + delta)) - 20 * delta)
    if derivative(-.5) <= 0:
        return -.5
    if derivative(.5) >= 0:
        return .5
    return float(brentq(derivative, -.5, .5, xtol=1e-12))


def replay_machine_days(frame: pd.DataFrame) -> tuple[int, float]:
    """Rebuild a fixed sample using a separate grouped chronological path."""
    selected = sorted(frame.machine_hash.unique())[:3]
    checked = 0
    largest_difference = 0.0
    for machine in selected:
        machine_frame = frame.loc[frame.machine_hash.eq(machine)]
        for day in (17501, 17503, 17508, 17515):
            today = machine_frame.loc[machine_frame.utc_day.eq(day)]
            if today.empty:
                continue
            previous = machine_frame.loc[
                machine_frame.utc_day.eq(day - 1)
                & machine_frame.decision_utc_s.add(1800).le(day * 86400)]
            previous = previous.sort_values(["decision_utc_s", "gpu_index"]).copy()
            previous["hour"] = previous.decision_utc_s // 3600
            previous = previous.drop_duplicates(["gpu_index", "hour"], keep="first")
            machine_shift = 0.0
            slot_shifts: dict[int, float] = {}
            if not previous.empty:
                y = previous.y.to_numpy(dtype=float)
                p0 = previous.p0.to_numpy(dtype=float)
                machine_shift = exact_shift(y, p0)
                previous["machine_p"] = expit(
                    logit(np.clip(p0, 1e-9, 1 - 1e-9)) + machine_shift)
                for gpu, part in previous.groupby("gpu_index"):
                    p = part.machine_p.to_numpy(dtype=float)
                    observed = part.y.to_numpy(dtype=float)
                    slot_shifts[int(gpu)] = float(np.clip(
                        (observed - p).sum() / ((p * (1 - p)).sum() + 20),
                        -.5, .5))
            base = today.p0.to_numpy(dtype=float)
            slots = today.gpu_index.map(slot_shifts).fillna(0).to_numpy(dtype=float)
            reconstructed = expit(logit(np.clip(base, 1e-9, 1 - 1e-9))
                                  + machine_shift + slots)
            largest_difference = max(largest_difference, float(np.max(np.abs(
                reconstructed - today.previous_day_machine_slot.to_numpy(dtype=float)))))
            checked += len(today)
    if checked == 0 or largest_difference > 1e-12:
        raise ValueError("causal previous-day replay differs")
    return checked, largest_difference


def verify() -> dict:
    result = json.loads((ARTIFACT / "result.json").read_text(encoding="utf-8"))
    prediction_path = ARTIFACT / "test_predictions.parquet"
    if (sha256(prediction_path) != result["predictions_sha256"]
            or sha256(ARTIFACT / "freeze.json") != result["freeze_sha256"]
            or sha256(ARTIFACT / "support_audit.json") != result["support_sha256"]):
        raise ValueError("one-time score input or prediction hash changed")
    frame = pd.read_parquet(prediction_path)
    if (len(frame) != result["test_rows"] or len(frame) != 812_729
            or frame.machine_hash.nunique() != result["test_machines"]
            or frame.duplicated(["machine_hash", "gpu_index", "decision_utc_s"]).any()
            or not frame.utc_day.eq(frame.decision_utc_s // 86400).all()
            or not frame.y.isin((0, 1)).all()):
        raise ValueError("saved score rows differ from frozen support")
    y = frame.y.to_numpy(dtype=float)
    days = frame.utc_day.to_numpy(dtype=int)
    midpoint = sorted(np.unique(days))[7]
    machine = frame.machine_hash.to_numpy(str)
    for name, recorded in result["scores"].items():
        p = frame[name].to_numpy(dtype=float)
        if not np.isfinite(p).all() or np.any((p <= 0) | (p >= 1)):
            raise ValueError(f"invalid probabilities for {name}")
        loss = (y - p) ** 2
        computed = {
            "row_brier": float(loss.mean()),
            "machine_equal_brier": float(pd.Series(loss).groupby(machine).mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
            "first_seven_days_brier": float(loss[days < midpoint].mean()),
            "last_eight_days_brier": float(loss[days >= midpoint].mean()),
        }
        for key, value in computed.items():
            check_close(value, recorded[key], f"{name}.{key}")
    exact = (y - frame.previous_day_exact.to_numpy(dtype=float)) ** 2
    slot = (y - frame.previous_day_machine_slot.to_numpy(dtype=float)) ** 2
    grouped = pd.DataFrame({"machine": machine, "exact": exact, "slot": slot})
    sums = grouped.groupby("machine").agg(n=("exact", "size"),
                                           exact_sum=("exact", "sum"),
                                           slot_sum=("slot", "sum"))
    n = sums.n.to_numpy(dtype=float)
    difference = sums.slot_sum.to_numpy(dtype=float) - sums.exact_sum.to_numpy(dtype=float)
    draws = np.random.default_rng(43).integers(0, len(n), size=(2000, len(n)))
    interval = np.quantile(difference[draws].sum(axis=1) / n[draws].sum(axis=1),
                           [.025, .975])
    for value, expected in zip(interval, result["machine_bootstrap"]["percentile_95"]):
        check_close(float(value), expected, "machine bootstrap")
    wins = int((sums.slot_sum / sums.n < sums.exact_sum / sums.n).sum())
    if wins != result["machines_slot_better_than_exact"]:
        raise ValueError("machine win count differs")
    causal_rows, max_difference = replay_machine_days(frame)
    return {"status": "VERIFIED", "prediction_sha256": result["predictions_sha256"],
            "rows": len(frame), "machines": frame.machine_hash.nunique(),
            "checked_methods": sorted(result["scores"]),
            "bootstrap_percentile_95": interval.tolist(),
            "causal_replay_rows": causal_rows,
            "causal_replay_max_abs_probability_difference": max_difference}


if __name__ == "__main__":
    verification = verify()
    if OUT.exists():
        if json.loads(OUT.read_text(encoding="utf-8")) != verification:
            raise ValueError("previous independent verification changed")
    else:
        OUT.write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n")
    print(json.dumps(verification, sort_keys=True))
