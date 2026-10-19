"""Recompute saved MaaS transfer scores without fitting or selecting methods."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/maas_day_ahead_instance_transfer_v1"
PREDICTIONS = ARTIFACT / "test_normal_predictions.parquet"
RESULT = ARTIFACT / "results.json"
OUT = ARTIFACT / "independent_verification.json"
METHODS = ("p0", "previous_day_global_exact", "previous_day_global_instance")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(rows: pd.DataFrame) -> dict:
    y = rows.y.to_numpy(dtype=float)
    ids = rows.instance_hash.to_numpy(str)
    answer = {}
    for name in METHODS:
        p = rows[name].to_numpy(dtype=float)
        loss = (y - p) ** 2
        answer[name] = {
            "row_brier": float(loss.mean()),
            "instance_equal_brier": float(pd.Series(loss).groupby(ids).mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
        }
    return answer


def compare(actual: dict, expected: dict, label: str) -> None:
    for name in METHODS:
        for key, value in actual[name].items():
            if not np.isclose(value, expected[name][key], rtol=0, atol=1e-12):
                raise ValueError(f"saved {label}.{name}.{key} differs")


def verify() -> dict:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if sha256(PREDICTIONS) != result["predictions_sha256"]:
        raise ValueError("prediction file differs from scored result")
    rows = pd.read_parquet(PREDICTIONS)
    if (len(rows) != result["test_rows"] or rows.instance_hash.nunique() != result["test_instances"]
            or rows.duplicated(["instance_hash", "decision_s"]).any()
            or not rows.day.eq(rows.decision_s // 86400).all()
            or not rows.y.isin((0, 1)).all()
            or not np.isfinite(rows[list(METHODS)].to_numpy()).all()):
        raise ValueError("saved prediction rows are inconsistent")
    compare(metrics(rows), result["scores"], "overall")
    for day, part in rows.groupby("day"):
        compare(metrics(part), result["by_day"][str(int(day))], f"day{day}")
    supported = rows.loc[rows.prior_hourly_labels.ge(6)]
    if len(supported) != result["supported_rows"]:
        raise ValueError("support count differs")
    compare(metrics(supported), result["six_anchor_scores"], "six_anchor")
    y = rows.y.to_numpy(dtype=float)
    losses = pd.DataFrame({
        "instance": rows.instance_hash,
        "global": (y - rows.previous_day_global_exact.to_numpy(dtype=float)) ** 2,
        "candidate": (y - rows.previous_day_global_instance.to_numpy(dtype=float)) ** 2,
    }).groupby("instance").mean()
    wins = int((losses.candidate < losses["global"]).sum())
    if wins != result["candidate_better_instances"]:
        raise ValueError("instance win count differs")
    return {"status": "VERIFIED", "prediction_sha256": result["predictions_sha256"],
            "rows": len(rows), "instances": rows.instance_hash.nunique(),
            "six_anchor_rows": len(supported), "candidate_better_instances": wins,
            "checked_days": sorted(int(x) for x in rows.day.unique())}


if __name__ == "__main__":
    answer = verify()
    if OUT.exists():
        if json.loads(OUT.read_text()) != answer:
            raise ValueError("saved independent verification changed")
    else:
        OUT.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n")
    print(json.dumps(answer, sort_keys=True))
