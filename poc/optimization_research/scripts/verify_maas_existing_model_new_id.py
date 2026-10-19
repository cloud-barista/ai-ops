"""Independent saved-prediction check for the MaaS new-ID model trial."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/maas_existing_model_new_id_v1"
RESULT = OUT / "results.json"
PREDICTIONS = OUT / "test_predictions.parquet"
SUPPORT = OUT / "support_audit.json"
VERIFICATION = OUT / "independent_verification.json"
METHODS = ("old_p0", "normal_beta", "pooled", "state_normal")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_top_events(y: np.ndarray, prediction: np.ndarray) -> float:
    n = int(np.ceil(len(y) * .05))
    score_groups = pd.DataFrame({"p": prediction, "y": y}).groupby("p", sort=True).agg(
        size=("y", "size"), positive=("y", "sum"))
    total = 0.0
    for _, item in score_groups.iloc[::-1].iterrows():
        used = min(n, int(item["size"]))
        total += used * float(item["positive"]) / int(item["size"])
        n -= used
        if n == 0:
            break
    return total


def summary(rows: pd.DataFrame) -> dict:
    y = rows.y.to_numpy(dtype=int)
    ids = rows.instance_hash.to_numpy(str)
    methods = {}
    for method in METHODS:
        p = rows[method].to_numpy(dtype=float)
        squared = (y - p) ** 2
        methods[method] = {
            "row_brier": float(squared.mean()),
            "id_equal_brier": float(pd.DataFrame({"id": ids, "sq": squared})
                                    .groupby("id").sq.mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
            "top5_alerts": int(np.ceil(len(y) * .05)),
            "top5_expected_events": expected_top_events(y, p),
        }
    return {"rows": len(rows), "events": int(y.sum()), "methods": methods}


def compare(actual: dict, expected: dict, context: str) -> None:
    if actual["rows"] != expected["rows"] or actual["events"] != expected["events"]:
        raise ValueError(f"{context} row/event count differs")
    for method in METHODS:
        for name, value in actual["methods"][method].items():
            target = expected["methods"][method][name]
            if not np.isclose(value, target, atol=1e-12, rtol=0):
                raise ValueError(f"{context}.{method}.{name} differs")


def verify() -> dict:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if (sha256(PREDICTIONS) != result["predictions_sha256"]
            or sha256(SUPPORT) != result["support_audit_sha256"]
            or json.loads(SUPPORT.read_text(encoding="utf-8"))["status"] != "SUPPORT_PASS"):
        raise ValueError("scored predictions changed")
    rows = pd.read_parquet(PREDICTIONS)
    if (rows.duplicated(["instance_hash", "decision_s"]).any()
            or not rows.day.eq(rows.decision_s // 86400).all()
            or set(int(x) for x in rows.day.unique()) != {145, 146}
            or not rows.y.isin([0, 1]).all()
            or not np.isfinite(rows[list(METHODS)].to_numpy(float)).all()):
        raise ValueError("saved test cohort changed")
    compare(summary(rows), result["overall"], "overall")
    for day, part in rows.groupby("day"):
        compare(summary(part), result["days"][str(int(day))], f"day{day}")
    difference = pd.DataFrame({
        "id": rows.instance_hash,
        "delta": (rows.y.to_numpy(float) - rows.pooled.to_numpy(float)) ** 2 -
                 (rows.y.to_numpy(float) - rows.normal_beta.to_numpy(float)) ** 2,
    }).groupby("id").delta.agg(["sum", "size"])
    sample = difference.to_numpy(float)
    rng = np.random.default_rng(43)
    draws = np.empty(2000)
    for i in range(2000):
        indices = rng.integers(0, len(sample), len(sample))
        taken = sample[indices]
        draws[i] = taken[:, 0].sum() / taken[:, 1].sum()
    saved = result["id_bootstrap_vs_normal_beta"]
    observed = [float(sample[:, 0].sum() / sample[:, 1].sum()),
                *[float(x) for x in np.percentile(draws, [2.5, 97.5])]]
    expected = [saved["point"], *saved["percentile_95"]]
    if not np.allclose(observed, expected, atol=1e-12, rtol=0):
        raise ValueError("ID bootstrap differs")
    overall = result["overall"]["methods"]
    days = result["days"]
    candidate = overall["pooled"]
    simple = ("old_p0", "normal_beta")
    checks = {
        "overall_brier_5pct_lower_than_both": all(
            candidate["row_brier"] <= .95 * overall[name]["row_brier"] for name in simple),
        "both_days_brier_lower_than_both": all(
            item["methods"]["pooled"]["row_brier"] < item["methods"][name]["row_brier"]
            for item in days.values() for name in simple),
        "id_equal_brier_lower_than_both": all(
            candidate["id_equal_brier"] < overall[name]["id_equal_brier"] for name in simple),
        "ap_no_drop_0p005_vs_best_simple": candidate["average_precision"] >= max(
            overall[name]["average_precision"] for name in simple) - .005,
        "both_days_top5_expected_events_no_lower": all(
            item["methods"]["pooled"]["top5_expected_events"] >=
            item["methods"][name]["top5_expected_events"]
            for item in days.values() for name in simple),
        "id_bootstrap_upper_below_zero": observed[2] < 0,
    }
    if checks != result["gate"]["checks"] or all(checks.values()) != result["gate"]["all_passed"]:
        raise ValueError("saved acceptance gate differs")
    return {"status": "VERIFIED", "rows": len(rows),
            "ids": int(rows.instance_hash.nunique()),
            "prediction_sha256": result["predictions_sha256"],
            "days": sorted(int(x) for x in rows.day.unique())}


if __name__ == "__main__":
    record = verify()
    if VERIFICATION.exists():
        if json.loads(VERIFICATION.read_text(encoding="utf-8")) != record:
            raise ValueError("independent verification changed")
    else:
        VERIFICATION.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))
