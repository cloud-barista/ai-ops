"""Exploratory transfer of Philly day-ahead residual calibration to MaaS."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score

import evaluate_maas_instance_risk as source
from compare_philly_day_ahead_baselines_posthoc import exact_shift


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/maas_day_ahead_instance_transfer_v1"
PROTOCOL = ROOT / "reports/maas_day_ahead_instance_transfer_protocol.md"
SUPPORT = OUT / "support_audit.json"
RESULT = OUT / "results.json"
PREDICTIONS = OUT / "test_normal_predictions.parquet"
ORIGINAL = ROOT / "artifacts/maas_instance_risk_v1/metrics.json"
ORIGINAL_PREDICTIONS = ROOT / "artifacts/maas_instance_risk_v1/test_predictions.parquet"
METHODS = ("p0", "previous_day_global_exact", "previous_day_global_instance")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare() -> tuple[pd.DataFrame, dict, dict]:
    source_info = source.verify_source(source.SOURCE, source.AUDIT)
    raw = source.load_compact(source.SOURCE)
    examples, diagnostics = source.construct_examples(raw)
    if (len(examples.loc[examples.split.eq("test") & examples.current_threshold.eq(0)])
            != 53_513):
        raise ValueError("normal-state original cohort changed")
    return examples, diagnostics, source_info


def hourly_anchors(examples: pd.DataFrame) -> pd.DataFrame:
    normal = examples.loc[examples.current_threshold.eq(0)].copy()
    if normal.duplicated(["instance_hash", "decision_s"]).any():
        raise ValueError("duplicate eligible instance decision")
    normal = normal.sort_values(["instance_hash", "decision_s"], kind="mergesort")
    normal["utc_hour"] = normal.decision_s // 3600
    anchors = normal.drop_duplicates(["instance_hash", "utc_hour"], keep="first")
    return anchors.copy()


def preceding(anchors: pd.DataFrame, day: int) -> pd.DataFrame:
    return anchors.loc[
        anchors.day.eq(day - 1)
        & anchors.decision_s.add(source.HORIZON_S + source.LABEL_DELIVERY_DELAY_S)
        .le(day * 86400)]


def support_counts(examples: pd.DataFrame, anchors: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    test = examples.loc[examples.split.eq("test") & examples.current_threshold.eq(0)].copy()
    groups = []
    for day in sorted(int(x) for x in test.day.unique()):
        prior = preceding(anchors, day)
        counts = prior.groupby("instance_hash").size().rename("prior_hourly_labels")
        current = test.loc[test.day.eq(day)].copy()
        current["prior_hourly_labels"] = current.instance_hash.map(counts).fillna(0).astype(int)
        groups.append(current)
    joined = pd.concat(groups, ignore_index=True)
    supported = joined.prior_hourly_labels.ge(6)
    checks = {
        "normal_rows_at_least_50000": len(joined) >= 50_000,
        "normal_events_at_least_1000": int(joined.y.sum()) >= 1_000,
        "six_anchor_rows_at_least_20000": int(supported.sum()) >= 20_000,
        "six_anchor_events_at_least_500": int(joined.loc[supported, "y"].sum()) >= 500,
    }
    return {
        "status": "SUPPORT_PASS" if all(checks.values()) else "SUPPORT_NO_GO_NO_SCORE",
        "normal_rows": len(joined), "normal_events": int(joined.y.sum()),
        "normal_instances": int(joined.instance_hash.nunique()),
        "six_anchor_rows": int(supported.sum()),
        "six_anchor_events": int(joined.loc[supported, "y"].sum()),
        "six_anchor_instances": int(joined.loc[supported, "instance_hash"].nunique()),
        "by_day": [{"day": int(day), "normal_rows": len(part),
                    "normal_events": int(part.y.sum()),
                    "six_anchor_rows": int(part.prior_hourly_labels.ge(6).sum()),
                    "six_anchor_events": int(part.loc[part.prior_hourly_labels.ge(6), "y"].sum())}
                   for day, part in joined.groupby("day")],
        "checks": checks,
    }, joined


def audit() -> dict:
    if SUPPORT.exists() or RESULT.exists():
        raise FileExistsError("MaaS transfer support or score already exists")
    examples, diagnostics, source_info = prepare()
    summary, _ = support_counts(examples, hourly_anchors(examples))
    record = {
        **summary, "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "compact_sha256": source_info["compact_sha256"],
        "source_audit_sha256": source_info["audit_sha256"],
        "source_eligible_test_rows": diagnostics["splits"]["test"]["eligible"],
        "original_metrics_sha256": sha256(ORIGINAL),
        "original_predictions_sha256": sha256(ORIGINAL_PREDICTIONS),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    SUPPORT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def predictions_for_day(target: pd.DataFrame, anchors: pd.DataFrame,
                        parameters: dict, day: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Use prior-day mature hourly normal anchors; target y is never read."""
    previous = preceding(anchors, day)
    p0 = source.baseline_predict(target, parameters, "any_recent_threshold")
    if previous.empty:
        return p0.copy(), p0.copy(), 0
    previous_p0 = source.baseline_predict(previous, parameters, "any_recent_threshold")
    shift = exact_shift(previous.y.to_numpy(dtype=float), previous_p0)
    corrected = expit(logit(np.clip(previous_p0, 1e-9, 1 - 1e-9)) + shift)
    prior = previous[["instance_hash"]].copy()
    prior["residual"] = previous.y.to_numpy(dtype=float) - corrected
    prior["variance"] = corrected * (1.0 - corrected)
    aggregated = prior.groupby("instance_hash").agg(
        residual=("residual", "sum"), variance=("variance", "sum"))
    shifts = (aggregated.residual / (aggregated.variance + 20.0)).clip(-.5, .5)
    current_shift = target.instance_hash.map(shifts).fillna(0).to_numpy(dtype=float)
    z = logit(np.clip(p0, 1e-9, 1 - 1e-9)) + shift
    return expit(z), expit(z + current_shift), len(previous)


