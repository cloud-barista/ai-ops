"""Frozen bucket-0 MaaS H/N/U model and one-time development validation.

Only filtered days 140--144 are returned from the compact Parquet scan. This
program has no bucket-1 URL, download code, or holdout scoring path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow
import pyarrow.dataset as ds
import pyarrow.compute as pc
import scipy
import sklearn
from scipy.optimize import minimize_scalar
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import evaluate_maas_instance_risk as source_contract
from audit_maas_sm_observability_support import classify_future


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_sample.parquet"
AUDIT = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_audit.json"
PROTOCOL = ROOT / "reports/maas_sm_observability_protocol_v1.md"
FEASIBILITY = ROOT / "artifacts/maas_sm_observability_v1/feasibility.json"
SUPPORT_AUDITOR = ROOT / "scripts/audit_maas_sm_observability_support.py"
TEST_FILE = ROOT / "tests/test_evaluate_maas_sm_observability_v1.py"
OUTPUT = ROOT / "artifacts/maas_sm_observability_v1"
EXPECTED_HASHES = {
    "source": "6f52288916f7a8be8bacb2d911bc20294ab7f6685eda2b1f0f63cfc77af77e82",
    "protocol": "79e82f9a03093b8423097cd6e8f628a58513effaddb12f5d587a5bc11b56774b",
    "feasibility": "249c475b93e60b4bd492a15f7322d440e5973bec29f5766f4a6756793df8e103",
    "support_auditor": "e85aa2b4a150a5752cbd7edd0317712de2c4c5e2dbb246edeb67864c6c41d7d9",
}
DAY_S = 86_400
WINDOW_S = 300
DECISION_DELAY_S = 600
LABEL_DELIVERY_DELAY_S = 300
HORIZON_S = 1_800
SPLITS = {
    "fit": (140 * DAY_S, 143 * DAY_S),
    "calibration": (143 * DAY_S, 144 * DAY_S),
    "validation": (144 * DAY_S, 145 * DAY_S),
}
CLASS_NAMES = ("observed_high", "fully_observed_normal", "unresolved")
PAST_OFFSETS = (-600, -300)
FUTURE_OFFSETS = (600, 900, 1200, 1500, 1800, 2100)
FEATURES = (
    "sm_oldest", "sm_middle", "sm_current",
    "samples_oldest", "samples_middle", "samples_current",
    "oldest_valid", "middle_valid", "gpu_count", "sm_mean_valid",
    "sm_max_valid", "sm_change_from_oldest_valid",
)
METHODS = ("two_stage_hgb", "global_frequency", "conditional_frequency",
           "multinomial_logistic", "direct_hgb")
BASELINES = METHODS[1:]
HGB_PARAMS = dict(max_iter=150, learning_rate=0.05, max_leaf_nodes=15,
                  min_samples_leaf=100, l2_regularization=1.0,
                  early_stopping=False, random_state=42)
SOURCE_COLUMNS = ("instance_hash", "time_s", "cluster_id", "gpu_count",
                  "sm_util", "samples")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> tuple[dict, dict]:
    for name, path in (("source", SOURCE), ("protocol", PROTOCOL),
                       ("feasibility", FEASIBILITY),
                       ("support_auditor", SUPPORT_AUDITOR)):
        if sha256_file(path) != EXPECTED_HASHES[name]:
            raise ValueError(f"pinned_{name}_sha256_mismatch")
    source = source_contract.verify_source(SOURCE, AUDIT)
    if source["compact_sha256"] != EXPECTED_HASHES["source"]:
        raise ValueError("source_contract_hash_mismatch")
    feasibility = json.loads(FEASIBILITY.read_text(encoding="utf-8"))
    if (feasibility.get("source_compact_sha256") != source["compact_sha256"]
            or feasibility.get("source_audit_sha256") != source["audit_sha256"]
            or feasibility.get("status") != "FEASIBILITY_ONLY_NO_MODEL_SCORES_NO_BUCKET1_FETCH"):
        raise ValueError("feasibility_provenance_mismatch")
    return source, feasibility


def load_development_only() -> pd.DataFrame:
    """Push time restriction into Arrow scan before materializing any rows."""
    dataset = ds.dataset(SOURCE, format="parquet")
    predicate = ((pc.field("time_s") >= 140 * DAY_S)
                 & (pc.field("time_s") < 145 * DAY_S))
    raw = dataset.to_table(columns=list(SOURCE_COLUMNS), filter=predicate,
                           use_threads=False).to_pandas()
    if (raw.empty or raw.time_s.isna().any()
            or not raw.time_s.between(140 * DAY_S, 145 * DAY_S - 1).all()
            or not np.equal(raw.time_s.to_numpy(np.int64) % WINDOW_S, 0).all()):
        raise ValueError("development_time_filter_or_alignment_failed")
    if (raw.instance_hash.isna().any()
            or not raw.instance_hash.astype(str).str.fullmatch(r"[0-9a-f]{64}").all()):
        raise ValueError("invalid_development_instance_hash")
    for column in ("gpu_count", "sm_util", "samples"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    return raw


def _unique_keys(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    times = frame.time_s.to_numpy(np.int64)
    codes, _ = pd.factorize(frame.instance_hash, sort=False)
    keys = np.empty(len(frame), dtype=[("instance", "<i8"), ("time", "<i8")])
    keys["instance"], keys["time"] = codes, times
    if len(keys) > 1 and ((codes[1:] < codes[:-1]).any()
                          or ((codes[1:] == codes[:-1])
                              & (times[1:] < times[:-1])).any()):
        raise AssertionError("stage_key_sort_failed")
    repeated = keys[1:] == keys[:-1]
    unique = np.ones(len(keys), dtype=bool)
    unique[1:] &= ~repeated
    unique[:-1] &= ~repeated
    return times, codes, keys, unique


def _class_support(ids: np.ndarray, classes: np.ndarray, grouping: np.ndarray) -> dict:
    result = {}
    for code, name in enumerate(CLASS_NAMES):
        mask = classes == code
        counts = pd.Series(ids[mask]).value_counts()
        result[name] = {
            "rows": int(mask.sum()),
            "instances": int(len(counts)),
            "largest_instance_fraction": (float(counts.max() / mask.sum())
                                          if mask.any() else None),
        }
    return result


def construct_stage(raw: pd.DataFrame, split: str) -> tuple[pd.DataFrame, dict]:
    """Build a stage-local exact lookup; only past 20 minutes cross its start."""
    if split not in SPLITS:
        raise ValueError("unknown_development_split")
    start, end = SPLITS[split]
    window = raw.loc[(raw.time_s >= max(140 * DAY_S, start - 1_200))
                     & (raw.time_s < end)].copy()
    if window.empty or (window.time_s >= 145 * DAY_S).any():
        raise ValueError("empty_or_out_of_scope_stage_window")
    frame = window.sort_values(["instance_hash", "time_s"],
                               kind="mergesort").reset_index(drop=True)
    times, codes, keys, unique = _unique_keys(frame)
    ids = frame.instance_hash.to_numpy()
    sm = frame.sm_util.to_numpy(float)
    samples = frame.samples.to_numpy(float)
    gpu = frame.gpu_count.to_numpy(float)
    cluster_present = frame.cluster_id.notna().to_numpy()
    cluster = frame.cluster_id.astype("string").fillna("<missing>").to_numpy(dtype=str)
    valid_measurement = (np.isfinite(sm) & (sm >= 0) & (sm <= 100)
                         & np.isfinite(samples) & (samples > 0)
                         & np.isfinite(gpu) & (gpu > 0) & cluster_present)
    decision = times + DECISION_DELAY_S
    candidate = (unique & valid_measurement & (sm < 80)
                 & (decision >= start)
                 & (decision + HORIZON_S + LABEL_DELIVERY_DELAY_S <= end))
    selected = np.flatnonzero(candidate)
    if not len(selected):
        raise ValueError("no_current_normal_decisions")

    past_positions, past_valid = [], []
    future_positions, future_valid = [], []
    for offset in (*PAST_OFFSETS, *FUTURE_OFFSETS):
        position, found = source_contract._exact_lookup(
            keys, codes[selected], times[selected], offset)
        same_allocation = ((cluster[position] == cluster[selected])
                           & (gpu[position] == gpu[selected]))
        valid = found & unique[position] & valid_measurement[position] & same_allocation
        if offset < 0:
            past_positions.append(position)
            past_valid.append(valid)
        else:
            future_positions.append(position)
            future_valid.append(valid)
    past_ok = np.column_stack(past_valid)
    future_ok = np.column_stack(future_valid)
    future_high = np.column_stack([sm[position] >= 80 for position in future_positions])
    classes = classify_future(future_ok, future_high)
    past_sm = np.column_stack([sm[position] for position in past_positions])
    past_samples = np.column_stack([samples[position] for position in past_positions])
    past_sm = np.where(past_ok, past_sm, np.nan)
    past_samples = np.where(past_ok, past_samples, np.nan)
    sm_valid = np.column_stack([past_sm, sm[selected]])
    oldest_past = np.where(past_ok[:, 0], past_sm[:, 0],
                           np.where(past_ok[:, 1], past_sm[:, 1], np.nan))
    decisions = decision[selected]
    day = decisions // DAY_S
    block = (decisions % DAY_S) // 21_600
    examples = pd.DataFrame({
        "instance_hash": ids[selected], "decision_s": decisions,
        "day": day.astype(np.int16), "six_hour_block": block.astype(np.int8),
        "y": classes,
        "sm_oldest": past_sm[:, 0], "sm_middle": past_sm[:, 1],
        "sm_current": sm[selected],
        "samples_oldest": past_samples[:, 0],
        "samples_middle": past_samples[:, 1],
        "samples_current": samples[selected],
        "oldest_valid": past_ok[:, 0].astype(np.int8),
        "middle_valid": past_ok[:, 1].astype(np.int8),
        "gpu_count": gpu[selected],
        "sm_mean_valid": np.nanmean(sm_valid, axis=1),
        "sm_max_valid": np.nanmax(sm_valid, axis=1),
        "sm_change_from_oldest_valid": sm[selected] - oldest_past,
    })
    if not examples["y"].isin((0, 1, 2)).all() or examples["decision_s"].max() + 2_100 > end:
        raise AssertionError("invalid_stage_label_or_closure")
    if len(examples) != len(selected):
        raise AssertionError("candidate_count_changed_by_future")
    past_count = past_ok.sum(axis=1) + 1
    summary = {
        "candidate_decisions": int(len(selected)),
        "distinct_instances": int(pd.Series(ids[selected]).nunique()),
        "past_valid_count": {str(k): int((past_count == k).sum()) for k in (1, 2, 3)},
        "future_full_observation": int(future_ok.all(axis=1).sum()),
        "future_incomplete": int((~future_ok.all(axis=1)).sum()),
        "class_counts": _class_support(ids[selected], classes, block),
        "by_day": {}, "by_six_hour_block": {},
    }
    for title, groups in (("by_day", day), ("by_six_hour_block", block)):
        for value in sorted(set(groups)):
            mask = groups == value
            summary[title][str(int(value))] = {
                label: int((mask & (classes == code)).sum())
                for code, label in enumerate(CLASS_NAMES)
            }
    return examples, summary


def check_support(actual: dict, feasibility: dict, raw_count: int) -> dict:
    """Demand exact reproduction and all predeclared row/ID/block gates."""
    if raw_count != feasibility["raw_development_rows"]:
        raise ValueError("development_raw_row_count_mismatch")
    if actual != feasibility["splits"]:
        raise ValueError("development_support_reproduction_mismatch")
    checks: dict[str, bool] = {}
    for split in SPLITS:
        stage = actual[split]
        cc = stage["class_counts"]
        h, n, u = (cc[label] for label in CLASS_NAMES)
        if split == "fit":
            checks["fit_H_rows"] = h["rows"] >= 3_000
            checks["fit_N_rows"] = n["rows"] >= 100_000
            checks["fit_U_rows"] = u["rows"] >= 15_000
            checks["fit_each_class_ids"] = all(v["instances"] >= 200 for v in cc.values())
        else:
            checks[f"{split}_candidates"] = stage["candidate_decisions"] >= 50_000
            checks[f"{split}_H_rows_ids"] = h["rows"] >= 1_000 and h["instances"] >= 200
            checks[f"{split}_N_rows"] = n["rows"] >= 20_000
            checks[f"{split}_U_rows_ids"] = u["rows"] >= 5_000 and u["instances"] >= 500
        checks[f"{split}_largest_class_id_fraction"] = all(
            v["largest_instance_fraction"] is not None
            and v["largest_instance_fraction"] <= 0.10 for v in cc.values())
    for block in range(4):
        counts = actual["validation"]["by_six_hour_block"].get(str(block), {})
        checks[f"validation_block_{block}_support"] = (
            counts.get("observed_high", 0) >= 100
            and counts.get("unresolved", 0) >= 1_000)
    if not all(checks.values()):
        raise ValueError("development_support_gate_failed:" +
                         ",".join(k for k, passed in checks.items() if not passed))
    return checks


def prepare_development() -> tuple[dict[str, pd.DataFrame], dict]:
    source, feasibility = verify_inputs()
    raw = load_development_only()
    frames = {}
    summaries = {}
    for split in SPLITS:
        examples, summary = construct_stage(raw, split)
        frames[split] = examples
        summaries[split] = summary
    checks = check_support(summaries, feasibility, len(raw))
    return frames, {"source": source, "raw_development_rows": len(raw),
                    "support": summaries, "support_checks": checks}


def equal_id_weights(ids: np.ndarray) -> np.ndarray:
    if not len(ids):
        raise ValueError("cannot_weight_empty_stage")
    code, uniques = pd.factorize(ids, sort=False)
    if (code < 0).any():
        raise ValueError("null_instance_id")
    counts = np.bincount(code)
    weights = len(ids) / (len(uniques) * counts[code].astype(float))
    if not math.isclose(float(weights.sum()), len(ids), rel_tol=0, abs_tol=1e-7):
        raise AssertionError("ID_equal_weight_sum_failed")
    return weights


def _feature_matrix(frame: pd.DataFrame) -> np.ndarray:
    if any(field not in frame for field in FEATURES):
        raise ValueError("missing_decision_time_feature")
    value = frame[list(FEATURES)].to_numpy(dtype=np.float64)
    if np.isinf(value).any():
        raise ValueError("infinite_decision_time_feature")
    return value


def _normalize_probability(prob: np.ndarray) -> np.ndarray:
    if prob.ndim != 2 or prob.shape[1] != 3 or not np.isfinite(prob).all():
        raise ValueError("invalid_three_class_probability")
    clipped = np.clip(prob, 1e-6, 1.0)
    return clipped / clipped.sum(axis=1, keepdims=True)


def _conditional_cells(frame: pd.DataFrame) -> np.ndarray:
    sm_bin = np.searchsorted([5.0, 20.0, 50.0, 80.0],
                             frame.sm_current.to_numpy(float), side="right")
    past_count = (frame.oldest_valid.to_numpy(int)
                  + frame.middle_valid.to_numpy(int) + 1)
    gpu_bin = np.searchsorted([1.0, 4.0], frame.gpu_count.to_numpy(float),
                              side="left")
    if (np.any(sm_bin > 3) or np.any(past_count < 1) or np.any(past_count > 3)
            or np.any(gpu_bin > 2)):
        raise ValueError("conditional_frequency_bin_out_of_range")
    return ((sm_bin * 3 + past_count - 1) * 3 + gpu_bin).astype(np.int16)


def _logistic_design(x: np.ndarray, medians: np.ndarray,
                     scaler: StandardScaler | None = None) -> np.ndarray:
    missing = np.isnan(x)
    expanded = np.concatenate((np.where(missing, medians, x),
                               missing.astype(np.float64)), axis=1)
    return expanded if scaler is None else scaler.transform(expanded)


def fit_models(frame: pd.DataFrame) -> dict:
    """Fit only day-140--142 labels, with each ID contributing equal weight."""
    if frame.day.min() != 140 or frame.day.max() != 142:
        raise ValueError("fit_day_boundary_failed")
    x = _feature_matrix(frame)
    y = frame.y.to_numpy(np.int8)
    weights = equal_id_weights(frame.instance_hash.to_numpy())
    onehot = np.eye(3)[y]
    global_count = np.sum(weights[:, None] * onehot, axis=0)
    global_p = (global_count + 1.0) / (weights.sum() + 3.0)
    cells = _conditional_cells(frame)
    cell_count = np.zeros((36, 3), dtype=np.float64)
    for label in range(3):
        cell_count[:, label] = np.bincount(cells[y == label],
                                          weights=weights[y == label], minlength=36)
    conditional_p = (cell_count + 50.0 * global_p[None, :]) / (
        cell_count.sum(axis=1, keepdims=True) + 50.0)

    hgb_h = HistGradientBoostingClassifier(**HGB_PARAMS)
    hgb_h.fit(x, (y == 0).astype(np.int8), sample_weight=weights)
    other = y != 0
    hgb_u = HistGradientBoostingClassifier(**HGB_PARAMS)
    hgb_u.fit(x[other], (y[other] == 2).astype(np.int8),
              sample_weight=equal_id_weights(frame.instance_hash.to_numpy()[other]))
    direct = HistGradientBoostingClassifier(**HGB_PARAMS)
    direct.fit(x, y, sample_weight=weights)
    if not np.array_equal(direct.classes_, [0, 1, 2]):
        raise ValueError("direct_hgb_missing_class")

    medians = np.nanmedian(x, axis=0)
    if not np.isfinite(medians).all():
        raise ValueError("training_feature_all_missing")
    designed = _logistic_design(x, medians)
    scaler = StandardScaler().fit(designed, sample_weight=weights)
    logistic = LogisticRegression(C=1.0, penalty="l2", max_iter=1000,
                                  random_state=42)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        logistic.fit(scaler.transform(designed), y, sample_weight=weights)
    if not np.array_equal(logistic.classes_, [0, 1, 2]):
        raise ValueError("logistic_missing_class")
    return {"hgb_h": hgb_h, "hgb_u": hgb_u, "direct_hgb": direct,
            "logistic": logistic, "scaler": scaler, "imputation_medians": medians,
            "global_probability": global_p, "conditional_probability": conditional_p,
            "feature_names": FEATURES, "hgb_params": HGB_PARAMS,
            "fit_rows": int(len(frame)), "fit_ids": int(frame.instance_hash.nunique())}


def predict_raw(bundle: dict, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    x = _feature_matrix(frame)
    h = bundle["hgb_h"].predict_proba(x)[:, 1]
    q = bundle["hgb_u"].predict_proba(x)[:, 1]
    two_stage = np.column_stack((h, (1.0 - h) * (1.0 - q), (1.0 - h) * q))
    logistic_x = _logistic_design(x, bundle["imputation_medians"], bundle["scaler"])
    probabilities = {
        "two_stage_hgb": two_stage,
        "global_frequency": np.tile(bundle["global_probability"], (len(frame), 1)),
        "conditional_frequency": bundle["conditional_probability"][_conditional_cells(frame)],
        "multinomial_logistic": bundle["logistic"].predict_proba(logistic_x),
        "direct_hgb": bundle["direct_hgb"].predict_proba(x),
    }
    return {name: _normalize_probability(value) for name, value in probabilities.items()}


def temperature_transform(prob: np.ndarray, temperature: float) -> np.ndarray:
    if not (0.5 <= temperature <= 5.0):
        raise ValueError("temperature_out_of_fixed_range")
    power = np.log(_normalize_probability(prob)) / temperature
    power -= power.max(axis=1, keepdims=True)
    exp = np.exp(power)
    return exp / exp.sum(axis=1, keepdims=True)


def _weighted_log_loss(y: np.ndarray, prob: np.ndarray, weights: np.ndarray) -> float:
    return float(np.average(-np.log(np.clip(prob[np.arange(len(y)), y], 1e-15, 1)),
                            weights=weights))


def calibrate_temperatures(raw_prob: dict[str, np.ndarray],
                           frame: pd.DataFrame) -> tuple[dict[str, float], dict]:
    if frame.day.nunique() != 1 or frame.day.iloc[0] != 143:
        raise ValueError("temperature_calibration_day_boundary_failed")
    y = frame.y.to_numpy(np.int8)
    weights = equal_id_weights(frame.instance_hash.to_numpy())
    temperatures = {}
    diagnostics = {}
    for name in METHODS:
        p = raw_prob[name]
        objective = lambda value: _weighted_log_loss(y, temperature_transform(p, value), weights)
        result = minimize_scalar(objective, bounds=(0.5, 5.0), method="bounded",
                                 options={"xatol": 1e-5, "maxiter": 200})
        success = bool(result.success and np.isfinite(result.x) and np.isfinite(result.fun))
        # The optimizer searches the interior; compare both allowed endpoints
        # to honor the predeclared closed interval.
        temperature = (min((0.5, float(result.x), 5.0),
                           key=lambda value: (objective(value), value))
                       if success else 1.0)
        temperatures[name] = temperature
        diagnostics[name] = {
            "temperature": temperature, "optimizer_success": success,
            "fallback_T1": not success,
            "calibration_ID_equal_logloss": float(objective(temperature)),
        }
    return temperatures, diagnostics


def _weighted_brier(y: np.ndarray, prob: np.ndarray, weights: np.ndarray) -> float:
    onehot = np.eye(3)[y]
    return float(np.average(np.sum((prob - onehot) ** 2, axis=1), weights=weights))


def _weighted_ece(y_binary: np.ndarray, prob: np.ndarray,
                  weights: np.ndarray) -> tuple[float, int]:
    """Ten equal-weight bins, retaining identical probabilities in one bin."""
    order = np.argsort(prob, kind="mergesort")
    sorted_prob = prob[order]
    sorted_y = y_binary[order]
    sorted_weight = weights[order]
    unique, first, counts = np.unique(sorted_prob, return_index=True,
                                      return_counts=True)
    group_weight = np.add.reduceat(sorted_weight, first)
    midpoint = np.cumsum(group_weight) - 0.5 * group_weight
    group_bin = np.minimum((10 * midpoint / group_weight.sum()).astype(int), 9)
    bin_index = np.repeat(group_bin, counts)
    ece = 0.0
    effective = 0
    for bin_id in range(10):
        mask = bin_index == bin_id
        if not mask.any():
            continue
        effective += 1
        bw = sorted_weight[mask].sum()
        observed = np.average(sorted_y[mask], weights=sorted_weight[mask])
        predicted = np.average(sorted_prob[mask], weights=sorted_weight[mask])
        ece += bw / weights.sum() * abs(observed - predicted)
    return float(ece), effective


def probability_metrics(frame: pd.DataFrame, prob: np.ndarray) -> dict:
    y = frame.y.to_numpy(np.int8)
    ids = frame.instance_hash.to_numpy()
    w = equal_id_weights(ids)
    row_w = np.ones(len(y), dtype=float)
    onehot = np.eye(3)[y]
    loss = np.sum((prob - onehot) ** 2, axis=1)
    code, _ = pd.factorize(ids, sort=False)
    id_loss = np.bincount(code, weights=loss) / np.bincount(code)
    result = {
        "rows": len(y), "instances": int(np.max(code) + 1),
        "ID_equal_brier": _weighted_brier(y, prob, w),
        "row_equal_brier": _weighted_brier(y, prob, row_w),
        "ID_equal_logloss": _weighted_log_loss(y, prob, w),
        "row_equal_logloss": _weighted_log_loss(y, prob, row_w),
        "per_ID_brier_quantiles": {
            str(q): float(np.quantile(id_loss, q)) for q in (0, .1, .5, .9, 1)
        },
        "one_vs_rest": {},
    }
    for code, name in ((0, "H"), (2, "U")):
        target = (y == code).astype(float)
        brier = np.average((prob[:, code] - target) ** 2, weights=w)
        ece, bins = _weighted_ece(target, prob[:, code], w)
        result["one_vs_rest"][name] = {
            "ID_equal_brier": float(brier),
            "ID_equal_ECE_10_equal_weight_bins": ece,
            "effective_bins_after_tie_preservation": bins,
        }
    observed = y != 2
    observed_weights = equal_id_weights(ids[observed])
    binary_p = prob[observed, 0] / (prob[observed, 0] + prob[observed, 1])
    result["H_vs_N_observed_only_ID_equal_brier"] = float(np.average(
        (binary_p - (y[observed] == 0)) ** 2, weights=observed_weights))
    result["exploratory_0_10_interval_abstain_row_fraction"] = float(np.mean(
        (prob[:, 0] <= .10) & (prob[:, 0] + prob[:, 2] >= .10)))
    result["exploratory_0_10_interval_abstain_ID_equal_fraction"] = float(np.average(
        (prob[:, 0] <= .10) & (prob[:, 0] + prob[:, 2] >= .10), weights=w))
    return result


def binary_difference_bounds(frame: pd.DataFrame, proposed: np.ndarray,
                             baseline: np.ndarray) -> dict:
    """All unresolved binary outcomes may independently be zero or one."""
    y = frame.y.to_numpy(np.int8)
    w = equal_id_weights(frame.instance_hash.to_numpy())
    p = proposed[:, 0] + .5 * proposed[:, 2]
    b = baseline[:, 0] + .5 * baseline[:, 2]
    difference_if_zero = p ** 2 - b ** 2
    difference_if_one = (1 - p) ** 2 - (1 - b) ** 2
    observed = np.where(y == 0, difference_if_one, difference_if_zero)
    lower = np.where(y == 2, np.minimum(difference_if_zero, difference_if_one), observed)
    upper = np.where(y == 2, np.maximum(difference_if_zero, difference_if_one), observed)
    return {"best_possible_ID_equal_brier_difference": float(np.average(lower, weights=w)),
            "worst_possible_ID_equal_brier_difference": float(np.average(upper, weights=w)),
            "contains_zero": bool(np.average(lower, weights=w) <= 0
                                  <= np.average(upper, weights=w))}


def validation_scores(frame: pd.DataFrame, predictions: dict[str, np.ndarray]) -> dict:
    if frame.day.nunique() != 1 or frame.day.iloc[0] != 144:
        raise ValueError("validation_day_boundary_failed")
    metrics = {name: probability_metrics(frame, predictions[name]) for name in METHODS}
    blocks = {}
    for block in range(4):
        part = frame.loc[frame.six_hour_block == block]
        if part.empty:
            raise ValueError("missing_validation_six_hour_block")
        blocks[str(block)] = {
            name: probability_metrics(part, prediction[frame.six_hour_block.to_numpy() == block])
            for name, prediction in predictions.items()
        }
    h = int((frame.y == 0).sum())
    u = int((frame.y == 2).sum())
    return {
        "day": 144, "rows": len(frame),
        "formal_binary_completion_rate_bounds": [h / len(frame),
                                                  (h + u) / len(frame)],
        "methods": metrics,
        "six_hour_blocks": blocks,
        "binary_risk_difference_sensitivity": {
            baseline: binary_difference_bounds(frame, predictions["two_stage_hgb"],
                                               predictions[baseline])
            for baseline in BASELINES
        },
    }


def performance_gate(scored: dict) -> dict:
    current = scored["methods"]["two_stage_hgb"]
    baselines = [scored["methods"][name] for name in BASELINES]
    checks = {}
    for name in BASELINES:
        other = scored["methods"][name]
        checks[f"ID_brier_5pct_below_{name}"] = (
            current["ID_equal_brier"] <= .95 * other["ID_equal_brier"])
        checks[f"row_brier_2pct_below_{name}"] = (
            current["row_equal_brier"] <= .98 * other["row_equal_brier"])
    checks["ID_logloss_within_1pct_best_baseline"] = (
        current["ID_equal_logloss"] <= 1.01 * min(v["ID_equal_logloss"] for v in baselines))
    for label in ("H", "U"):
        current_brier = current["one_vs_rest"][label]["ID_equal_brier"]
        best = min(v["one_vs_rest"][label]["ID_equal_brier"] for v in baselines)
        checks[f"{label}_ID_brier_within_2pct_best"] = current_brier <= 1.02 * best
    block_wins = sum(
        scored["six_hour_blocks"][str(block)]["two_stage_hgb"]["ID_equal_brier"]
        < min(scored["six_hour_blocks"][str(block)][name]["ID_equal_brier"]
              for name in BASELINES)
        for block in range(4)
    )
    checks["three_of_four_six_hour_block_wins"] = block_wins >= 3
    checks["H_ECE_at_most_0_03"] = (
        current["one_vs_rest"]["H"]["ID_equal_ECE_10_equal_weight_bins"] <= .03)
    checks["U_ECE_at_most_0_05"] = (
        current["one_vs_rest"]["U"]["ID_equal_ECE_10_equal_weight_bins"] <= .05)
    return {"all_passed": all(checks.values()), "checks": checks,
            "six_hour_block_wins": int(block_wins),
            "failed": [key for key, passed in checks.items() if not passed]}


def _code_and_environment() -> dict:
    return {
        "script_sha256": sha256_file(Path(__file__)),
        "test_sha256": sha256_file(TEST_FILE),
        "python": os.sys.version.split()[0],
        "numpy": np.__version__, "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__, "scipy": scipy.__version__,
        "pyarrow": pyarrow.__version__,
    }


def atomic_write_json(path: Path, value: dict) -> None:
    """Publish a complete JSON artifact only after its bytes reach disk."""
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_dump_model(path: Path, value: dict) -> None:
    """Publish the fitted bundle only after joblib finishes writing it."""
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        joblib.dump(value, temporary, compress=3)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def release_links(training_path: Path, baseline_path: Path,
                  calibration_path: Path, model_hashes: dict[str, str]) -> dict:
    """Use the same file/hash fields expected by the bucket-1 release check."""
    script_hash = sha256_file(Path(__file__))
    return {
        "protocol_sha256": EXPECTED_HASHES["protocol"],
        "training_script_sha256": script_hash,
        "evaluation_script_sha256": script_hash,
        "training_result_sha256": sha256_file(training_path),
        "calibration_parameters_sha256": sha256_file(calibration_path),
        "baseline_parameters_sha256": sha256_file(baseline_path),
        "fitted_models_sha256": model_hashes,
    }


def freeze() -> dict:
    frames, prepared = prepare_development()
    del frames
    freeze_path = OUTPUT / "development_freeze.json"
    if freeze_path.exists():
        raise FileExistsError("development_freeze_already_exists")
    record = {
        "status": "FROZEN_BEFORE_TRAINING_AND_144_SCORING",
        "scope": "bucket0 days 140-144 only; bucket0 145-146 and bucket1 never scored",
        "source_sha256": EXPECTED_HASHES["source"],
        "source_audit_sha256": prepared["source"]["audit_sha256"],
        "protocol_sha256": EXPECTED_HASHES["protocol"],
        "feasibility_sha256": EXPECTED_HASHES["feasibility"],
        "support_auditor_sha256": EXPECTED_HASHES["support_auditor"],
        "code_and_environment": _code_and_environment(),
        "raw_development_rows": prepared["raw_development_rows"],
        "support": prepared["support"],
        "support_checks": prepared["support_checks"],
        "features": FEATURES, "methods": METHODS,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(freeze_path, record)
    return record


def fit_validate() -> dict:
    freeze_path = OUTPUT / "development_freeze.json"
    if not freeze_path.exists():
        raise ValueError("missing_pretraining_development_freeze")
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    if (frozen["status"] != "FROZEN_BEFORE_TRAINING_AND_144_SCORING"
            or frozen["code_and_environment"] != _code_and_environment()
            or frozen["feasibility_sha256"] != EXPECTED_HASHES["feasibility"]):
        raise ValueError("development_freeze_code_or_environment_mismatch")
    result_path = OUTPUT / "validation_result.json"
    start_path = OUTPUT / "training_started.json"
    if result_path.exists() or start_path.exists():
        raise FileExistsError("one_time_development_validation_already_started")
    frames, prepared = prepare_development()
    if (prepared["support"] != frozen["support"]
            or prepared["support_checks"] != frozen["support_checks"]
            or prepared["raw_development_rows"] != frozen["raw_development_rows"]):
        raise ValueError("development_support_changed_after_freeze")
    # Exclusive creation makes a second training/validation attempt visible.
    descriptor = os.open(start_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"development_freeze_sha256": sha256_file(freeze_path),
                   "status": "TRAINING_STARTED_ONCE"}, stream)
        stream.write("\n")
    bundle = fit_models(frames["fit"])
    calibration_raw = predict_raw(bundle, frames["calibration"])
    temperatures, calibration_detail = calibrate_temperatures(
        calibration_raw, frames["calibration"])
    bundle["temperatures"] = temperatures
    model_path = OUTPUT / "development_models.joblib"
    atomic_dump_model(model_path, bundle)
    baseline_path = OUTPUT / "baseline_parameters.json"
    atomic_write_json(baseline_path, {
        "global_class_probability": bundle["global_probability"].tolist(),
        "conditional_class_probability_36_cells": bundle["conditional_probability"].tolist(),
        "conditional_bins": {
            "current_SM": [0, 5, 20, 50, 80],
            "valid_past_plus_current_count": [1, 2, 3],
            "GPU_count": ["(0,1]", "(1,4]", "(4,infinity)"],
            "prior_equivalent_rows": 50,
        },
        "class_order": CLASS_NAMES,
        "fit_rows": len(frames["fit"]),
    })
    calibration_path = OUTPUT / "calibration_parameters.json"
    atomic_write_json(calibration_path, {
        "temperature_by_method": temperatures,
        "optimization": calibration_detail,
        "calibration_rows": len(frames["calibration"]),
        "calibration_instances": int(frames["calibration"].instance_hash.nunique()),
        "objective": "ID-equal-weight three-class log loss, T in [0.5,5]",
    })
    model_hashes = {str(model_path.relative_to(ROOT)): sha256_file(model_path)}
    training_path = OUTPUT / "training_result.json"
    atomic_write_json(training_path, {
        "status": "FITTED_AND_TEMPERATURE_CALIBRATED_BEFORE_VALIDATION_SCORE",
        "development_freeze_sha256": sha256_file(freeze_path),
        "training_script_sha256": sha256_file(Path(__file__)),
        "fit_rows": len(frames["fit"]),
        "fit_instances": int(frames["fit"].instance_hash.nunique()),
        "feature_names": FEATURES,
        "method_names": METHODS,
        "fitted_models_sha256": model_hashes,
        "baseline_parameters_sha256": sha256_file(baseline_path),
        "calibration_parameters_sha256": sha256_file(calibration_path),
    })
    validation_raw = predict_raw(bundle, frames["validation"])
    validation_prob = {name: temperature_transform(validation_raw[name], temperatures[name])
                       for name in METHODS}
    scored = validation_scores(frames["validation"], validation_prob)
    gate = performance_gate(scored)
    report = {
        "status": "DEVELOPMENT_GO_FOR_SEPARATE_BUCKET1_REVIEW" if gate["all_passed"]
                  else "DEVELOPMENT_NO_GO_BUCKET1_UNOPENED",
        "scope": "observed same-allocation SM 5-minute-average state, not user SLO or VM scale action",
        "source_compact_sha256": EXPECTED_HASHES["source"],
        "source_audit_sha256": prepared["source"]["audit_sha256"],
        "feasibility_sha256": EXPECTED_HASHES["feasibility"],
        "support_auditor_sha256": EXPECTED_HASHES["support_auditor"],
        "development_freeze_sha256": sha256_file(freeze_path),
        "development_script_sha256": sha256_file(Path(__file__)),
        "development_tests_sha256": sha256_file(TEST_FILE),
        "development_models_sha256": sha256_file(model_path),
        # These links have the names required by the separate bucket-1
        # release-manifest verifier. Both roles use this one frozen script.
        **release_links(training_path, baseline_path, calibration_path, model_hashes),
        "dependency_versions": {
            "python": os.sys.version.split()[0], "numpy": np.__version__,
            "pandas": pd.__version__, "sklearn": sklearn.__version__,
            "pyarrow": pyarrow.__version__,
        },
        "validation_gate_pass": bool(gate["all_passed"]),
        "code_and_environment": _code_and_environment(),
        "support_checks": prepared["support_checks"],
        "support": prepared["support"],
        "training": {"rows": len(frames["fit"]),
                     "instances": int(frames["fit"].instance_hash.nunique()),
                     "feature_names": FEATURES,
                     "first_stage_classes": bundle["hgb_h"].classes_.tolist(),
                     "second_stage_classes": bundle["hgb_u"].classes_.tolist(),
                     "direct_classes": bundle["direct_hgb"].classes_.tolist(),
                     "logistic_classes": bundle["logistic"].classes_.tolist()},
        "calibration": calibration_detail,
        "validation": scored,
        "development_gate": gate,
        "holdout_bucket1_accessed": False,
        "exposed_bucket0_days145_146_scored": False,
    }
    atomic_write_json(result_path, report)
    atomic_write_json(OUTPUT / "development_manifest.json", {
        "validation_result_sha256": sha256_file(result_path),
        "training_result_sha256": sha256_file(training_path),
        "calibration_parameters_sha256": sha256_file(calibration_path),
        "baseline_parameters_sha256": sha256_file(baseline_path),
        "fitted_models_sha256": model_hashes,
        "development_freeze_sha256": sha256_file(freeze_path),
        "script_sha256": sha256_file(Path(__file__)),
        "tests_sha256": sha256_file(TEST_FILE),
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "fit_validate"))
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze()
        print(json.dumps({"status": result["status"],
                          "support_checks": result["support_checks"]},
                         ensure_ascii=False, indent=2))
    else:
        result = fit_validate()
        print(json.dumps({"status": result["status"],
                          "development_gate": result["development_gate"]},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
