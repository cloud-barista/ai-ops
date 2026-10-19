"""Post-score, instance-resampled sensitivity audit for the MaaS follow-up.

This audit is descriptive. It is deliberately separate from the frozen model
selection and success criterion in evaluate_maas_state_followup.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/maas_state_followup_v1"
RESAMPLES = 2_000
SEED = 42


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def instance_bootstrap(frame: pd.DataFrame, comparator: str, *, seed: int) -> dict:
    """Paired Brier(new minus comparator), resampling whole instance histories."""
    y = frame.y.to_numpy(float)
    new = frame.pred_new_state_full.to_numpy(float)
    old = frame[comparator].to_numpy(float)
    delta = (y - new) ** 2 - (y - old) ** 2
    grouped = pd.DataFrame({"instance_hash": frame.instance_hash.to_numpy(),
                            "delta": delta}).groupby("instance_hash", sort=True).agg(
        error_sum=("delta", "sum"), rows=("delta", "size"))
    values = grouped[["error_sum", "rows"]].to_numpy(float)
    if not len(values):
        raise ValueError("Cannot bootstrap an empty instance group")
    rng = np.random.default_rng(seed)
    draws = np.empty(RESAMPLES, dtype=float)
    for start in range(0, RESAMPLES, 128):
        stop = min(start + 128, RESAMPLES)
        indices = rng.integers(0, len(values), size=(stop - start, len(values)))
        sampled = values[indices]
        draws[start:stop] = sampled[:, :, 0].sum(axis=1) / sampled[:, :, 1].sum(axis=1)
    lo, hi = np.quantile(draws, [0.025, 0.975])
    return {"rows": int(len(frame)), "instances": int(len(values)),
            "new_minus_comparator_brier": float(delta.mean()),
            "instance_bootstrap_percentile_95": [float(lo), float(hi)],
            "resamples": RESAMPLES, "seed": seed}


def label_observation_bounds(dry: dict, cohort: str) -> dict:
    by_state = dry["diagnostics"]["splits"]["test"]["cohorts"][cohort]["by_current_high"]
    current_normal = next(row for row in by_state if row["current_high"] == 0)
    candidates = int(current_normal["candidates"])
    labeled = int(current_normal["labeled"])
    positive = int(dry["counts"][cohort]["current_normal"]["test"]["positive"])
    if not (0 <= positive <= labeled <= candidates):
        raise ValueError("Inconsistent candidate and observed-label counts")
    return {"candidates": candidates, "labeled": labeled, "observed_positive": positive,
            "label_observation_rate": labeled / candidates,
            "observed_positive_rate": positive / labeled,
            "all_unobserved_negative_lower": positive / candidates,
            "all_unobserved_positive_upper": (positive + candidates - labeled) / candidates}


def audit() -> dict:
    dry_path = OUTPUT / "dry_run.json"
    metrics_path = OUTPUT / "metrics.json"
    prediction_path = OUTPUT / "test_predictions.parquet"
    cohort_path = OUTPUT / "cohort_rows.parquet"
    dry = json.loads(dry_path.read_text())
    metrics = json.loads(metrics_path.read_text())
    if not metrics.get("scoring_performed") or dry.get("scoring_performed"):
        raise ValueError("Expected scored follow-up and unscored dry run")
    if metrics["dry_run_sha256"] != sha256_file(dry_path):
        raise ValueError("Dry-run hash differs from scored artifact")
    if metrics["test_predictions_sha256"] != sha256_file(prediction_path):
        raise ValueError("Test predictions differ from scored artifact")
    if metrics["cohort_rows_sha256"] != sha256_file(cohort_path):
        raise ValueError("Cohort rows differ from scored artifact")
    predictions = pd.read_parquet(prediction_path)
    if predictions.duplicated(["instance_hash", "decision_s"]).any():
        raise ValueError("Duplicate test decision in saved predictions")
    result = {"status": "post_score_descriptive_sensitivity",
              "not_part_of_frozen_success_criterion": True,
              "script_sha256": sha256_file(Path(__file__)),
              "dry_run_sha256": sha256_file(dry_path),
              "metrics_sha256": sha256_file(metrics_path),
              "test_predictions_sha256": sha256_file(prediction_path),
              "bootstrap_unit": "instance_hash; all its eligible test rows sampled together",
              "bootstrap_limitation": "shared cluster/day shocks and the reused week remain untested",
              "current_normal": {}}
    selected = metrics["selected_original_baseline"]
    cohort_rows = pd.read_parquet(cohort_path,
                                  columns=["cohort", "split", "current_high", "any_recent_high", "y"])
    normal_complete_train = cohort_rows.loc[
        cohort_rows.cohort.eq("complete") & cohort_rows.split.eq("train") &
        cohort_rows.current_high.eq(0)]
    normal_recent_beta = {}
    for recent in (0, 1):
        block = normal_complete_train.loc[normal_complete_train.any_recent_high.eq(recent)]
        if block.empty:
            raise ValueError("Missing complete/current-normal train group for simple challenger")
        normal_recent_beta[str(recent)] = {"train_rows": int(len(block)),
                                            "train_positive": int(block.y.sum()),
                                            "beta_1_1_rate": float((block.y.sum() + 1) / (len(block) + 2))}
    result["posthoc_complete_normal_recent_beta"] = {
        "status": "proposed after reading test scores; never used in frozen selection or success",
        "train_only_rates": normal_recent_beta}
    for cohort in ("complete", "partial_past"):
        group = predictions.loc[predictions.cohort.eq(cohort) & predictions.current_high.eq(0)].copy()
        if cohort == "complete":
            group["pred_posthoc_normal_recent_beta"] = np.where(
                group.any_recent_high.eq(1), normal_recent_beta["1"]["beta_1_1_rate"],
                normal_recent_beta["0"]["beta_1_1_rate"])
            comparators = [f"pred_old_{selected}", "pred_posthoc_normal_recent_beta"]
        else:
            comparators = ["pred_partial_beta", "pred_posthoc_state_age_beta"]
        one = {"label_observation_bounds": label_observation_bounds(dry, cohort),
               "paired_brier_by_comparator": {}}
        for index, comparator in enumerate(comparators):
            if group[comparator].isna().any() or group.pred_new_state_full.isna().any():
                raise ValueError(f"Missing prediction for {cohort}/{comparator}")
            overall = instance_bootstrap(group, comparator, seed=SEED + index)
            if comparator != "pred_posthoc_normal_recent_beta":
                group_metric = metrics["test"][cohort]["current_normal"]["overall"]["methods"]
                stored_delta = (group_metric["new_state_full"]["brier"] -
                                group_metric[comparator.removeprefix("pred_")]["brier"])
                if not np.isclose(overall["new_minus_comparator_brier"], stored_delta, atol=1e-12):
                    raise ValueError(f"Stored Brier differs from saved predictions for {cohort}/{comparator}")
            by_day = {str(int(day)): instance_bootstrap(day_rows, comparator,
                                                       seed=SEED + int(day) + index)
                      for day, day_rows in group.groupby("day", sort=True)}
            one["paired_brier_by_comparator"][comparator] = {"overall": overall,
                                                                "by_day": by_day}
        result["current_normal"][cohort] = one
    path = OUTPUT / "posthoc_robustness.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    report = audit()
    print(json.dumps({"output": str(OUTPUT / "posthoc_robustness.json"),
                      "current_normal": {cohort: item["label_observation_bounds"]
                                         for cohort, item in report["current_normal"].items()}},
                     indent=2, ensure_ascii=False))