def scores(frame: pd.DataFrame) -> dict:
    y = frame.y.to_numpy(dtype=float)
    machine = frame.instance_hash.to_numpy(str)
    outcome = {}
    for method in METHODS:
        p = frame[method].to_numpy(dtype=float)
        loss = (y - p) ** 2
        outcome[method] = {
            "row_brier": float(loss.mean()),
            "instance_equal_brier": float(pd.Series(loss).groupby(machine).mean().mean()),
            "average_precision": float(average_precision_score(y, p)),
        }
    return outcome


def score() -> dict:
    frozen = json.loads(SUPPORT.read_text())
    if (frozen.get("status") != "SUPPORT_PASS" or RESULT.exists() or PREDICTIONS.exists()
            or frozen["protocol_sha256"] != sha256(PROTOCOL)
            or frozen["script_sha256"] != sha256(Path(__file__))
            or frozen["original_metrics_sha256"] != sha256(ORIGINAL)
            or frozen["original_predictions_sha256"] != sha256(ORIGINAL_PREDICTIONS)):
        raise ValueError("support or fixed code changed before score")
    examples, _, source_info = prepare()
    if (source_info["compact_sha256"] != frozen["compact_sha256"]
            or source_info["audit_sha256"] != frozen["source_audit_sha256"]):
        raise ValueError("verified MaaS source changed")
    anchors = hourly_anchors(examples)
    support, test = support_counts(examples, anchors)
    for key in ("status", "normal_rows", "normal_events", "normal_instances",
                "six_anchor_rows", "six_anchor_events", "six_anchor_instances", "by_day", "checks"):
        if support[key] != frozen[key]:
            raise ValueError("score cohort differs from support audit")
    original = json.loads(ORIGINAL.read_text())
    if original["selection"]["selected_baseline"] != "any_recent_threshold":
        raise ValueError("old validation-selected baseline changed")
    trained = source.fit_baselines(examples.loc[examples.split.eq("train")])
    if trained != original["baseline_train_parameters"]:
        raise ValueError("previous training-only baseline coefficients changed")
    saved = pd.read_parquet(ORIGINAL_PREDICTIONS)
    paired = test[["instance_hash", "decision_s", "y"]].merge(
        saved[["instance_hash", "decision_s", "y", "pred_any_recent_threshold"]],
        on=["instance_hash", "decision_s", "y"], how="left", validate="one_to_one")
    expected_p0 = source.baseline_predict(test, trained, "any_recent_threshold")
    if (paired.pred_any_recent_threshold.isna().any()
            or not np.array_equal(expected_p0, paired.pred_any_recent_threshold.to_numpy(float))):
        raise ValueError("saved previous test baseline not reproduced")
    test = test.sort_values(["decision_s", "instance_hash"], kind="mergesort").reset_index(drop=True)
    test["p0"] = source.baseline_predict(test, trained, "any_recent_threshold")
    test["previous_day_global_exact"] = np.nan
    test["previous_day_global_instance"] = np.nan
    prior_counts = []
    for day in sorted(int(x) for x in test.day.unique()):
        mask = test.day.eq(day).to_numpy()
        global_p, local_p, n = predictions_for_day(test.loc[mask], anchors, trained, day)
        test.loc[mask, "previous_day_global_exact"] = global_p
        test.loc[mask, "previous_day_global_instance"] = local_p
        prior_counts.append({"day": day, "previous_day_hourly_normal_anchors": n})
    if test[list(METHODS)].isna().any().any():
        raise ValueError("missing method probability")
    overall = scores(test)
    by_day = {str(int(day)): scores(part) for day, part in test.groupby("day")}
    supported = test.loc[test.prior_hourly_labels.ge(6)]
    supported_scores = scores(supported)
    global_score, candidate = overall["previous_day_global_exact"], overall["previous_day_global_instance"]
    base = overall["p0"]
    losses = pd.DataFrame({
        "instance_hash": test.instance_hash,
        "global_loss": (test.y - test.previous_day_global_exact) ** 2,
        "candidate_loss": (test.y - test.previous_day_global_instance) ** 2,
    }).groupby("instance_hash").mean()
    wins = int((losses.candidate_loss < losses.global_loss).sum())
    checks = {
        "overall_5pct_better_than_both": candidate["row_brier"] <= .95 * min(
            base["row_brier"], global_score["row_brier"]),
        "both_days_better_than_both": all(
            by_day[day]["previous_day_global_instance"]["row_brier"]
            < min(by_day[day]["p0"]["row_brier"],
                  by_day[day]["previous_day_global_exact"]["row_brier"])
            for day in by_day),
        "instance_equal_brier_not_worse_than_both": candidate["instance_equal_brier"]
            <= min(base["instance_equal_brier"], global_score["instance_equal_brier"]),
        "ap_drop_less_than_0p005_vs_both": candidate["average_precision"]
            > max(base["average_precision"], global_score["average_precision"]) - .005,
        "six_anchor_5pct_better_than_global": supported_scores[
            "previous_day_global_instance"]["row_brier"] <= .95 * supported_scores[
                "previous_day_global_exact"]["row_brier"],
    }
    rows = test[["instance_hash", "decision_s", "day", "y", "prior_hourly_labels",
                 *METHODS]].copy()
    rows.to_parquet(PREDICTIONS, index=False, compression="zstd")
    result = {
        "status": "EXPLORATORY_GO_TO_NEW_ID_COLLECTION" if all(checks.values())
                  else "EXPLORATORY_NO_GO",
        "scope": "mixed MaaS instances; observed 30min SM or GPU memory threshold event",
        "support_audit_sha256": sha256(SUPPORT),
        "source_compact_sha256": source_info["compact_sha256"],
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "predictions_sha256": sha256(PREDICTIONS),
        "test_rows": len(test), "test_instances": test.instance_hash.nunique(),
        "supported_rows": len(supported),
        "prior_anchor_counts": prior_counts,
        "scores": overall, "by_day": by_day,
        "six_anchor_scores": supported_scores,
        "candidate_better_instances": wins,
        "gate": {"all_passed": bool(all(checks.values())), "checks": checks},
        "original_test_was_exposed_before_method_design": True,
    }
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "score"))
    args = parser.parse_args()
    result = audit() if args.command == "audit" else score()
    print(json.dumps({"status": result["status"], "support": result.get("checks"),
                      "scores": result.get("scores"), "gate": result.get("gate")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
