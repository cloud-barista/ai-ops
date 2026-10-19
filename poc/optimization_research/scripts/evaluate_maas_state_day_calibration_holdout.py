"""One-shot new-ID test of fixed state-conditioned MaaS day-ahead calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import evaluate_maas_instance_risk as original
import evaluate_maas_state_followup as state
import explore_maas_state_day_calibration as development
import fetch_maas_instance_bucket2 as fetcher


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/maas_state_day_calibration_holdout_v1"
PREFLIGHT = OUT / "preflight.json"
RELEASE = OUT / "release_manifest.json"
SUPPORT = OUT / "support_audit.json"
RESULT = OUT / "results.json"
PREDICTIONS = OUT / "test_predictions.parquet"
BUCKET0 = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_sample.parquet"
BUCKET1 = ROOT / "data/external/alibaba_maas_2026_bucket1/instance_week_5min_bucket1.parquet"
TRAIN_BETA = {0: 0.033061253175105165, 1: 0.7215373459028281}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preflight() -> dict:
    if PREFLIGHT.exists() or RELEASE.exists():
        raise FileExistsError("bucket2 preflight or release already exists")
    metrics = json.loads((development.STATE / "metrics.json").read_text(encoding="utf-8"))
    existing = json.loads(development.RESULT.read_text(encoding="utf-8"))
    if (existing["status"] != "EXPOSED_COHORT_DEVELOPMENT_ONLY"
            or existing["script_sha256"] != sha256(Path(development.__file__))
            or existing["model_sha256"] != sha256(development.STATE / "new_pooled.joblib")
            or metrics["fitted_models"]["new_pooled"]["sha256"] != existing["model_sha256"]):
        raise ValueError("exposed development/model changed")
    model = joblib.load(development.STATE / "new_pooled.joblib")
    medians = metrics["train_only_imputation_medians"]
    errors = {}
    for bucket in ("bucket0", "bucket1"):
        featured = development.load_cohort(bucket, model, medians)
        test = featured.loc[featured.day.ge(145), ["instance_hash", "decision_s", "y", "pooled"]]
        if bucket == "bucket0":
            saved = pd.read_parquet(development.BUCKET0_PRED)
            saved = saved.loc[saved.cohort.eq("complete") & saved.current_high.eq(0),
                              ["instance_hash", "decision_s", "y", "pred_new_pooled"]]
            name = "pred_new_pooled"
        else:
            saved = pd.read_parquet(development.BUCKET1_PRED,
                                    columns=["instance_hash", "decision_s", "y", "pooled"])
            saved = saved.rename(columns={"pooled": "saved_pooled"})
            name = "saved_pooled"
        paired = test.merge(saved, on=["instance_hash", "decision_s", "y"],
                            how="left", validate="one_to_one")
        if len(paired) != len(saved) or paired[name].isna().any():
            raise ValueError("saved old-bucket predictions incomplete")
        errors[bucket] = float(np.max(np.abs(paired.pooled - paired[name])))
    if max(errors.values()) > 1e-12:
        raise ValueError("saved predictions were not reproduced")
    record = {"status": "PREFLIGHT_PASS", "saved_prediction_max_abs_error": max(errors.values()),
              "by_bucket_max_abs_error": errors, "scorer_sha256": sha256(Path(__file__)),
              "development_result_sha256": sha256(development.RESULT),
              "model_sha256": sha256(development.STATE / "new_pooled.joblib")}
    OUT.mkdir(parents=True, exist_ok=True)
    PREFLIGHT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def verified_raw() -> tuple[pd.DataFrame, dict]:
    release_sha, _ = fetcher.verify_release(RELEASE)
    audit = json.loads(fetcher.AUDIT.read_text(encoding="utf-8"))
    if (audit.get("release_manifest_sha256") != release_sha
            or audit.get("prior_full_source_audit_sha256") != fetcher.SOURCE_AUDIT_SHA256
            or audit.get("integrity_pass") is not True
            or audit.get("all_members_verified") is not True
            or audit.get("source_rows") != fetcher.source.EXPECTED_ROWS
            or audit.get("id_bucket") != fetcher.BUCKET
            or audit.get("compact_sha256") != sha256(fetcher.COMPACT)):
        raise ValueError("new bucket2 source failed pinned audit")
    raw = original.load_compact(fetcher.COMPACT)
    new_ids = raw.instance_hash.unique()
    for path in (BUCKET0, BUCKET1):
        old_ids = pd.read_parquet(path, columns=["instance_hash"]).instance_hash.unique()
        if np.intersect1d(new_ids, old_ids).size:
            raise ValueError("bucket2 ID overlaps an exposed cohort")
    return raw, audit


def support_summary(raw: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    examples, diagnostics = state.construct_cohorts(raw)
    normal = examples.loc[examples.cohort.eq("complete") & examples.current_high.eq(0)
                          & examples.day.between(144, 146)].copy()
    test = normal.loc[normal.day.ge(145)]
    profile = next((entry for entry in diagnostics["splits"]["test"]["cohorts"]
                    ["complete"]["by_current_high"] if entry["current_high"] == 0), None)
    if profile is None:
        raise ValueError("bucket2 normal candidate profile missing")
    daily = {str(int(day)): {"rows": len(group), "events": int(group.y.sum())}
             for day, group in test.groupby("day")}
    matured = {}
    for day in (145, 146):
        prior = development.matured_previous_day(normal, day)
        by_state = {str(int(h)): {"rows": len(group), "positive": int(group.y.sum()),
                                   "negative": int(len(group) - group.y.sum())}
                    for h, group in prior.groupby("any_recent_high")}
        matured[str(day)] = {"rows": len(prior), "states": by_state}
    positive = test.loc[test.y.eq(1)]
    checks = {
        "rows_at_least_50000": len(test) >= 50_000,
        "events_at_least_1000": len(positive) >= 1_000,
        "ids_at_least_500": test.instance_hash.nunique() >= 500,
        "positive_ids_at_least_50": positive.instance_hash.nunique() >= 50,
        "both_days_20000_rows_300_events": set(daily) == {"145", "146"} and all(
            d["rows"] >= 20_000 and d["events"] >= 300 for d in daily.values()),
        "label_observation_at_least_75pct": profile["label_observation_rate"] >= .75,
        "matured_both_states_supported": all(
            set(matured[str(day)]["states"]) == {"0", "1"} and all(
                v["rows"] >= 300 and v["positive"] >= 30 and v["negative"] >= 30
                for v in matured[str(day)]["states"].values())
            for day in (145, 146)),
    }
    answer = {"status": "SUPPORT_PASS" if all(checks.values()) else "SUPPORT_NO_GO_NO_SCORE",
              "rows": len(test), "events": len(positive),
              "ids": int(test.instance_hash.nunique()),
              "positive_ids": int(positive.instance_hash.nunique()),
              "days": daily, "matured_previous_day": matured,
              "candidate_profile": profile, "checks": checks}
    return answer, normal


def audit_support() -> dict:
    if SUPPORT.exists() or RESULT.exists():
        raise FileExistsError("bucket2 support or score already exists")
    raw, source_audit = verified_raw()
    counts, _ = support_summary(raw)
    record = {**counts, "release_manifest_sha256": sha256(RELEASE),
              "bucket2_audit_sha256": sha256(fetcher.AUDIT),
              "bucket2_compact_sha256": source_audit["compact_sha256"],
              "scorer_sha256": sha256(Path(__file__))}
    OUT.mkdir(parents=True, exist_ok=True)
    SUPPORT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def acceptance(overall: dict, days: dict, bootstrap: dict) -> dict:
    primary = overall["methods"]["model_last"]
    other = ("pooled", "global_exact", "last_beta")
    return {
        "overall_brier_2pct_better_than_three": all(
            primary["brier"] <= .98 * overall["methods"][name]["brier"] for name in other),
        "both_days_brier_lower_than_three": all(
            item["methods"]["model_last"]["brier"] < item["methods"][name]["brier"]
            for item in days.values() for name in other),
        "id_equal_brier_lower_than_three": all(
            primary["id_equal_brier"] < overall["methods"][name]["id_equal_brier"]
            for name in other),
        "ap_drop_less_than_0p005_vs_pooled_and_state_exact": all(
            primary["average_precision"] >= overall["methods"][name]["average_precision"] - .005
            for name in ("pooled", "state_exact")),
        "id_bootstrap_upper_below_zero_vs_pooled_and_global": all(
            bootstrap[name]["percentile_95"][1] < 0 for name in ("pooled", "global_exact")),
        "not_more_than_0p5pct_worse_than_state_exact":
            primary["brier"] <= 1.005 * overall["methods"]["state_exact"]["brier"],
    }


def score_once() -> dict:
    frozen = json.loads(SUPPORT.read_text(encoding="utf-8"))
    if (frozen["status"] != "SUPPORT_PASS" or RESULT.exists() or PREDICTIONS.exists()
            or frozen["release_manifest_sha256"] != sha256(RELEASE)
            or frozen["bucket2_audit_sha256"] != sha256(fetcher.AUDIT)
            or frozen["scorer_sha256"] != sha256(Path(__file__))):
        raise ValueError("bucket2 support/code did not remain frozen")
    raw, audit = verified_raw()
    if frozen["bucket2_compact_sha256"] != audit["compact_sha256"]:
        raise ValueError("bucket2 source changed after support audit")
    support, normal = support_summary(raw)
    for key in ("status", "rows", "events", "ids", "positive_ids", "days",
                "matured_previous_day", "candidate_profile", "checks"):
        if support[key] != frozen[key]:
            raise ValueError("bucket2 scored cohort changed after support audit")
    state_metrics = json.loads((development.STATE / "metrics.json").read_text(encoding="utf-8"))
    featured = state.make_features(normal, state_metrics["train_only_imputation_medians"])
    model = joblib.load(development.STATE / "new_pooled.joblib")
    featured["pooled"] = model.predict_proba(featured[list(state.FULL_FEATURES)])[:, 1]
    groups, prior_summary = [], {}
    for day in (145, 146):
        today = featured.loc[featured.day.eq(day)]
        previous = development.matured_previous_day(featured, day)
        predicted, info = development.predict_day(today, previous, TRAIN_BETA)
        if info["previous_day_mature_rows"] != frozen["matured_previous_day"][str(day)]["rows"]:
            raise ValueError("previous-day maturity support changed")
        groups.append(predicted)
        prior_summary[str(day)] = info
    rows = pd.concat(groups, ignore_index=True).sort_values(
        ["decision_s", "instance_hash"], kind="mergesort")
    if (len(rows) != frozen["rows"] or rows.duplicated(["instance_hash", "decision_s"]).any()
            or not rows.y.isin([0, 1]).all()
            or not np.isfinite(rows[list(development.METHODS)].to_numpy(float)).all()):
        raise ValueError("bucket2 prediction rows invalid")
    overall = development.scores(rows)
    days = {str(int(day)): development.scores(group)
            for day, group in rows.groupby("day")}
    boot = {name: development.bootstrap(rows, name)
            for name in ("pooled", "global_exact")}
    checks = acceptance(overall, days, boot)
    rows.to_parquet(PREDICTIONS, index=False, compression="zstd")
    record = {"status": "NEW_ID_STATE_DAY_GO" if all(checks.values()) else "NEW_ID_STATE_DAY_NO_GO",
              "scope": "bucket2 new IDs in same MaaS week; observed GPU threshold risk only",
              "release_manifest_sha256": sha256(RELEASE),
              "support_audit_sha256": sha256(SUPPORT),
              "scorer_sha256": sha256(Path(__file__)),
              "predictions_sha256": sha256(PREDICTIONS),
              "overall": overall, "days": days, "matured_prior": prior_summary,
              "id_bootstrap": boot,
              "gate": {"all_passed": bool(all(checks.values())), "checks": checks}}
    RESULT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "support", "score_once"))
    args = parser.parse_args()
    action = {"preflight": preflight, "support": audit_support, "score_once": score_once}[args.command]
    result = action()
    print(json.dumps({"status": result["status"], "rows": result.get("rows"),
                      "gate": result.get("gate", result.get("checks"))}, sort_keys=True))
