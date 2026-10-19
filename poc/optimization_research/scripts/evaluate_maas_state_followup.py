"""Post-baseline, same-week MaaS state/missingness exploration.

Run --dry-run before --score. The dry run establishes complete and partial-past
cohorts and every cohort/stratum/split gate before any new model score exists.
This script never changes the original MaaS experiment or its saved results.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import evaluate_maas_instance_risk as original


ROOT = Path(__file__).resolve().parents[1]
SOURCE = original.SOURCE
SOURCE_AUDIT = original.AUDIT
OLD_METRICS = ROOT / "artifacts/maas_instance_risk_v1/metrics.json"
OLD_PREDICTIONS = ROOT / "artifacts/maas_instance_risk_v1/test_predictions.parquet"
HYPOTHESIS = ROOT / "reports/maas_risk_prior_art_and_next_hypothesis.md"
IMPLEMENTATION_NOTE = ROOT / "reports/maas_state_followup_implementation_note.md"
OUTPUT = ROOT / "artifacts/maas_state_followup_v1"
EXPECTED_HYPOTHESIS_SHA = "a8ffbebf94ad9a9ea35119eab7e24195783415db49069f7cb73b74d7adba3cef"
EXPECTED_NOTE_SHA = "2d24feb2ec6347a07bc477f51fffea271245862e2afcb3858e776da08c89ecc1"
EXPECTED_ORIGINAL_PROTOCOL_SHA = "5718e07cb57111d687e3ab5c03bf41d8c5137ad1a580f3abc31bcb1f8c65b500"
COHORTS = ("complete", "partial_past")
STRATA = ("all", "current_normal")
SPLITS = tuple(original.SPLITS)
PAST_RAW = ("sm_oldest", "sm_middle", "sm_current", "mem_oldest", "mem_middle",
            "mem_current", "samples_oldest", "samples_middle", "samples_current")
IMPUTED_COLUMNS = ("sm_oldest", "sm_middle", "mem_oldest", "mem_middle",
                   "samples_oldest", "samples_middle")
BASE_FEATURES = tuple(original.FEATURES) + ("current_high", "any_recent_high")
FULL_FEATURES = BASE_FEATURES + ("valid_oldest", "valid_middle", "past_age_s")
CS = original.CS


def frozen_sources() -> dict:
    if original.sha256_file(HYPOTHESIS) != EXPECTED_HYPOTHESIS_SHA:
        raise ValueError("Follow-up hypothesis note changed")
    if original.sha256_file(IMPLEMENTATION_NOTE) != EXPECTED_NOTE_SHA:
        raise ValueError("Pre-score implementation note changed")
    if original.sha256_file(original.PROTOCOL) != EXPECTED_ORIGINAL_PROTOCOL_SHA:
        raise ValueError("Original MaaS protocol changed")
    source = original.verify_source(SOURCE, SOURCE_AUDIT)
    old = json.loads(OLD_METRICS.read_text())
    if not old.get("scoring_performed") or old.get("protocol_sha256") != EXPECTED_ORIGINAL_PROTOCOL_SHA:
        raise ValueError("Original MaaS metrics do not match the reviewed experiment")
    expected_old_predictions = old.get("test_predictions_sha256")
    if original.sha256_file(OLD_PREDICTIONS) != expected_old_predictions:
        raise ValueError("Original complete-cohort test predictions changed")
    return {"source": source, "hypothesis_sha256": EXPECTED_HYPOTHESIS_SHA,
            "implementation_note_sha256": EXPECTED_NOTE_SHA,
            "original_protocol_sha256": EXPECTED_ORIGINAL_PROTOCOL_SHA,
            "original_metrics_sha256": original.sha256_file(OLD_METRICS),
            "original_predictions_sha256": expected_old_predictions}


def valid_meter(frame: pd.DataFrame) -> np.ndarray:
    sm = frame.sm_util.to_numpy(float)
    mem = frame.gpu_mem_util.to_numpy(float)
    samples = frame.samples.to_numpy(float)
    gpu = frame.gpu_count.to_numpy(float)
    return (np.isfinite(sm) & np.isfinite(mem) & np.isfinite(samples) & np.isfinite(gpu)
            & (sm >= 0) & (sm <= 100) & (mem >= 0) & (mem <= 100)
            & (samples > 0) & (gpu > 0))


def _profile(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    if frame.empty:
        return []
    agg = frame.groupby(columns, dropna=False, sort=True).agg(
        candidates=("label_observed", "size"), labeled=("label_observed", "sum"),
        old_missing=("old_missing", "sum"), middle_missing=("middle_missing", "sum"),
        old_duplicate=("old_duplicate", "sum"), middle_duplicate=("middle_duplicate", "sum"),
        old_invalid=("old_invalid", "sum"), middle_invalid=("middle_invalid", "sum"),
        future_missing=("future_missing", "sum"), future_duplicate=("future_duplicate", "sum"),
        future_invalid=("future_invalid", "sum"),
    ).reset_index()
    agg["label_observation_rate"] = agg.labeled / agg.candidates
    return json.loads(agg.to_json(orient="records"))


def _candidate_audit(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"candidates": 0, "labeled": 0, "label_observation_rate": None,
                "by_day": [], "by_cluster": [], "by_gpu_count": [],
                "by_current_high": [], "reasons_nonexclusive": {}}
    reasons = ("old_missing", "middle_missing", "old_duplicate", "middle_duplicate",
               "old_invalid", "middle_invalid", "future_missing", "future_duplicate", "future_invalid")
    return {"candidates": int(len(frame)), "labeled": int(frame.label_observed.sum()),
            "label_observation_rate": float(frame.label_observed.mean()),
            "reasons_nonexclusive": {x: int(frame[x].sum()) for x in reasons},
            "by_day": _profile(frame, ["day"]), "by_cluster": _profile(frame, ["cluster_id"]),
            "by_gpu_count": _profile(frame, ["gpu_count"]),
            "by_current_high": _profile(frame, ["current_high"])}


def construct_cohorts(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Exact time joins; label availability is audited after causal cohort choice."""
    if raw.empty:
        raise ValueError("Empty MaaS compact trace")
    usable = raw.time_s.notna() & np.isfinite(raw.time_s.to_numpy(dtype=float, na_value=np.nan))
    frame = raw.loc[usable].sort_values(["instance_hash", "time_s"], kind="mergesort").reset_index(drop=True)
    n = len(frame)
    if n == 0:
        raise ValueError("No MaaS rows have parsed time")
    times = frame.time_s.to_numpy(np.int64)
    codes, _ = pd.factorize(frame.instance_hash, sort=False)
    keys = np.empty(n, dtype=[("instance", "<i8"), ("time", "<i8")])
    keys["instance"] = codes
    keys["time"] = times
    if np.any(codes[1:] < codes[:-1]) or np.any((codes[1:] == codes[:-1]) & (times[1:] < times[:-1])):
        raise AssertionError("MaaS instance/time sorting failed")
    duplicated = keys[1:] == keys[:-1]
    unique = np.ones(n, dtype=bool)
    unique[1:] &= ~duplicated
    unique[:-1] &= ~duplicated
    valid = valid_meter(frame)
    sm = frame.sm_util.to_numpy(float)
    mem = frame.gpu_mem_util.to_numpy(float)
    samples = frame.samples.to_numpy(float)
    gpu = frame.gpu_count.to_numpy(float)
    cluster = frame.cluster_id.astype("string").to_numpy()
    cluster_for_change = frame.cluster_id.astype("string").fillna("<missing>").to_numpy(dtype=str)
    instance_hash = frame.instance_hash.to_numpy()
    tie_hash = frame.alarm_tie_hash.to_numpy()
    positions, exists, unambiguous, measured = [], [], [], []
    for offset in original.PAST_OFFSETS + original.FUTURE_OFFSETS:
        pos, found = original._exact_lookup(keys, codes, times, offset)
        positions.append(pos)
        exists.append(found)
        unambiguous.append(found & unique[pos])
        measured.append(found & unique[pos] & valid[pos])
    positions = np.column_stack(positions)
    exists = np.column_stack(exists)
    unambiguous = np.column_stack(unambiguous)
    measured = np.column_stack(measured)
    if not np.array_equal(measured[:, 2], unique & valid):
        raise AssertionError("Current-window lookup differs from raw-row uniqueness")
    current_ok = measured[:, 2]
    older_complete = measured[:, :2].all(axis=1)
    future_ok = measured[:, 3:].all(axis=1)
    # The cohort assignment depends on current/past only. Future affects label observability.
    complete_candidate = current_ok & older_complete
    partial_candidate = current_ok & ~older_complete
    decision = times + original.DECISION_DELAY_S
    rows = []
    diagnostics: dict = {"raw_rows": int(len(raw)), "time_parsed_rows": n,
                         "duplicate_current_raw_rows": int((~unique).sum()),
                         "invalid_meter_raw_rows": int((~valid).sum()), "splits": {}}
    for split, (start, end) in original.SPLITS.items():
        in_split = (decision >= start) & (
            decision + original.HORIZON_S + original.LABEL_DELIVERY_DELAY_S <= end)
        split_candidates = np.flatnonzero(in_split)
        # Present but invalid/duplicate current rows are counted by decision key.
        current_bad = split_candidates[~current_ok[split_candidates]]
        bad_keys = set(zip(instance_hash[current_bad].tolist(), times[current_bad].tolist()))
        split_audit = {"current_insufficient_evidence_keys": len(bad_keys),
                       "candidate_raw_rows": int(len(split_candidates)), "cohorts": {}}
        for cohort, candidate_flag in (("complete", complete_candidate),
                                       ("partial_past", partial_candidate)):
            candidate_idx = split_candidates[candidate_flag[split_candidates]]
            labeled_idx = candidate_idx[future_ok[candidate_idx]]
            profile = pd.DataFrame({
                "day": (decision[candidate_idx] // 86400).astype(np.int64),
                "cluster_id": cluster[candidate_idx], "gpu_count": gpu[candidate_idx],
                "current_high": ((sm[candidate_idx] >= 80) | (mem[candidate_idx] >= 90)).astype(np.int8),
                "label_observed": future_ok[candidate_idx],
                "old_missing": ~exists[candidate_idx, 0],
                "middle_missing": ~exists[candidate_idx, 1],
                "old_duplicate": exists[candidate_idx, 0] & ~unambiguous[candidate_idx, 0],
                "middle_duplicate": exists[candidate_idx, 1] & ~unambiguous[candidate_idx, 1],
                "old_invalid": unambiguous[candidate_idx, 0] & ~measured[candidate_idx, 0],
                "middle_invalid": unambiguous[candidate_idx, 1] & ~measured[candidate_idx, 1],
                "future_missing": ~exists[candidate_idx, 3:].all(axis=1),
                "future_duplicate": (exists[candidate_idx, 3:] &
                                     ~unambiguous[candidate_idx, 3:]).any(axis=1),
                "future_invalid": (unambiguous[candidate_idx, 3:] &
                                   ~measured[candidate_idx, 3:]).any(axis=1),
            })
            split_audit["cohorts"][cohort] = _candidate_audit(profile)
            if not len(labeled_idx):
                continue
            p = positions[labeled_idx, :3]
            f = positions[labeled_idx, 3:]
            sm_past = sm[p].copy()
            mem_past = mem[p].copy()
            sample_past = samples[p].copy()
            old_valid = measured[labeled_idx, 0]
            middle_valid = measured[labeled_idx, 1]
            sm_past[:, 0] = np.where(old_valid, sm_past[:, 0], np.nan)
            sm_past[:, 1] = np.where(middle_valid, sm_past[:, 1], np.nan)
            mem_past[:, 0] = np.where(old_valid, mem_past[:, 0], np.nan)
            mem_past[:, 1] = np.where(middle_valid, mem_past[:, 1], np.nan)
            sample_past[:, 0] = np.where(old_valid, sample_past[:, 0], np.nan)
            sample_past[:, 1] = np.where(middle_valid, sample_past[:, 1], np.nan)
            y = ((sm[f] >= 80) | (mem[f] >= 90)).any(axis=1).astype(np.int8)
            future_cluster_changed = (cluster_for_change[f] !=
                                      cluster_for_change[labeled_idx, None]).any(axis=1)
            future_gpu_changed = (gpu[f] != gpu[labeled_idx, None]).any(axis=1)
            split_audit["cohorts"][cohort]["future_cluster_changed_labeled"] = int(future_cluster_changed.sum())
            split_audit["cohorts"][cohort]["future_gpu_count_changed_labeled"] = int(future_gpu_changed.sum())
            high_past = ((sm_past >= 80) | (mem_past >= 90))  # NaN comparisons stay false.
            current_high = high_past[:, 2].astype(np.int8)
            one = pd.DataFrame({
                "cohort": cohort, "split": split, "instance_hash": instance_hash[labeled_idx],
                "time_s": times[labeled_idx], "decision_s": decision[labeled_idx],
                "day": (decision[labeled_idx] // 86400).astype(np.int64),
                "cluster_id": cluster[labeled_idx], "gpu_count": gpu[labeled_idx],
                "alarm_tie_hash": tie_hash[labeled_idx], "y": y,
                "future_cluster_changed": future_cluster_changed,
                "future_gpu_count_changed": future_gpu_changed,
                "current_high": current_high,
                "any_recent_high": high_past.any(axis=1).astype(np.int8),
                "valid_oldest": old_valid.astype(np.int8),
                "valid_middle": middle_valid.astype(np.int8),
                "past_age_s": np.where(middle_valid, 300, np.where(old_valid, 600, 900)).astype(np.int16),
                "sm_oldest": sm_past[:, 0], "sm_middle": sm_past[:, 1],
                "sm_current": sm_past[:, 2], "mem_oldest": mem_past[:, 0],
                "mem_middle": mem_past[:, 1], "mem_current": mem_past[:, 2],
                "samples_oldest": sample_past[:, 0], "samples_middle": sample_past[:, 1],
                "samples_current": sample_past[:, 2],
            })
            rows.append(one)
        diagnostics["splits"][split] = split_audit
    examples = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if examples.empty:
        raise ValueError("No labeled complete or partial-past MaaS examples")
    if examples.duplicated(["instance_hash", "time_s"]).any():
        raise ValueError("Same MaaS decision appears twice across cohorts")
    return examples, diagnostics


def cohort_counts(examples: pd.DataFrame) -> dict:
    report = {}
    for cohort in COHORTS:
        report[cohort] = {}
        for stratum in STRATA:
            report[cohort][stratum] = {}
            for split in SPLITS:
                keep = examples.cohort.eq(cohort) & examples.split.eq(split)
                if stratum == "current_normal":
                    keep &= examples.current_high.eq(0)
                group = examples.loc[keep]
                pos = group.loc[group.y.eq(1)]
                n = len(group)
                positive = len(pos)
                record = {"eligible": n, "positive": positive, "negative": n - positive,
                          "positive_instances": int(pos.instance_hash.nunique())}
                record["passes"] = (record["eligible"] >= 2000 and record["positive"] >= 100
                                    and record["negative"] >= 100 and record["positive_instances"] >= 30)
                report[cohort][stratum][split] = record
    return report


def state_model_class_gate(examples: pd.DataFrame) -> dict:
    """Audit both class labels for every train/validation state model."""
    by_state = {}
    for state, label in ((0, "normal"), (1, "high")):
        by_state[label] = {}
        for split in ("train", "validation"):
            group = examples.loc[examples.split.eq(split) & examples.current_high.eq(state)]
            positive = int(group.y.sum())
            negative = int(len(group) - positive)
            by_state[label][split] = {"eligible": len(group), "positive": positive,
                                      "negative": negative,
                                      "both_classes": positive > 0 and negative > 0}
    return {"by_state": by_state, "passes": all(
        item["both_classes"] for state in by_state.values() for item in state.values())}


def compare_original_complete(examples: pd.DataFrame) -> dict:
    old = pd.read_parquet(OLD_PREDICTIONS, columns=["instance_hash", "decision_s", "y", "current_threshold"])
    new = examples.loc[examples.cohort.eq("complete") & examples.split.eq("test"),
                       ["instance_hash", "decision_s", "y", "current_high"]]
    if old.duplicated(["instance_hash", "decision_s"]).any() or new.duplicated(["instance_hash", "decision_s"]).any():
        raise ValueError("Duplicate paired complete-test MaaS decision")
    if len(old) != len(new):
        raise ValueError("Complete-test row count differs from original fixed experiment")
    pair = new.merge(old, on=["instance_hash", "decision_s"], how="outer", indicator=True,
                     suffixes=("_new", "_old"), validate="one_to_one")
    if (not pair["_merge"].eq("both").all() or
            not pair.y_new.eq(pair.y_old).all() or
            not pair.current_high.eq(pair.current_threshold).all()):
        raise ValueError("Complete-test rows, labels, or current state differ from original")
    return {"paired_complete_test_rows": len(pair), "same_y": True, "same_current_state": True}


def dry_run() -> dict:
    source_hashes = frozen_sources()
    raw = original.load_compact(SOURCE)
    examples, diagnostics = construct_cohorts(raw)
    counts = cohort_counts(examples)
    state_class_gate = state_model_class_gate(examples)
    paired = compare_original_complete(examples)
    original_metrics = json.loads(OLD_METRICS.read_text())
    for split in SPLITS:
        old_count = original_metrics["data_audit"]["splits"][split]["eligible"]
        if counts["complete"]["all"][split]["eligible"] != old_count:
            raise ValueError(f"Complete {split} count differs from original MaaS audit")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    example_path = OUTPUT / "cohort_rows.parquet"
    examples.to_parquet(example_path, index=False)
    gates = {cohort: {stratum: all(counts[cohort][stratum][split]["passes"] for split in SPLITS)
                      for stratum in STRATA} for cohort in COHORTS}
    result = {"status": "dry_run", "scoring_performed": False,
              "source_hashes": source_hashes, "script_sha256": original.sha256_file(Path(__file__)),
              "cohort_rows_sha256": original.sha256_file(example_path), "cohort_rows": len(examples),
              "diagnostics": diagnostics, "counts": counts, "gates": gates,
              "state_model_class_gate": state_class_gate,
              "paired_original_complete_test": paired,
              "gpu_mem_over_100_semantics": "unresolved; values are excluded as in original, so this is exploratory",
              "timing": {"decision_offset_s": 600, "label_available_offset_from_decision_s": 2100,
                         "past_offsets_s": original.PAST_OFFSETS,
                         "future_offsets_s": original.FUTURE_OFFSETS}}
    (OUTPUT / "dry_run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return result


def train_imputation(train: pd.DataFrame) -> dict[str, float]:
    """Fit six old/middle-window medians on the expanded train split only."""
    medians = {column: float(train[column].median(skipna=True)) for column in IMPUTED_COLUMNS}
    if not all(np.isfinite(value) for value in medians.values()):
        raise ValueError("Expanded train split lacks an observed imputation column")
    return medians


def make_features(rows: pd.DataFrame, medians: dict[str, float]) -> pd.DataFrame:
    result = rows.copy()
    for column in IMPUTED_COLUMNS:
        result[column] = result[column].fillna(medians[column])
    sm = result[["sm_oldest", "sm_middle", "sm_current"]].to_numpy(float)
    mem = result[["mem_oldest", "mem_middle", "mem_current"]].to_numpy(float)
    result["sm_mean3"] = sm.mean(axis=1)
    result["sm_max3"] = sm.max(axis=1)
    result["sm_change3"] = sm[:, 2] - sm[:, 0]
    result["mem_mean3"] = mem.mean(axis=1)
    result["mem_max3"] = mem.max(axis=1)
    result["mem_change3"] = mem[:, 2] - mem[:, 0]
    if not np.isfinite(result[list(FULL_FEATURES)].to_numpy(float)).all():
        raise ValueError("Nonfinite fitted feature after train-only imputation")
    return result


def select_logistic(train: pd.DataFrame, validation: pd.DataFrame,
                    features: tuple[str, ...]) -> tuple[object, dict]:
    if train.y.nunique() != 2 or validation.y.nunique() != 2:
        raise ValueError("Logistic state split needs both observed classes")
    candidates = []
    for c in CS:
        model = make_pipeline(StandardScaler(), LogisticRegression(
            C=c, penalty="l2", solver="lbfgs", max_iter=1000, random_state=42,
        ))
        model.fit(train[list(features)], train.y)
        probability = model.predict_proba(validation[list(features)])[:, 1]
        candidates.append((float(brier_score_loss(validation.y, probability)), c, model))
    best = min(candidates, key=lambda item: (item[0], item[1]))
    return best[2], {"validation_brier_by_C": {str(c): score for score, c, _ in candidates},
                     "selected_C": best[1], "train_rows": len(train),
                     "validation_rows": len(validation), "features": list(features)}


def score_set(frame: pd.DataFrame, names: tuple[str, ...]) -> dict:
    overall = original.score_group(frame, names)
    by_day = {str(day): original.score_group(day_rows, names)
              for day, day_rows in frame.groupby("day", sort=True)}
    return {"overall": overall, "by_day": by_day}


def instance_diagnostics(frame: pd.DataFrame, names: tuple[str, ...]) -> dict:
    if frame.empty:
        return {}
    per_instance = frame.groupby("instance_hash", sort=False).size()
    result = {"instances": len(per_instance), "rows_per_instance": {
        "min": int(per_instance.min()), "p25": float(per_instance.quantile(.25)),
        "median": float(per_instance.median()), "p75": float(per_instance.quantile(.75)),
        "max": int(per_instance.max())}, "mean_instance_brier": {}}
    for name in names:
        error = (frame.y.to_numpy(float) - frame[f"pred_{name}"].to_numpy(float)) ** 2
        per_error = pd.Series(error, index=frame.index).groupby(frame.instance_hash).mean()
        result["mean_instance_brier"][name] = float(per_error.mean())
    return result


def success_checks(scored: dict, selected_old_baseline: str) -> dict:
    if not scored:
        return {"assessable": False, "reason": "complete/current_normal gate did not pass"}
    methods = scored["overall"]["methods"]
    new = methods["new_state_full"]
    old_names = ("old_logistic", f"old_{selected_old_baseline}")
    days = scored["by_day"]
    day_positives = {day: entry["positive"] for day, entry in days.items()}
    # score_set stores direct score_group results in by_day.
    checks = {}
    for comparator in old_names:
        old = methods[comparator]
        checks[comparator] = {
            "overall_relative_brier_gain_at_least_5pct":
                bool(old["brier"] > 0 and new["brier"] <= .95 * old["brier"]),
            "both_test_days_brier_lower": bool(len(days) == 2 and all(
                entry["methods"]["new_state_full"]["brier"] < entry["methods"][comparator]["brier"]
                for entry in days.values())),
            "overall_average_precision_not_lower":
                bool(new["average_precision"] >= old["average_precision"]),
            "both_test_days_top5_recall_not_lower": bool(len(days) == 2 and all(
                entry["methods"]["new_state_full"]["top5pct_alarm"]["recall"] >=
                entry["methods"][comparator]["top5pct_alarm"]["recall"]
                for entry in days.values())),
        }
    sufficient_day_positives = len(day_positives) == 2 and all(n >= 100 for n in day_positives.values())
    return {"assessable": sufficient_day_positives,
            "day_positives": day_positives,
            "checks_against_each_original": checks,
            "meets_frozen_criterion": (sufficient_day_positives and all(
                all(value for value in group.values()) for group in checks.values()))}


def score() -> dict:
    """Verify the frozen dry-run and all gates before fitting any new model."""
    dry_path = OUTPUT / "dry_run.json"
    dry = json.loads(dry_path.read_text())
    if dry["scoring_performed"] or dry["script_sha256"] != original.sha256_file(Path(__file__)):
        raise ValueError("Dry-run is absent, stale, or was modified after code freeze")
    if dry["source_hashes"] != frozen_sources():
        raise ValueError("Pinned MaaS data/protocol changed after dry-run")
    row_path = OUTPUT / "cohort_rows.parquet"
    if dry["cohort_rows_sha256"] != original.sha256_file(row_path):
        raise ValueError("Cohort rows changed after dry-run")
    rows = pd.read_parquet(row_path)
    counts = cohort_counts(rows)
    if (counts != dry["counts"] or
            state_model_class_gate(rows) != dry["state_model_class_gate"] or
            compare_original_complete(rows) != dry["paired_original_complete_test"]):
        raise ValueError("Cohort gates or original paired rows changed after dry-run")
    gates = dry["gates"]
    active = [(cohort, stratum) for cohort in COHORTS for stratum in STRATA
              if gates[cohort][stratum]]
    if not active:
        result = {"scoring_performed": False, "reason": "No cohort/stratum passed the frozen gates",
                  "gates": gates, "dry_run_sha256": original.sha256_file(dry_path)}
        (OUTPUT / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    train = rows.loc[rows.split.eq("train")]
    val = rows.loc[rows.split.eq("validation")]
    state_gate = dry["state_model_class_gate"]["passes"]
    medians = train_imputation(train)
    featured = make_features(rows, medians)
    ftrain = featured.loc[featured.split.eq("train")]
    fval = featured.loc[featured.split.eq("validation")]
    ftest = featured.loc[featured.split.eq("test")].copy()
    models, selection = {}, {}
    models["new_pooled"], selection["new_pooled"] = select_logistic(ftrain, fval, FULL_FEATURES)
    ftest["pred_new_pooled"] = models["new_pooled"].predict_proba(ftest[list(FULL_FEATURES)])[:, 1]
    if state_gate:
        for state, label in ((0, "normal"), (1, "high")):
            tr = ftrain.loc[ftrain.current_high.eq(state)]
            va = fval.loc[fval.current_high.eq(state)]
            te = ftest.current_high.eq(state)
            for ablation, features in (("new_state_full", FULL_FEATURES),
                                       ("new_state_nomask", BASE_FEATURES)):
                key = f"{ablation}_{label}"
                models[key], selection[key] = select_logistic(tr, va, features)
                ftest.loc[te, f"pred_{ablation}"] = models[key].predict_proba(
                    ftest.loc[te, list(features)])[:, 1]
    partial_train = train.loc[train.cohort.eq("partial_past")]
    py = partial_train.y.to_numpy(np.int8)
    partial_beta = {str(state): float((py[partial_train.current_high.to_numpy() == state].sum() + 1) /
                                      ((partial_train.current_high == state).sum() + 2))
                    for state in (0, 1)}
    ftest["pred_partial_beta"] = np.where(ftest.current_high.eq(1),
                                           partial_beta["1"], partial_beta["0"])
    # Post-hoc diagnostic proposed after inspecting dry-run age strata. It is
    # excluded from the frozen success criterion and never uses test labels.
    posthoc_age_beta = {}
    for state in (0, 1):
        for age in (300, 600, 900):
            group = partial_train.loc[partial_train.current_high.eq(state) & partial_train.past_age_s.eq(age)]
            posthoc_age_beta[f"{state}:{age}"] = float((group.y.sum() + 1) / (len(group) + 2))
    ftest["pred_posthoc_state_age_beta"] = np.array([
        posthoc_age_beta[f"{int(state)}:{int(age)}"]
        for state, age in zip(ftest.current_high, ftest.past_age_s)
    ])
    ftest.loc[ftest.cohort.eq("complete"), "pred_posthoc_state_age_beta"] = np.nan
    old_metrics = json.loads(OLD_METRICS.read_text())
    old_names = ("prevalence", "current_threshold", "any_recent_threshold", "logistic")
    old = pd.read_parquet(OLD_PREDICTIONS,
                          columns=["instance_hash", "decision_s", *[f"pred_{name}" for name in old_names]])
    old = old.rename(columns={f"pred_{name}": f"pred_old_{name}" for name in old_names})
    ftest = ftest.merge(old, on=["instance_hash", "decision_s"], how="left", validate="one_to_one")
    complete_mask = ftest.cohort.eq("complete")
    if ftest.loc[complete_mask, [f"pred_old_{name}" for name in old_names]].isna().any().any():
        raise ValueError("Missing original predictions for paired complete test rows")
    result = {"scoring_performed": True, "same_week_post_baseline_exploration": True,
              "dry_run_sha256": original.sha256_file(dry_path),
              "script_sha256": original.sha256_file(Path(__file__)),
              "source_hashes": dry["source_hashes"], "cohort_rows_sha256": dry["cohort_rows_sha256"],
              "counts": counts, "gates": gates, "state_model_train_validation_gate": state_gate,
              "state_model_class_gate": dry["state_model_class_gate"],
              "train_only_imputation_medians": medians, "model_selection": selection,
              "partial_past_beta_state_rates": partial_beta,
              "posthoc_state_age_beta_rates": posthoc_age_beta,
              "posthoc_state_age_beta_status": "proposed after dry-run label counts; excluded from frozen criterion",
              "selected_original_baseline": old_metrics["selection"]["selected_baseline"],
              "sklearn_version": sklearn.__version__, "test": {}}
    for cohort, stratum in active:
        mask = ftest.cohort.eq(cohort)
        if stratum == "current_normal":
            mask &= ftest.current_high.eq(0)
        subset = ftest.loc[mask].copy()
        names = ["new_pooled"]
        if state_gate:
            names.extend(["new_state_full", "new_state_nomask"])
        if cohort == "complete":
            names.extend(f"old_{name}" for name in old_names)
        else:
            names.append("partial_beta")
            names.append("posthoc_state_age_beta")
        group = score_set(subset, tuple(names))
        group["instance_diagnostics"] = instance_diagnostics(subset, tuple(names))
        result["test"].setdefault(cohort, {})[stratum] = group
    primary = result["test"].get("complete", {}).get("current_normal")
    result["frozen_criterion"] = (success_checks(primary, result["selected_original_baseline"])
                                   if state_gate else {"assessable": False,
                                                       "reason": "State model class gate failed"})
    output_predictions = OUTPUT / "test_predictions.parquet"
    keep = ["cohort", "instance_hash", "decision_s", "day", "cluster_id", "gpu_count",
            "alarm_tie_hash", "y", "current_high", "any_recent_high", "valid_oldest",
            "valid_middle", "past_age_s", "future_cluster_changed", "future_gpu_count_changed",
            *[column for column in ftest.columns if column.startswith("pred_")]]
    ftest[keep].to_parquet(output_predictions, index=False)
    result["test_predictions_sha256"] = original.sha256_file(output_predictions)
    result["fitted_models"] = {}
    for key, model in models.items():
        model_path = OUTPUT / f"{key}.joblib"
        joblib.dump(model, model_path)
        result["fitted_models"][key] = {"sha256": original.sha256_file(model_path),
                                          "selected_C": selection[key]["selected_C"]}
    (OUTPUT / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                                       allow_nan=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--score", action="store_true")
    args = parser.parse_args()
    result = dry_run() if args.dry_run else score()
    print(json.dumps({"scoring_performed": result["scoring_performed"],
                      "gates": result.get("gates"),
                      "frozen_criterion": result.get("frozen_criterion"),
                      "output": str(OUTPUT)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
