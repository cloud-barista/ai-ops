"""Recalculate new-ID MaaS scores and replay prior-day probability shifts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score

import evaluate_maas_instance_risk as original
import evaluate_maas_state_followup as state
from compare_philly_day_ahead_baselines_posthoc import exact_shift


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/maas_state_day_calibration_holdout_v1"
RESULT = OUT / "results.json"
PREDICTIONS = OUT / "test_predictions.parquet"
SUPPORT = OUT / "support_audit.json"
VERIFICATION = OUT / "independent_verification.json"
SOURCE = ROOT / "data/external/alibaba_maas_2026_bucket2/instance_week_5min_bucket2.parquet"
STATE = ROOT / "artifacts/maas_state_followup_v1"
METHODS = ("frozen_beta", "pooled", "last_beta", "global_exact", "state_exact", "model_last")
BETA = {0: 0.033061253175105165, 1: 0.7215373459028281}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scores(rows: pd.DataFrame) -> dict:
    y = rows.y.to_numpy(float)
    methods = {}
    for name in METHODS:
        p = rows[name].to_numpy(float)
        squared = (y - p) ** 2
        means = pd.DataFrame({"id": rows.instance_hash.to_numpy(str),
                              "squared": squared}).groupby("id").squared.mean()
        methods[name] = {"brier": float(squared.mean()),
                         "id_equal_brier": float(means.mean()),
                         "average_precision": float(average_precision_score(y, p))}
    return {"rows": len(rows), "events": int(y.sum()),
            "ids": int(rows.instance_hash.nunique()), "methods": methods}


def compare(observed: dict, saved: dict, context: str) -> None:
    if any(observed[key] != saved[key] for key in ("rows", "events", "ids")):
        raise ValueError(f"{context} support counts differ")
    for name in METHODS:
        for metric, value in observed["methods"][name].items():
            if not np.isclose(value, saved["methods"][name][metric], atol=1e-12, rtol=0):
                raise ValueError(f"{context}.{name}.{metric} differs")


def bootstrap(rows: pd.DataFrame, other: str) -> tuple[float, list[float]]:
    y = rows.y.to_numpy(float)
    delta = ((y - rows.model_last.to_numpy(float)) ** 2 -
             (y - rows[other].to_numpy(float)) ** 2)
    group = pd.DataFrame({"id": rows.instance_hash.to_numpy(str), "delta": delta}).groupby("id")
    totals = group.delta.sum().to_numpy(float)
    counts = group.size().to_numpy(int)
    rng = np.random.default_rng(43)
    samples = np.empty(2000)
    for index in range(len(samples)):
        draw = rng.integers(0, len(counts), len(counts))
        samples[index] = totals[draw].sum() / counts[draw].sum()
    return float(totals.sum() / counts.sum()), [float(x) for x in np.percentile(samples, [2.5, 97.5])]


def replay(rows: pd.DataFrame) -> dict:
    metrics = json.loads((STATE / "metrics.json").read_text(encoding="utf-8"))
    raw = original.load_compact(SOURCE)
    cohort, _ = state.construct_cohorts(raw)
    normal = cohort.loc[cohort.cohort.eq("complete") & cohort.current_high.eq(0)
                         & cohort.day.between(144, 146)].copy()
    features = state.make_features(normal, metrics["train_only_imputation_medians"])
    model = joblib.load(STATE / "new_pooled.joblib")
    features["p"] = model.predict_proba(features[list(state.FULL_FEATURES)])[:, 1]
    maxima = {name: 0.0 for name in METHODS}
    for day in (145, 146):
        now = features.loc[features.day.eq(day)]
        earlier = features.loc[features.day.eq(day - 1)
                               & features.decision_s.add(2100).le(day * 86400)]
        if earlier.empty or earlier.decision_s.max() + 2100 > day * 86400:
            raise ValueError("prior-day label maturity failed")
        groups = {int(h): part for h, part in earlier.groupby("any_recent_high")}
        if set(groups) != {0, 1}:
            raise ValueError("prior day lacks a state")
        rates = {h: float((part.y.sum() + 1) / (len(part) + 2)) for h, part in groups.items()}
        shifts = {h: exact_shift(part.y.to_numpy(float), part.p.to_numpy(float))
                  for h, part in groups.items()}
        global_shift = exact_shift(earlier.y.to_numpy(float), earlier.p.to_numpy(float))
        current = now[["instance_hash", "decision_s", "y", "any_recent_high", "p"]].merge(
            rows.loc[rows.day.eq(day), ["instance_hash", "decision_s", "y", *METHODS]],
            on=["instance_hash", "decision_s", "y"], how="left", validate="one_to_one")
        if len(current) != len(rows.loc[rows.day.eq(day)]) or current.pooled.isna().any():
            raise ValueError("saved new-ID predictions do not pair to source")
        h = current.any_recent_high.to_numpy(int)
        p = current.p.to_numpy(float)
        z = logit(np.clip(p, 1e-9, 1 - 1e-9))
        base = np.where(h == 1, BETA[1], BETA[0])
        prior_rate = np.where(h == 1, rates[1], rates[0])
        expected = {
            "frozen_beta": base,
            "pooled": p,
            "last_beta": prior_rate,
            "global_exact": expit(z + global_shift),
            "state_exact": expit(z + np.where(h == 1, shifts[1], shifts[0])),
            "model_last": expit(z + logit(prior_rate) - logit(base)),
        }
        for name, value in expected.items():
            maxima[name] = max(maxima[name], float(np.max(np.abs(
                current[name].to_numpy(float) - value))))
    if any(value > 1e-12 for value in maxima.values()):
        raise ValueError("causal previous-day prediction replay differs")
    return maxima


def verify() -> dict:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if (sha256(PREDICTIONS) != result["predictions_sha256"]
            or sha256(SUPPORT) != result["support_audit_sha256"]
            or json.loads(SUPPORT.read_text(encoding="utf-8"))["status"] != "SUPPORT_PASS"):
        raise ValueError("saved new-ID score or support changed")
    rows = pd.read_parquet(PREDICTIONS)
    if (rows.duplicated(["instance_hash", "decision_s"]).any()
            or set(rows.day.unique()) != {145, 146}
            or not rows.day.eq(rows.decision_s // 86400).all()
            or not rows.y.isin([0, 1]).all()
            or not np.isfinite(rows[list(METHODS)].to_numpy(float)).all()):
        raise ValueError("invalid saved prediction rows")
    compare(scores(rows), result["overall"], "overall")
    for day, part in rows.groupby("day"):
        compare(scores(part), result["days"][str(int(day))], f"day{day}")
    for name in ("pooled", "global_exact"):
        point, interval = bootstrap(rows, name)
        saved = result["id_bootstrap"][name]
        if (not np.isclose(point, saved["point"], atol=1e-12, rtol=0)
                or not np.allclose(interval, saved["percentile_95"], atol=1e-12, rtol=0)):
            raise ValueError("saved ID bootstrap differs")
    maxima = replay(rows)
    return {"status": "VERIFIED", "rows": len(rows),
            "ids": int(rows.instance_hash.nunique()),
            "prediction_sha256": result["predictions_sha256"],
            "max_absolute_replay_difference": maxima}


if __name__ == "__main__":
    answer = verify()
    if VERIFICATION.exists():
        if json.loads(VERIFICATION.read_text(encoding="utf-8")) != answer:
            raise ValueError("saved independent verification changed")
    else:
        VERIFICATION.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(answer, sort_keys=True))
