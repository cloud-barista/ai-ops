"""Replay every saved Philly slot-day holdout decision through the pure core."""

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
RESULT = ARTIFACTS / "result.json"
PREDICTIONS = ARTIFACTS / "test_predictions.parquet"
OUTPUT = ARTIFACTS / "core_replay_verification.json"
FEATURES = ["machine_hash", "gpu_index", "decision_utc_s", "p0"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run() -> dict:
    saved = json.loads(RESULT.read_text(encoding="utf-8"))
    if (saved["status"] != "NEW_MACHINE_EXPLORATORY_GO"
            or sha256(PREDICTIONS) != saved["predictions_sha256"]):
        raise ValueError("frozen successful holdout or prediction hash changed")
    rows = pd.read_parquet(PREDICTIONS)
    if (len(rows) != saved["test_rows"] or rows.machine_hash.nunique() != saved["test_machines"]
            or rows.duplicated(["machine_hash", "gpu_index", "decision_utc_s"]).any()
            or not rows.utc_day.eq(rows.decision_utc_s // 86400).all()):
        raise ValueError("saved holdout row contract changed")
    scored = []
    for day in sorted(rows.utc_day.unique()):
        day = int(day)
        targets = rows.loc[rows.utc_day.eq(day), FEATURES]
        prior = rows.loc[rows.utc_day.eq(day - 1), [*FEATURES, "y"]].copy()
        prior["label_available_at_utc_s"] = prior.decision_utc_s + 1800
        replay = calibrate_day(targets, prior, day_start_utc_s=day * 86400)
        original = rows.loc[rows.utc_day.eq(day)].reset_index(drop=True)
        for generated, stored in (("machine_probability", "previous_day_exact"),
                                  ("slot_probability", "previous_day_machine_slot")):
            difference = np.abs(replay[generated].to_numpy(float) -
                                original[stored].to_numpy(float))
            replay[generated + "_absolute_error"] = difference
        replay["y"] = original.y.to_numpy(int)
        scored.append(replay)
    all_rows = pd.concat(scored, ignore_index=True)
    maximum = {name: float(all_rows[name + "_absolute_error"].max())
               for name in ("machine_probability", "slot_probability")}
    if any(value > 1e-12 for value in maximum.values()):
        raise ValueError("independent core differs from frozen holdout probabilities")
    y = all_rows.y.to_numpy(float)
    brier = {
        "machine": float(np.mean((y - all_rows.machine_probability.to_numpy(float)) ** 2)),
        "machine_slot": float(np.mean((y - all_rows.slot_probability.to_numpy(float)) ** 2)),
    }
    old = saved["scores"]
    if (not np.isclose(brier["machine"], old["previous_day_exact"]["row_brier"], atol=1e-12, rtol=0)
            or not np.isclose(brier["machine_slot"],
                              old["previous_day_machine_slot"]["row_brier"], atol=1e-12, rtol=0)):
        raise ValueError("core Brier differs from frozen holdout score")
    record = {"status": "CORE_REPLAY_VERIFIED", "predictions_sha256": sha256(PREDICTIONS),
              "holdout_result_sha256": sha256(RESULT), "rows": len(all_rows),
              "machines": int(all_rows.machine_hash.nunique()), "days": len(scored),
              "max_absolute_difference": maximum, "row_brier": brier,
              "feedback_availability_assumption": "decision_utc_s + 1800 seconds"}
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != record:
            raise ValueError("existing core replay record changed")
    else:
        OUTPUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
