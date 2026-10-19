"""Post-hoc decomposition of the frozen Philly calibration test.

This script only reads the published one-shot predictions and metrics. Its
output is an explanation of the failed gate, not another model-selection run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "philly_machine_calibration_v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def summarize(rows: pd.DataFrame) -> dict:
    y = rows["y"].to_numpy()
    baseline = rows["pooled"].to_numpy()
    method = rows["validated_offset"].to_numpy()
    return {
        "rows": int(len(rows)),
        "machines": int(rows["machine_hash"].nunique()),
        "event_rate": float(np.mean(y)),
        "baseline_mean": float(np.mean(baseline)),
        "method_mean": float(np.mean(method)),
        "baseline_brier": float(np.mean((baseline - y) ** 2)),
        "method_brier": float(np.mean((method - y) ** 2)),
        "delta_brier_method_minus_baseline": float(
            np.mean((method - y) ** 2 - (baseline - y) ** 2)
        ),
        "baseline_ap": float(average_precision_score(y, baseline)),
        "method_ap": float(average_precision_score(y, method)),
    }


def main() -> None:
    metrics = json.loads((ARTIFACTS / "test_metrics.json").read_text())
    predictions_path = ARTIFACTS / "test_predictions.parquet"
    assert sha256(predictions_path) == metrics["test_predictions_sha256"]
    rows = pd.read_parquet(
        predictions_path,
        columns=["machine_hash", "utc_day", "y", "pooled", "validated_offset"],
    )
    assert len(rows) == metrics["rows"]
    gate = {entry["machine_hash"]: entry["gate_on"] for entry in metrics["gate_decisions"]}
    rows["applied"] = rows["machine_hash"].map(gate)
    assert rows["applied"].notna().all()
    assert int(rows.loc[rows["applied"], "machine_hash"].nunique()) == metrics["gated_machines"]

    overall = summarize(rows)
    assert abs(overall["baseline_brier"] - metrics["metrics"]["pooled"]["brier"]) < 1e-12
    assert abs(overall["method_brier"] - metrics["metrics"]["validated_offset"]["brier"]) < 1e-12

    by_application = {
        "gate_on": summarize(rows.loc[rows["applied"]]),
        "gate_off": summarize(rows.loc[~rows["applied"]]),
    }
    by_half = {}
    for name, selected in (
        ("first", rows["utc_day"] < metrics["calendar_halves"][0]["end_utc_day_exclusive"]),
        ("second", rows["utc_day"] >= metrics["calendar_halves"][0]["end_utc_day_exclusive"]),
    ):
        by_half[name] = {
            "gate_on": summarize(rows.loc[selected & rows["applied"]]),
            "gate_off": summarize(rows.loc[selected & ~rows["applied"]]),
        }

    eligible_machine_ap = []
    for _, machine_rows in rows.groupby("machine_hash", sort=False):
        if machine_rows["y"].nunique() < 2:
            continue
        eligible_machine_ap.append(
            [
                average_precision_score(machine_rows["y"], machine_rows["pooled"]),
                average_precision_score(machine_rows["y"], machine_rows["validated_offset"]),
            ]
        )
    ap_pairs = np.asarray(eligible_machine_ap)

    rows["loss_delta"] = (rows["validated_offset"] - rows["y"]) ** 2 - (
        rows["pooled"] - rows["y"]
    ) ** 2
    contributions = (
        rows.loc[rows["applied"]]
        .groupby("machine_hash", sort=False)
        .agg(rows=("y", "size"), loss_delta_sum=("loss_delta", "sum"))
        .reset_index()
    )
    output = {
        "kind": "posthoc_philly_calibration_failure_analysis",
        "selection_allowed": False,
        "test_metrics_sha256": sha256(ARTIFACTS / "test_metrics.json"),
        "test_predictions_sha256": sha256(predictions_path),
        "overall": overall,
        "by_gate": by_application,
        "by_gate_and_calendar_half": by_half,
        "within_machine_ap": {
            "eligible_machines": int(len(ap_pairs)),
            "baseline_machine_equal_mean": float(ap_pairs[:, 0].mean()),
            "method_machine_equal_mean": float(ap_pairs[:, 1].mean()),
            "max_absolute_difference": float(np.max(np.abs(ap_pairs[:, 1] - ap_pairs[:, 0]))),
        },
        "largest_harmful_machine_contributions": contributions.nlargest(3, "loss_delta_sum").to_dict("records"),
        "largest_helpful_machine_contributions": contributions.nsmallest(3, "loss_delta_sum").to_dict("records"),
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
