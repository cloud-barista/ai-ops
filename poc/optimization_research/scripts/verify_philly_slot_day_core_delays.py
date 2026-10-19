"""Independently replay all published feedback-delay cases with the new core."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from policies.philly_slot_day import calibrate_day  # noqa: E402


ARTIFACTS = ROOT / "artifacts/philly_slot_day_holdout_v1"
PREDICTIONS = ARTIFACTS / "test_predictions.parquet"
PUBLISHED = ARTIFACTS / "feedback_delay_sensitivity_posthoc.json"
OUTPUT = ARTIFACTS / "core_delay_replay_verification.json"
FEATURES = ["machine_hash", "gpu_index", "decision_utc_s", "p0"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replay_one(rows: pd.DataFrame, delay_s: int) -> tuple[np.ndarray, np.ndarray]:
    machine, slot = [], []
    for day in sorted(int(x) for x in rows.utc_day.unique()):
        targets = rows.loc[rows.utc_day.eq(day), FEATURES]
        prior = rows.loc[rows.utc_day.eq(day - 1), [*FEATURES, "y"]].copy()
        prior["label_available_at_utc_s"] = prior.decision_utc_s + 1800 + delay_s
        result = calibrate_day(targets, prior, day_start_utc_s=day * 86400)
        machine.append(result.machine_probability.to_numpy(float))
        slot.append(result.slot_probability.to_numpy(float))
    return np.concatenate(machine), np.concatenate(slot)


def run() -> dict:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    if (published["status"] != "POST_HOC_DELAY_SENSITIVITY"
            or sha256(PREDICTIONS) != published["source_predictions_sha256"]):
        raise ValueError("published source differs")
    rows = pd.read_parquet(PREDICTIONS)
    if (len(rows) != published["rows"] or rows.machine_hash.nunique() != published["machines"]
            or not rows.utc_day.is_monotonic_increasing
            or not rows.utc_day.eq(rows.decision_utc_s // 86400).all()):
        raise ValueError("saved decision rows differ")
    y = rows.y.to_numpy(float)
    records = {}
    for key, original in published["delays"].items():
        delay_s = int(key)
        machine, slot = replay_one(rows, delay_s)
        hashes = {
            "machine": hashlib.sha256(machine.tobytes()).hexdigest(),
            "machine_slot": hashlib.sha256(slot.tobytes()).hexdigest(),
        }
        if (hashes["machine"] != original["exact_prediction_float64_sha256"]
                or hashes["machine_slot"] != original["slot_prediction_float64_sha256"]):
            raise ValueError(f"delay {delay_s} probability bytes differ")
        brier = {"machine": float(np.mean((y - machine) ** 2)),
                 "machine_slot": float(np.mean((y - slot) ** 2))}
        if (not np.isclose(brier["machine"], original["exact_machine"]["row_brier"],
                           rtol=0, atol=1e-12)
                or not np.isclose(brier["machine_slot"],
                                  original["machine_plus_slot"]["row_brier"],
                                  rtol=0, atol=1e-12)):
            raise ValueError(f"delay {delay_s} Brier differs")
        if delay_s == 86400 and not (np.array_equal(machine, rows.p0.to_numpy(float))
                                     and np.array_equal(slot, rows.p0.to_numpy(float))):
            raise ValueError("24-hour delay must have no available previous-day feedback")
        records[key] = {"probability_hashes_match": True, "row_brier": brier}
    answer = {"status": "CORE_DELAY_REPLAY_VERIFIED", "rows": len(rows),
              "machines": int(rows.machine_hash.nunique()),
              "source_predictions_sha256": sha256(PREDICTIONS),
              "published_delay_result_sha256": sha256(PUBLISHED),
              "core_code_sha256": sha256(ROOT / "policies/philly_slot_day.py"),
              "delays": records}
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != answer:
            raise ValueError("saved core delay verification changed")
    else:
        OUTPUT.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return answer


if __name__ == "__main__":
    record = run()
    print(json.dumps({"status": record["status"], "rows": record["rows"],
                      "delays_seconds": sorted(map(int, record["delays"]))}, sort_keys=True))
