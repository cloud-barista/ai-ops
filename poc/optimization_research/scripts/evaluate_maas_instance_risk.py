"""Pre-registered Alibaba MaaS instance saturation-risk experiment.

The outcome is an observed 5-minute-average threshold crossing, not a scale
action or its effect. No model is fitted and no test score is calculated until
the source-integrity and sample-eligibility gates have passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_sample.parquet"
AUDIT = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_audit.json"
OUTPUT = ROOT / "artifacts/maas_instance_risk_v1"
PROTOCOL = ROOT / "reports/maas_instance_risk_protocol.md"
PINNED_SOURCE_URL = (
    "https://tre-clusterdata.oss-cn-hangzhou.aliyuncs.com/"
    "cluster-trace-v2026-maas/data/instance_week_5min.zip"
)
PINNED_SOURCE_SIZE = 4_812_189_789
PINNED_SOURCE_ETAG = "434E78D81E2A9F9F1FDEF3163DD0FCEC"
PINNED_MEMBER_COUNT = 21
PINNED_ROW_COUNT = 140_906_827
WINDOW_S = 300
DECISION_DELAY_S = 600
HORIZON_S = 1800
LABEL_DELIVERY_DELAY_S = 300
PAST_OFFSETS = (-600, -300, 0)
FUTURE_OFFSETS = (600, 900, 1200, 1500, 1800, 2100)
SPLITS = {
    "train": (140 * 86400, 144 * 86400),
    "validation": (144 * 86400, 145 * 86400),
    "test": (145 * 86400, 147 * 86400),
}
REQUIRED_COLUMNS = (
    "instance_hash", "time_s", "cluster_id", "gpu_count", "sm_util",
    "gpu_mem_util", "samples", "alarm_tie_hash",
)
FEATURES = (
    "sm_oldest", "sm_middle", "sm_current", "mem_oldest", "mem_middle",
    "mem_current", "samples_oldest", "samples_middle", "samples_current",
    "gpu_count", "sm_mean3", "sm_max3", "sm_change3", "mem_mean3",
    "mem_max3", "mem_change3",
)
BASELINES = ("prevalence", "current_threshold", "any_recent_threshold")
METHODS = BASELINES + ("logistic",)
CS = (0.1, 1.0, 10.0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(source: Path, audit_path: Path) -> dict:
    """Demand a complete, successful 21-member audit before reading rows."""
    audit = json.loads(audit_path.read_text())
    pinned = {
        "source_url": PINNED_SOURCE_URL,
        "source_size": PINNED_SOURCE_SIZE,
        "source_etag": PINNED_SOURCE_ETAG,
        "member_count": PINNED_MEMBER_COUNT,
        "expected_member_count": PINNED_MEMBER_COUNT,
    }
    for field, expected in pinned.items():
        if audit.get(field) != expected:
            raise ValueError(f"Source audit gate failed: {field} differs from pinned source")
    for field in ("integrity_pass", "schema_pass", "all_members_verified"):
        if audit.get(field) is not True:
            raise ValueError(f"Source audit gate failed: {field} is not true")
    totals = audit.get("totals")
    if not isinstance(totals, dict) or totals.get("row_count") != PINNED_ROW_COUNT:
        raise ValueError("Source audit gate failed: totals.row_count differs from official count")
    for field in ("official_row_count_match", "time_aligned_5min", "official_days_only",
                  "schema_consistent_across_members"):
        if totals.get(field) is not True:
            raise ValueError(f"Source audit gate failed: totals.{field} is not true")
    expected_hash = audit.get("compact_sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("Source audit lacks compact_sha256")
    actual_hash = sha256_file(source)
    if actual_hash != expected_hash:
        raise ValueError("Compact Parquet SHA256 does not match source audit")
    return {"audit_sha256": sha256_file(audit_path), "compact_sha256": actual_hash}


def load_compact(source: Path) -> pd.DataFrame:
    frame = pd.read_parquet(source)
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing compact columns: {missing}")
    frame = frame[list(REQUIRED_COLUMNS)].copy()
    if frame.instance_hash.isna().any() or not frame.instance_hash.astype(str).str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("instance_hash must be a full lowercase SHA256 digest")
    time = pd.to_numeric(frame.time_s, errors="coerce")
    finite_time = time.notna()
    if finite_time.any() and not np.equal(np.mod(time.loc[finite_time].to_numpy(float), 1), 0).all():
        raise ValueError("Noninteger time_s in compact data")
    if finite_time.any() and not np.equal(np.mod(time.loc[finite_time].to_numpy(float), WINDOW_S), 0).all():
        raise ValueError("Unaligned time_s in compact data")
    has_tie = frame.alarm_tie_hash.notna()
    if not has_tie.equals(finite_time):
        raise ValueError("alarm_tie_hash must exist exactly when time_s exists")
    if has_tie.any() and not frame.loc[has_tie, "alarm_tie_hash"].astype(str).str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("alarm_tie_hash must be a full lowercase SHA256 digest")
    for column in ("gpu_count", "sm_util", "gpu_mem_util", "samples"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["time_s"] = time
    return frame


def _exact_lookup(keys: np.ndarray, code: np.ndarray, time: np.ndarray, offset: int) -> tuple[np.ndarray, np.ndarray]:
    query = np.empty(len(code), dtype=keys.dtype)
    query["instance"] = code
    query["time"] = time + offset
    position = np.searchsorted(keys, query, side="left")
    safe = np.minimum(position, len(keys) - 1)
    found = (position < len(keys)) & (keys[safe] == query)
    return safe, found


def _group_summary(frame: pd.DataFrame, group_columns: list[str]) -> list[dict]:
    if frame.empty:
        return []
    grouped = frame.groupby(group_columns, dropna=False, sort=True)
    result = grouped.agg(
        candidates=("eligible", "size"), eligible=("eligible", "sum"),
        missing_window=("missing_window", "sum"),
        missing_past=("missing_past", "sum"), missing_future=("missing_future", "sum"),
        duplicate_past=("duplicate_past", "sum"), duplicate_future=("duplicate_future", "sum"),
        invalid_past=("invalid_past", "sum"), invalid_future=("invalid_future", "sum"),
    ).reset_index()
    result["eligibility_fraction"] = result.eligible / result.candidates
    result["ineligible_fraction"] = 1 - result.eligibility_fraction
    result["missing_window_fraction"] = result.missing_window / result.candidates
    return json.loads(result.to_json(orient="records"))


def construct_examples(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Join exact instance/time windows, preserving duplicate and invalid rows.

    Search is by an ordered (instance code, time) key. Looking up exact times
    matters because the intentionally unused window [t+300,t+600) may be absent.
    """
    if raw.empty:
        return pd.DataFrame(), {"raw_rows": 0, "splits": {}}
    time = pd.to_numeric(raw.time_s, errors="coerce")
    usable_time = time.notna() & np.isfinite(time.to_numpy(dtype=float, na_value=np.nan))
    frame = raw.loc[usable_time].sort_values(["instance_hash", "time_s"], kind="mergesort").reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame(), {"raw_rows": int(len(raw)), "rows_without_time": int(len(raw)), "splits": {}}
    n = len(frame)
    times = frame.time_s.to_numpy(np.int64)
    codes, _ = pd.factorize(frame.instance_hash, sort=False)
    keys = np.empty(n, dtype=[("instance", "<i8"), ("time", "<i8")])
    keys["instance"] = codes
    keys["time"] = times
    if n > 1 and (np.any(codes[1:] < codes[:-1]) or
                  np.any((codes[1:] == codes[:-1]) & (times[1:] < times[:-1]))):
        raise AssertionError("Ordered instance/time key construction failed")
    adjacent = keys[1:] == keys[:-1]
    unique = np.ones(n, dtype=bool)
    unique[1:] &= ~adjacent
    unique[:-1] &= ~adjacent

    sm = frame.sm_util.to_numpy(float)
    mem = frame.gpu_mem_util.to_numpy(float)
    samples = frame.samples.to_numpy(float)
    gpu = frame.gpu_count.to_numpy(float)
    valid = (np.isfinite(sm) & np.isfinite(mem) & np.isfinite(samples) & np.isfinite(gpu)
             & (sm >= 0) & (sm <= 100) & (mem >= 0) & (mem <= 100)
             & (samples > 0) & (gpu > 0))
    offsets = PAST_OFFSETS + FUTURE_OFFSETS
    positions, exists, unambiguous, measured = [], [], [], []
    for offset in offsets:
        position, found = _exact_lookup(keys, codes, times, offset)
        positions.append(position)
        exists.append(found)
        unambiguous.append(found & unique[position])
        measured.append(found & unique[position] & valid[position])
    exists = np.column_stack(exists)
    unambiguous = np.column_stack(unambiguous)
    measured = np.column_stack(measured)
    eligible = measured.all(axis=1)
    decision = times + DECISION_DELAY_S
    reason = {
        "missing_past": ~exists[:, :3].all(axis=1),
        "missing_future": ~exists[:, 3:].all(axis=1),
        "duplicate_past": (exists[:, :3] & ~unambiguous[:, :3]).any(axis=1),
        "duplicate_future": (exists[:, 3:] & ~unambiguous[:, 3:]).any(axis=1),
        "invalid_past": (unambiguous[:, :3] & ~measured[:, :3]).any(axis=1),
        "invalid_future": (unambiguous[:, 3:] & ~measured[:, 3:]).any(axis=1),
    }
    cluster = frame.cluster_id.astype("string").to_numpy()
    cluster_for_change = frame.cluster_id.astype("string").fillna("<missing>").to_numpy(dtype=str)
    gpu_group = frame.gpu_count.to_numpy()
    examples = []
    split_stats = {}
    for split, (start, end) in SPLITS.items():
        in_split = (decision >= start) & (decision + HORIZON_S + LABEL_DELIVERY_DELAY_S <= end)
        candidate_idx = np.flatnonzero(in_split)
        selected_idx = candidate_idx[eligible[candidate_idx]]
        candidates = pd.DataFrame({
            "day": (decision[candidate_idx] // 86400).astype(np.int64),
            "cluster_id": cluster[candidate_idx],
            "gpu_count": gpu_group[candidate_idx],
            "eligible": eligible[candidate_idx],
            "missing_window": (reason["missing_past"][candidate_idx] | reason["missing_future"][candidate_idx]),
            **{name: values[candidate_idx] for name, values in reason.items()},
        })
        stats = {
            "candidates": int(len(candidate_idx)), "eligible": int(len(selected_idx)),
            "eligibility_fraction": float(len(selected_idx) / len(candidate_idx)) if len(candidate_idx) else None,
            "ineligible": int(len(candidate_idx) - len(selected_idx)),
            "missing_window": int(candidates.missing_window.sum()),
            "missing_window_fraction": float(candidates.missing_window.mean()) if len(candidate_idx) else None,
            "reason_counts_nonexclusive": {name: int(values[candidate_idx].sum()) for name, values in reason.items()},
            "by_day": _group_summary(candidates, ["day"]),
            "by_cluster": _group_summary(candidates, ["cluster_id"]),
            "by_gpu_count": _group_summary(candidates, ["gpu_count"]),
        }
        split_stats[split] = stats
        if not len(selected_idx):
            continue
        p = np.column_stack([position[selected_idx] for position in positions[:3]])
        f = np.column_stack([position[selected_idx] for position in positions[3:]])
        sm_past, mem_past, sample_past = sm[p], mem[p], samples[p]
        y = ((sm[f] >= 80) | (mem[f] >= 90)).any(axis=1).astype(np.int8)
        high_past = (sm_past >= 80) | (mem_past >= 90)
        current_high = high_past[:, 2]
        changed_cluster = (cluster_for_change[f] != cluster_for_change[selected_idx, None]).any(axis=1)
        changed_gpu = (gpu[f] != gpu[selected_idx, None]).any(axis=1)
        one = pd.DataFrame({
            "split": split, "instance_hash": frame.instance_hash.to_numpy()[selected_idx],
            "time_s": times[selected_idx], "decision_s": decision[selected_idx],
            "day": (decision[selected_idx] // 86400).astype(np.int64),
            "cluster_id": cluster[selected_idx], "alarm_tie_hash": frame.alarm_tie_hash.to_numpy()[selected_idx],
            "y": y, "current_threshold": current_high.astype(np.int8),
            "any_recent_threshold": high_past.any(axis=1).astype(np.int8),
            "future_cluster_changed": changed_cluster, "future_gpu_count_changed": changed_gpu,
            "gpu_count": gpu[selected_idx],
            "sm_oldest": sm_past[:, 0], "sm_middle": sm_past[:, 1], "sm_current": sm_past[:, 2],
            "mem_oldest": mem_past[:, 0], "mem_middle": mem_past[:, 1], "mem_current": mem_past[:, 2],
            "samples_oldest": sample_past[:, 0], "samples_middle": sample_past[:, 1],
            "samples_current": sample_past[:, 2],
            "sm_mean3": sm_past.mean(axis=1), "sm_max3": sm_past.max(axis=1),
            "sm_change3": sm_past[:, 2] - sm_past[:, 0],
            "mem_mean3": mem_past.mean(axis=1), "mem_max3": mem_past.max(axis=1),
            "mem_change3": mem_past[:, 2] - mem_past[:, 0],
        })
        examples.append(one)
        stats.update({
            "positive": int(y.sum()), "negative": int(len(y) - y.sum()),
            "positive_instances": int(one.loc[one.y.eq(1), "instance_hash"].nunique()),
            "future_cluster_changed": int(np.sum(changed_cluster)),
            "future_gpu_count_changed": int(np.sum(changed_gpu)),
            "future_cluster_changed_fraction": float(np.mean(changed_cluster)),
            "future_gpu_count_changed_fraction": float(np.mean(changed_gpu)),
        })
    labeled = pd.concat(examples, ignore_index=True) if examples else pd.DataFrame()
    diagnostics = {
        "raw_rows": int(len(raw)), "rows_without_time": int(len(raw) - n),
        "sorted_rows_with_time": int(n), "duplicate_instance_time_rows": int((~unique).sum()),
        "invalid_meter_rows": int((~valid).sum()), "splits": split_stats,
    }
    return labeled, diagnostics


def quality_gates(diagnostics: dict) -> dict:
    checks = {}
    for split in SPLITS:
        row = diagnostics.get("splits", {}).get(split, {})
        n = int(row.get("eligible", 0))
        positive = int(row.get("positive", 0))
        negative = int(row.get("negative", 0))
        positive_instances = int(row.get("positive_instances", 0))
        fraction = row.get("eligibility_fraction")
        checks[split] = {
            "eligibility_fraction_at_least_0_20": fraction is not None and fraction >= 0.20,
            "eligible_at_least_2000": n >= 2000,
            "positive_at_least_100": positive >= 100,
            "negative_at_least_100": negative >= 100,
            "positive_instances_at_least_30": positive_instances >= 30,
        }
    return {"by_split": checks, "passes": all(all(items.values()) for items in checks.values())}


def fit_baselines(train: pd.DataFrame) -> dict:
    y = train.y.to_numpy(np.int8)
    parameters = {"prevalence": float(y.mean())}
    for name in BASELINES[1:]:
        key = train[name].to_numpy(np.int8)
        parameters[name] = {
            str(group): float((y[key == group].sum() + 1) / ((key == group).sum() + 2))
            for group in (0, 1)
        }
    return parameters


def baseline_predict(frame: pd.DataFrame, parameters: dict, name: str) -> np.ndarray:
    if name == "prevalence":
        return np.full(len(frame), parameters[name], dtype=float)
    key = frame[name].to_numpy(np.int8)
    return np.where(key == 1, parameters[name]["1"], parameters[name]["0"])


def fit_and_select(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[dict, dict, object]:
    """Fit only on train and choose both comparators by validation Brier."""
    parameters = fit_baselines(train)
    vy = validation.y.to_numpy(np.int8)
    baseline_validation = {
        name: float(brier_score_loss(vy, baseline_predict(validation, parameters, name)))
        for name in BASELINES
    }
    selected_baseline = min(BASELINES, key=lambda name: (baseline_validation[name], BASELINES.index(name)))
    candidates = []
    for c in CS:
        model = make_pipeline(StandardScaler(), LogisticRegression(
            C=c, penalty="l2", solver="lbfgs", max_iter=1000, random_state=42,
        ))
        model.fit(train[list(FEATURES)], train.y)
        probability = model.predict_proba(validation[list(FEATURES)])[:, 1]
        candidates.append((float(brier_score_loss(vy, probability)), c, model))
    selected = min(candidates, key=lambda item: (item[0], item[1]))
    selection = {
        "baseline_validation_brier": baseline_validation,
        "selected_baseline": selected_baseline,
        "logistic_validation_brier_by_C": {str(c): score for score, c, _ in candidates},
        "selected_C": selected[1],
    }
    return parameters, selection, selected[2]


def _alarm_metrics(frame: pd.DataFrame, probability: np.ndarray) -> dict:
    """Take ceil(5% of each day), descending probability then source-ID tie hash."""
    if frame.empty:
        return {"alarms": 0, "precision": None, "recall": None, "alarms_per_instance": None}
    y = frame.y.to_numpy(np.int8)
    chosen = np.zeros(len(frame), dtype=bool)
    all_ties = frame.alarm_tie_hash.to_numpy(dtype="S64")
    for _, positions in frame.groupby("day", sort=True).indices.items():
        count = int(math.ceil(0.05 * len(positions)))
        ties = all_ties[positions]
        ordered = np.lexsort((ties, -probability[positions]))
        chosen[positions[ordered[:count]]] = True
    alarms = int(chosen.sum())
    true_positive = int(y[chosen].sum())
    return {
        "alarms": alarms, "true_positive_alarms": true_positive,
        "precision": float(true_positive / alarms) if alarms else None,
        "recall": float(true_positive / int(y.sum())) if y.sum() else None,
        "alarms_per_instance": float(alarms / frame.instance_hash.nunique()),
        "unique_instances": int(frame.instance_hash.nunique()),
    }


def score_group(frame: pd.DataFrame, methods: tuple[str, ...] = METHODS, alarms: bool = True) -> dict:
    if frame.empty:
        return {"n": 0, "positive": 0, "positive_rate": None, "methods": {}}
    y = frame.y.to_numpy(np.int8)
    result = {
        "n": int(len(frame)), "positive": int(y.sum()), "positive_rate": float(y.mean()),
        "unique_instances": int(frame.instance_hash.nunique()), "methods": {},
    }
    for name in methods:
        p = frame[f"pred_{name}"].to_numpy(float)
        scores = {
            "brier": float(brier_score_loss(y, p)),
            "average_precision": float(average_precision_score(y, p)) if y.sum() else None,
        }
        if alarms:
            scores["top5pct_alarm"] = _alarm_metrics(frame, p)
        result["methods"][name] = scores
    return result


def run_scoring(examples: pd.DataFrame) -> tuple[dict, pd.DataFrame, object]:
    train = examples.loc[examples.split.eq("train")].copy()
    validation = examples.loc[examples.split.eq("validation")].copy()
    test = examples.loc[examples.split.eq("test")].copy()
    parameters, selection, model = fit_and_select(train, validation)
    for name in BASELINES:
        test[f"pred_{name}"] = baseline_predict(test, parameters, name)
    test["pred_logistic"] = model.predict_proba(test[list(FEATURES)])[:, 1]
    overall = score_group(test)
    by_day = {str(day): score_group(group.copy()) for day, group in test.groupby("day", sort=True)}
    by_current = {
        "currently_below_threshold": score_group(test.loc[test.current_threshold.eq(0)].copy(), alarms=False),
        "currently_over_threshold": score_group(test.loc[test.current_threshold.eq(1)].copy(), alarms=False),
    }
    selected = selection["selected_baseline"]
    model_score = overall["methods"]["logistic"]
    base_score = overall["methods"][selected]
    day_brier_better = all(
        entry["methods"]["logistic"]["brier"] < entry["methods"][selected]["brier"]
        for entry in by_day.values()
    )
    success_checks = {
        "overall_relative_brier_gain_at_least_5pct": (
            model_score["brier"] <= 0.95 * base_score["brier"]
            if base_score["brier"] > 0 else False
        ),
        "both_test_days_brier_better": len(by_day) == 2 and day_brier_better,
        "average_precision_at_least_baseline": model_score["average_precision"] >= base_score["average_precision"],
        "top5pct_recall_at_least_baseline": (
            model_score["top5pct_alarm"]["recall"] >= base_score["top5pct_alarm"]["recall"]
        ),
    }
    result = {
        "selection": selection, "baseline_train_parameters": parameters,
        "test": {"overall": overall, "by_day": by_day, "by_current_threshold": by_current},
        "future_state_changed_in_eligible_test": {
            "cluster": int(test.future_cluster_changed.sum()),
            "gpu_count": int(test.future_gpu_count_changed.sum()),
        },
        "exploratory_success_checks": success_checks,
        "exploratory_success": all(success_checks.values()),
    }
    return result, test, model


def evaluate(source: Path = SOURCE, audit_path: Path = AUDIT, output: Path = OUTPUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    # Reusing an output directory must not leave an old fitted model or test
    # predictions beside metrics from a run that failed either gate.
    for old_artifact in ("logistic_scaler.joblib", "test_predictions.parquet"):
        (output / old_artifact).unlink(missing_ok=True)
    result = {
        "protocol_sha256": sha256_file(PROTOCOL),
        "evaluation_script_sha256": sha256_file(Path(__file__)),
        "sklearn_version": sklearn.__version__,
        "scoring_performed": False,
        "timing": {
            "decision_delay_s": DECISION_DELAY_S, "forecast_horizon_s": HORIZON_S,
            "label_delivery_delay_s": LABEL_DELIVERY_DELAY_S,
            "label_available_offset_from_decision_s": HORIZON_S + LABEL_DELIVERY_DELAY_S,
            "past_offsets_s": PAST_OFFSETS, "future_offsets_s": FUTURE_OFFSETS,
            "split_on": "decision_s", "top_alarm_fraction_per_day": 0.05,
            "top_alarm_count_rule": "ceil", "top_alarm_tie_rule": "SHA256(normalized original instance_id + ':' + decision_s)",
        },
    }
    try:
        result["source_verification"] = verify_source(source, audit_path)
        raw = load_compact(source)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result["source_gate"] = {"passes": False, "reason": str(exc)}
        (output / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        return result
    result["source_gate"] = {"passes": True}
    examples, diagnostics = construct_examples(raw)
    result["data_audit"] = diagnostics
    gates = quality_gates(diagnostics)
    result["sample_gates"] = gates
    if gates["passes"]:
        scoring, test, model = run_scoring(examples)
        result["scoring_performed"] = True
        result.update(scoring)
        model_path = output / "logistic_scaler.joblib"
        joblib.dump(model, model_path)
        result["fitted_model"] = {
            "path": str(model_path), "sha256": sha256_file(model_path),
            "selected_C": result["selection"]["selected_C"],
            "feature_order": FEATURES,
        }
        test[[
            "instance_hash", "decision_s", "day", "cluster_id", "gpu_count", "alarm_tie_hash",
            "y", "current_threshold", "any_recent_threshold", "future_cluster_changed",
            "future_gpu_count_changed", *[f"pred_{name}" for name in METHODS],
        ]].to_parquet(output / "test_predictions.parquet", index=False)
        result["test_predictions_sha256"] = sha256_file(output / "test_predictions.parquet")
    (output / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.source, args.audit, args.output)
    print(json.dumps({
        "scoring_performed": result["scoring_performed"],
        "source_gate": result["source_gate"],
        "sample_gates": result.get("sample_gates"),
        "exploratory_success": result.get("exploratory_success"),
        "metrics_path": str(args.output / "metrics.json"),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
