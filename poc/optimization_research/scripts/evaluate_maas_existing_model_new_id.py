"""Frozen existing MaaS model against previously unused instance-ID bucket 1.

Run selfcheck before release freeze, support after pinned extraction, then score
only when support passes. No model fitting or method selection occurs here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

import evaluate_maas_instance_risk as original
import evaluate_maas_state_followup as state
import fetch_maas_instance_bucket1 as fetcher


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/maas_existing_model_new_id_v1"
STATE = ROOT / "artifacts/maas_state_followup_v1"
OLD = ROOT / "artifacts/maas_instance_risk_v1/metrics.json"
BUCKET0 = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_sample.parquet"
BUCKET1 = fetcher.DEFAULT_OUTPUT / "instance_week_5min_bucket1.parquet"
BUCKET1_AUDIT = fetcher.DEFAULT_OUTPUT / "instance_week_5min_bucket1_audit.json"
RELEASE = OUT / "release_manifest.json"
SELFCHECK = OUT / "bucket0_selfcheck.json"
SUPPORT = OUT / "support_audit.json"
RESULT = OUT / "results.json"
PREDICTIONS = OUT / "test_predictions.parquet"
METHODS = ("old_p0", "normal_beta", "pooled", "state_normal")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normal_beta_rates(rows: pd.DataFrame) -> dict[str, float]:
    train = rows.loc[rows.split.eq("train") & rows.cohort.eq("complete")
                     & rows.current_high.eq(0)]
    return {str(key): float((group.y.sum() + 1) / (len(group) + 2))
            for key, group in train.groupby("any_recent_high")}


def selfcheck() -> dict:
    if SELFCHECK.exists() or RELEASE.exists():
        raise FileExistsError("selfcheck or release already frozen")
    metrics = json.loads((STATE / "metrics.json").read_text(encoding="utf-8"))
    old = json.loads(OLD.read_text(encoding="utf-8"))
    if (not metrics["scoring_performed"]
            or sha256(STATE / "test_predictions.parquet") != metrics["test_predictions_sha256"]
            or sha256(STATE / "cohort_rows.parquet") != metrics["cohort_rows_sha256"]
            or sha256(Path(state.__file__)) != metrics["script_sha256"]
            or sha256(OLD) != metrics["source_hashes"]["original_metrics_sha256"]
            or old["selection"]["selected_baseline"] != "any_recent_threshold"):
        raise ValueError("old fit or source changed")
    for name in ("new_pooled", "new_state_full_normal"):
        if sha256(STATE / f"{name}.joblib") != metrics["fitted_models"][name]["sha256"]:
            raise ValueError(f"saved {name} model changed")
    all_rows = pd.read_parquet(STATE / "cohort_rows.parquet")
    beta = normal_beta_rates(all_rows)
    expected_beta = {"0": 0.033061253175105165, "1": 0.7215373459028281}
    if beta != expected_beta:
        raise ValueError("strong train-only normal Beta comparator changed")
    rows = all_rows.loc[all_rows.split.eq("test") & all_rows.cohort.eq("complete")
                        & all_rows.current_high.eq(0)].copy()
    featured = state.make_features(rows, metrics["train_only_imputation_medians"])
    saved = pd.read_parquet(STATE / "test_predictions.parquet",
                            columns=["instance_hash", "decision_s", "y", "pred_old_any_recent_threshold",
                                     "pred_new_pooled", "pred_new_state_full"])
    paired = featured.merge(saved, on=["instance_hash", "decision_s", "y"],
                            how="left", validate="one_to_one")
    if len(paired) != 53_513 or paired[["pred_new_pooled", "pred_new_state_full"]].isna().any().any():
        raise ValueError("original complete-normal predictions do not pair")
    p0 = np.where(paired.any_recent_high.eq(1),
                  old["baseline_train_parameters"]["any_recent_threshold"]["1"],
                  old["baseline_train_parameters"]["any_recent_threshold"]["0"])
    pooled = joblib.load(STATE / "new_pooled.joblib").predict_proba(
        paired[list(state.FULL_FEATURES)])[:, 1]
    state_normal = joblib.load(STATE / "new_state_full_normal.joblib").predict_proba(
        paired[list(state.FULL_FEATURES)])[:, 1]
    errors = {
        "old_p0": float(np.max(np.abs(p0 - paired.pred_old_any_recent_threshold))),
        "pooled": float(np.max(np.abs(pooled - paired.pred_new_pooled))),
        "state_normal": float(np.max(np.abs(state_normal - paired.pred_new_state_full))),
    }
    if max(errors.values()) > 1e-12:
        raise ValueError("saved predictions were not reproduced")
    record = {
        "status": "PARITY_PASS", "rows": len(paired), "maximum_absolute_errors": errors,
        "normal_beta_rates": beta,
        "state_metrics_sha256": sha256(STATE / "metrics.json"),
        "state_predictions_sha256": sha256(STATE / "test_predictions.parquet"),
        "state_cohort_rows_sha256": sha256(STATE / "cohort_rows.parquet"),
        "pooled_model_sha256": sha256(STATE / "new_pooled.joblib"),
        "normal_model_sha256": sha256(STATE / "new_state_full_normal.joblib"),
        "scorer_sha256": sha256(Path(__file__)),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    SELFCHECK.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def verified_bucket1() -> tuple[pd.DataFrame, dict]:
    _, _ = fetcher.verify_release_manifest(RELEASE)
    audit = json.loads(BUCKET1_AUDIT.read_text(encoding="utf-8"))
    if (audit.get("release_manifest_sha256") != sha256(RELEASE)
            or audit.get("prior_full_source_audit_sha256") != fetcher.SOURCE_AUDIT_SHA256
            or audit.get("integrity_pass") is not True
            or audit.get("all_members_verified") is not True
            or audit.get("id_bucket") != 1
            or audit.get("source_rows") != fetcher.source.EXPECTED_ROWS
            or audit.get("compact_sha256") != sha256(BUCKET1)):
        raise ValueError("bucket 1 source or release differs from freeze")
    raw = original.load_compact(BUCKET1)
    old_ids = pd.read_parquet(BUCKET0, columns=["instance_hash"]).instance_hash.unique()
    if np.intersect1d(raw.instance_hash.unique(), old_ids).size:
        raise ValueError("bucket 1 shares a raw ID with bucket 0")
    return raw, audit


def support_summary(raw: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    examples, diagnostics = state.construct_cohorts(raw)
    test = examples.loc[examples.split.eq("test") & examples.cohort.eq("complete")
                        & examples.current_high.eq(0)].copy()
    target_profile = next((row for row in diagnostics["splits"]["test"]["cohorts"]
                           ["complete"]["by_current_high"] if row["current_high"] == 0), None)
    if target_profile is None:
        raise ValueError("complete/current-normal candidate profile missing")
    day = {str(int(key)): {"rows": len(group), "events": int(group.y.sum()),
                           "positive_ids": int(group.loc[group.y.eq(1), "instance_hash"].nunique())}
           for key, group in test.groupby("day")}
    positives = test.loc[test.y.eq(1)]
    max_share = float(positives.groupby("instance_hash").size().max() / len(positives)) if len(positives) else 0.0
    checks = {
        "rows_at_least_50000": len(test) >= 50_000,
        "events_at_least_1000": len(positives) >= 1_000,
        "ids_at_least_500": test.instance_hash.nunique() >= 500,
        "positive_ids_at_least_50": positives.instance_hash.nunique() >= 50,
        "both_days_have_20000_rows_300_events": set(day) == {"145", "146"} and all(
            item["rows"] >= 20_000 and item["events"] >= 300 for item in day.values()),
        "label_observation_at_least_75pct": target_profile["label_observation_rate"] >= .75,
    }
    answer = {
        "status": "SUPPORT_PASS" if all(checks.values()) else "SUPPORT_NO_GO_NO_SCORE",
        "rows": len(test), "events": len(positives),
        "ids": int(test.instance_hash.nunique()),
        "positive_ids": int(positives.instance_hash.nunique()),
        "maximum_positive_id_share": max_share,
        "days": day, "current_normal_candidate_profile": target_profile,
        "checks": checks,
    }
    return answer, test


def audit_support() -> dict:
    if SUPPORT.exists() or RESULT.exists():
        raise FileExistsError("new-ID support or score already exists")
    raw, source_audit = verified_bucket1()
    counts, _ = support_summary(raw)
    record = {**counts, "release_manifest_sha256": sha256(RELEASE),
              "bucket1_audit_sha256": sha256(BUCKET1_AUDIT),
              "bucket1_compact_sha256": source_audit["compact_sha256"],
              "scorer_sha256": sha256(Path(__file__))}
    OUT.mkdir(parents=True, exist_ok=True)
    SUPPORT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def tie_expected_hits(y: np.ndarray, p: np.ndarray, k: int) -> float:
    order = np.argsort(-p, kind="stable")
    score, answer = p[order], y[order]
    bounds = np.r_[np.flatnonzero(np.diff(score) != 0) + 1, len(score)]
    total, start = 0.0, 0
    for end in bounds:
        selected = min(max(k - start, 0), end - start)
        if selected:
            total += float(answer[start:end].sum()) * selected / (end - start)
        if end >= k:
            break
        start = end
    return total


def metrics(frame: pd.DataFrame) -> dict:
    y = frame.y.to_numpy(dtype=int)
    ids = frame.instance_hash.to_numpy(str)
    k = int(np.ceil(.05 * len(frame)))
    result = {"rows": len(frame), "events": int(y.sum()), "methods": {}}
    for method in METHODS:
        p = frame[method].to_numpy(float)
        losses = (y - p) ** 2
        result["methods"][method] = {
            "row_brier": float(losses.mean()),
            "id_equal_brier": float(pd.Series(losses).groupby(ids).mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
            "top5_alerts": k,
            "top5_expected_events": tie_expected_hits(y, p, k),
        }
    return result


def id_bootstrap_delta(frame: pd.DataFrame, *, seed: int = 43, draws: int = 2000) -> dict:
    y = frame.y.to_numpy(float)
    data = pd.DataFrame({
        "id": frame.instance_hash,
        "count": 1,
        "diff": (y - frame.pooled.to_numpy(float)) ** 2 -
                (y - frame.normal_beta.to_numpy(float)) ** 2,
    }).groupby("id").agg(count=("count", "sum"), diff=("diff", "sum"))
    n = data["count"].to_numpy(int)
    diff = data["diff"].to_numpy(float)
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for index in range(draws):
        take = rng.integers(0, len(n), len(n))
        samples[index] = diff[take].sum() / n[take].sum()
    return {"seed": seed, "draws": draws, "ids": len(n),
            "point": float(diff.sum() / n.sum()),
            "percentile_95": [float(x) for x in np.percentile(samples, [2.5, 97.5])]}


def score_once() -> dict:
    frozen = json.loads(SUPPORT.read_text(encoding="utf-8"))
    if (frozen["status"] != "SUPPORT_PASS" or RESULT.exists() or PREDICTIONS.exists()
            or frozen["release_manifest_sha256"] != sha256(RELEASE)
            or frozen["bucket1_audit_sha256"] != sha256(BUCKET1_AUDIT)
            or frozen["scorer_sha256"] != sha256(Path(__file__))):
        raise ValueError("support did not pass or frozen code/source changed")
    raw, audit = verified_bucket1()
    if audit["compact_sha256"] != frozen["bucket1_compact_sha256"]:
        raise ValueError("new-ID source changed after support audit")
    summary, test = support_summary(raw)
    for key in ("status", "rows", "events", "ids", "positive_ids", "days", "checks",
                "current_normal_candidate_profile", "maximum_positive_id_share"):
        if summary[key] != frozen[key]:
            raise ValueError("support cohort changed before scoring")
    state_metrics = json.loads((STATE / "metrics.json").read_text(encoding="utf-8"))
    old_metrics = json.loads(OLD.read_text(encoding="utf-8"))
    beta = json.loads(SELFCHECK.read_text(encoding="utf-8"))["normal_beta_rates"]
    featured = state.make_features(test, state_metrics["train_only_imputation_medians"])
    high = featured.any_recent_high.to_numpy(dtype=int)
    old_rates = old_metrics["baseline_train_parameters"]["any_recent_threshold"]
    featured["old_p0"] = np.where(high == 1, old_rates["1"], old_rates["0"])
    featured["normal_beta"] = np.where(high == 1, beta["1"], beta["0"])
    featured["pooled"] = joblib.load(STATE / "new_pooled.joblib").predict_proba(
        featured[list(state.FULL_FEATURES)])[:, 1]
    featured["state_normal"] = joblib.load(STATE / "new_state_full_normal.joblib").predict_proba(
        featured[list(state.FULL_FEATURES)])[:, 1]
    if (not featured[list(METHODS)].apply(np.isfinite).all().all()
            or featured[list(METHODS)].lt(0).any().any()
            or featured[list(METHODS)].gt(1).any().any()):
        raise ValueError("invalid saved-model probability")
    featured = featured.sort_values(["decision_s", "instance_hash"], kind="mergesort")
    overall = metrics(featured)
    days = {str(int(key)): metrics(part) for key, part in featured.groupby("day")}
    main = overall["methods"]["pooled"]
    comparators = ("old_p0", "normal_beta")
    ci = id_bootstrap_delta(featured)
    checks = {
        "overall_brier_5pct_lower_than_both": all(
            main["row_brier"] <= .95 * overall["methods"][name]["row_brier"]
            for name in comparators),
        "both_days_brier_lower_than_both": all(
            entry["methods"]["pooled"]["row_brier"] < entry["methods"][name]["row_brier"]
            for entry in days.values() for name in comparators),
        "id_equal_brier_lower_than_both": all(
            main["id_equal_brier"] < overall["methods"][name]["id_equal_brier"]
            for name in comparators),
        "ap_no_drop_0p005_vs_best_simple": main["average_precision"] >= max(
            overall["methods"][name]["average_precision"] for name in comparators) - .005,
        "both_days_top5_expected_events_no_lower": all(
            entry["methods"]["pooled"]["top5_expected_events"] >=
            entry["methods"][name]["top5_expected_events"]
            for entry in days.values() for name in comparators),
        "id_bootstrap_upper_below_zero": ci["percentile_95"][1] < 0,
    }
    keep = ["instance_hash", "decision_s", "day", "y", *METHODS]
    featured[keep].to_parquet(PREDICTIONS, index=False, compression="zstd")
    record = {
        "status": "NEW_ID_SIGNAL_GO" if all(checks.values()) else "NEW_ID_SIGNAL_NO_GO",
        "release_manifest_sha256": sha256(RELEASE),
        "support_audit_sha256": sha256(SUPPORT),
        "predictions_sha256": sha256(PREDICTIONS),
        "scorer_sha256": sha256(Path(__file__)),
        "scope": "bucket1 new IDs in same MaaS week; observed 30min GPU threshold only",
        "overall": overall, "days": days, "id_bootstrap_vs_normal_beta": ci,
        "gate": {"all_passed": bool(all(checks.values())), "checks": checks},
    }
    RESULT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("selfcheck", "support", "score_once"))
    args = parser.parse_args()
    result = {"selfcheck": selfcheck, "support": audit_support, "score_once": score_once}[args.command]()
    print(json.dumps({"status": result["status"], "rows": result.get("rows"),
                      "gate": result.get("gate", result.get("checks"))}, sort_keys=True))


if __name__ == "__main__":
    main()
