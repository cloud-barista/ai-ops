"""Post-hoc same-budget top-5% risk-ranking audit on scored Philly machines."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/philly_day_ahead_calibration_holdout_v1"
PREDICTIONS = ARTIFACT / "test_predictions.parquet"
RESULT = ARTIFACT / "result.json"
OUTPUT = ARTIFACT / "alert_budget_posthoc.json"
METHODS = ("frozen_pooled", "previous_day_global", "previous_day_machine")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def score() -> dict:
    saved = json.loads(RESULT.read_text(encoding="utf-8"))
    if saved["predictions_sha256"] != digest(PREDICTIONS):
        raise ValueError("scored row hash differs")
    rows = pd.read_parquet(PREDICTIONS)
    if (len(rows) != saved["test_rows"] or rows.machine_hash.nunique() != saved["test_machines"]
            or sorted(rows.utc_day.unique().tolist()) != list(range(17501, 17516))):
        raise ValueError("eligible sample differs")
    rows["tie"] = [hashlib.sha256(
        f"{machine}:{int(slot)}:{int(time)}".encode("utf-8")).hexdigest()
        for machine, slot, time in zip(rows.machine_hash, rows.gpu_index, rows.decision_utc_s)]
    per_day = []
    for day, one in rows.groupby("utc_day", sort=True):
        quota = math.ceil(.05 * len(one))
        record = {"utc_day": int(day), "eligible": int(len(one)),
                  "positives": int(one.y.sum()), "alert_quota": int(quota),
                  "true_positive_alerts": {}}
        for method in METHODS:
            selected = one.sort_values([method, "tie"], ascending=[False, True],
                                       kind="mergesort").head(quota)
            record["true_positive_alerts"][method] = int(selected.y.sum())
        per_day.append(record)
    selected_total = sum(record["alert_quota"] for record in per_day)
    positive_total = sum(record["positives"] for record in per_day)
    combined = {}
    for method in METHODS:
        hits = sum(record["true_positive_alerts"][method] for record in per_day)
        combined[method] = {"alerts": selected_total, "true_positive_alerts": hits,
                            "precision": hits / selected_total,
                            "recall_of_positive_decision_rows": hits / positive_total}
    gain = np.array([record["true_positive_alerts"]["previous_day_machine"]
                     - record["true_positive_alerts"]["frozen_pooled"]
                     for record in per_day], dtype=int)
    # A date resampling diagnostic only; days in this cluster can be correlated.
    samples = np.random.default_rng(43).integers(0, len(gain), size=(10000, len(gain)))
    interval = np.quantile(gain[samples].sum(axis=1), [.025, .975])
    if (combined["previous_day_global"]["true_positive_alerts"]
            != combined["frozen_pooled"]["true_positive_alerts"]):
        raise ValueError("global daily logit shift changed a within-day ranking")
    result = {
        "status": "POST_HOC_ALERT_BUDGET_DIAGNOSTIC_ONLY",
        "scope": "same exposed machines, overlapping 30-minute outcome windows; no scale-action effect",
        "source_predictions_sha256": digest(PREDICTIONS),
        "selection": "ceil(5% of eligible decisions) each UTC day, probability descending, SHA256(machine_hash:gpu_index:decision_utc_s) ascending ties",
        "methods": combined,
        "candidate_minus_p0_true_positive_alerts": int(gain.sum()),
        "day_wins_ties_losses": {"wins": int((gain > 0).sum()),
                                 "ties": int((gain == 0).sum()),
                                 "losses": int((gain < 0).sum())},
        "day_resample_gain_95_exploratory": [float(x) for x in interval],
        "per_day": per_day,
    }
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("stored alert-budget diagnostic differs from fresh replay")
    else:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    result = score()
    print(json.dumps({"status": result["status"], "methods": result["methods"],
                      "day_wins_ties_losses": result["day_wins_ties_losses"],
                      "day_resample_gain_95_exploratory": result["day_resample_gain_95_exploratory"]},
                     ensure_ascii=False, indent=2))
