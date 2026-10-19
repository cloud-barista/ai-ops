"""Causal 5-minute SwissAI demand forecast from the frozen protocol.

Run --dry-run before --score. The former writes and audits the decision rows;
the latter fits the two fixed quantile models and scores only July 16--August 16.
No row created on or after August 16 is processed for features or scoring.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import resource
import time

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from fetch_swissai_trace import file_digest as source_digest, local_source_path

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    # The tiny synthetic timing tests can use the same exact Python function.
    # Full-trace evaluation should use a Python environment with numba installed.
    HAS_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda function: function


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/swissai_serving_trace"
OUTPUT = ROOT / "artifacts/swissai_demand_v1"
PROTOCOL = ROOT / "reports/swissai_demand_protocol.md"
EXPECTED_PROTOCOL_SHA = "a50ded6b5d29ec83dc84efbd7635dd63a0f05d832175e362d174ad71495fed8d"
EXPECTED_RAW_SHA = "79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7"
EXPECTED_COMPACT_SHA = "d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b"
EXPECTED_ALIASES = ("45363f1dab", "99668f5da8")
US = 1_000_000
MIN_US = 60 * US
BIN_US = 5 * MIN_US
HOUR_US = 60 * MIN_US
DAY_US = 24 * HOUR_US
LOOKBACK_US = 120 * MIN_US
FEEDBACK_DELAY_US = HOUR_US
GAP_US = 6 * HOUR_US
Q = 0.9
START = "2025-01-16"
FIT_END = "2025-06-16"
VAL_END = "2025-07-16"
TEST_END = "2025-08-16"
SPLITS = ("fit", "validation", "test")
ALIASES = tuple(sorted(EXPECTED_ALIASES))
EXPECTED_DECISION_AUDIT = {
    "45363f1dab:fit": (1104, 718, 299, 419),
    "99668f5da8:fit": (18345, 11708, 9681, 2027),
    "45363f1dab:validation": (2099, 1526, 1203, 323),
    "99668f5da8:validation": (2329, 1513, 743, 770),
    "45363f1dab:test": (2492, 1747, 1037, 710),
    "99668f5da8:test": (2620, 1681, 880, 801),
}
ARRIVAL_FEATURES = [
    "model_code", "m5", "m10", "m30", "m60", "m120",
    "g5", "g10", "g30", "g60", "g120",
    "sin_day", "cos_day", "sin_week", "cos_week",
]
FEEDBACK_FEATURES = ["c30", "c120", "duration_p90_logsec", "error_rate120", "completion_empty120"]
SIMPLE_METHODS = ("recent_p90", "persistence", "fixed_p90")


def timestamp_us(date: str) -> int:
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp() * US)


T0 = timestamp_us(START)
T_FIT = timestamp_us(FIT_END)
T_VAL = timestamp_us(VAL_END)
T_END = timestamp_us(TEST_END)
N_BINS = (T_END - T0) // BIN_US
SPLIT_BOUNDS = ((T0, T_FIT), (T_FIT, T_VAL), (T_VAL, T_END))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def assert_sources() -> dict[str, str]:
    if sha256(PROTOCOL) != EXPECTED_PROTOCOL_SHA:
        raise ValueError("Frozen SwissAI demand protocol SHA256 changed")
    audit_path = SOURCE / "audit.json"
    compact_path = SOURCE / "requests_sorted.parquet"
    raw_path = local_source_path(SOURCE)
    audit = json.loads(audit_path.read_text())
    if audit["source"]["sha256"] != EXPECTED_RAW_SHA or audit["compact"]["rows"] != 16_329_237:
        raise ValueError("SwissAI audit metadata changed")
    if source_digest(raw_path)[1] != EXPECTED_RAW_SHA:
        raise ValueError("SwissAI raw trace SHA256 mismatch")
    if sha256(compact_path) != EXPECTED_COMPACT_SHA:
        raise ValueError("SwissAI compact trace SHA256 mismatch")
    return {"raw_sha256": EXPECTED_RAW_SHA, "compact_sha256": EXPECTED_COMPACT_SHA,
            "audit_sha256": sha256(audit_path), "protocol_sha256": EXPECTED_PROTOCOL_SHA}


def select_models(audit: dict) -> tuple[str, ...]:
    """Use train/validation arrival counts only; never consult test models."""
    fit = Counter()
    val = Counter()
    for key, value in audit["by_model_day"].items():
        date, sep, model = key.partition(" | ")
        if not sep:
            raise ValueError("Malformed model-day audit key")
        if START <= date < FIT_END:
            fit[model] += int(value["rows"])
        elif FIT_END <= date < VAL_END:
            val[model] += int(value["rows"])
    eligible = [m for m, n in fit.items() if n >= 5_000 and val[m] >= 10_000]
    ranked = sorted(eligible, key=lambda m: (-val[m], hashlib.sha256(m.encode()).hexdigest()[:10]))[:2]
    if len(ranked) != 2:
        raise ValueError("Fewer than two eligible train/validation models")
    aliases = tuple(hashlib.sha256(m.encode()).hexdigest()[:10] for m in ranked)
    if set(aliases) != set(EXPECTED_ALIASES):
        raise ValueError("Pre-registered model aliases do not match train/validation counts")
    return tuple(ranked)


def _as_int(array: pa.Array) -> np.ndarray:
    return pc.fill_null(array, 0).to_numpy(zero_copy_only=False).astype(np.int64, copy=False)


def _eq(array: pa.Array, value: str) -> np.ndarray:
    return pc.fill_null(pc.equal(array, value), False).to_numpy(zero_copy_only=False).astype(bool, copy=False)


def ingest_before_holdout(compact_path: Path, selected_names: tuple[str, ...]) -> tuple[np.ndarray, dict[str, np.ndarray], list[int], dict[str, dict[str, np.ndarray]], dict]:
    """Read sorted trace only through T_END, storing no raw name or request ID."""
    pf = pq.ParquetFile(compact_path)
    needed = {"created_utc_us", "finished_utc_us", "elapsed_us", "model", "status"}
    if not needed.issubset(pf.schema_arrow.names):
        raise ValueError("SwissAI compact trace lacks required columns")
    names = {hashlib.sha256(n.encode()).hexdigest()[:10]: n for n in selected_names}
    if set(names) != set(ALIASES):
        raise ValueError("Model alias collision or selection mismatch")
    global_bins = np.zeros(N_BINS, dtype=np.int64)
    model_bins = {alias: np.zeros(N_BINS, dtype=np.int64) for alias in ALIASES}
    feedback_parts: dict[str, dict[str, list[np.ndarray]]] = {
        alias: {"available_us": [], "duration_us": [], "is_error": []} for alias in ALIASES
    }
    gap_ends: list[int] = []
    previous: int | None = None
    read_rows = 0
    for batch in pf.iter_batches(columns=["created_utc_us", "finished_utc_us", "elapsed_us", "model", "status"], batch_size=500_000):
        created_full = _as_int(batch.column("created_utc_us"))
        if not len(created_full):
            continue
        if np.any(np.diff(created_full) < 0) or (previous is not None and created_full[0] < previous):
            raise ValueError("Compact requests are not sorted by creation time")
        stop = int(np.searchsorted(created_full, T_END, side="left"))
        if stop == 0:
            break
        created = created_full[:stop]
        if previous is not None and created[0] - previous >= GAP_US:
            gap_ends.append(int(created[0]))
        ix = np.flatnonzero(np.diff(created) >= GAP_US)
        gap_ends.extend(int(created[k + 1]) for k in ix)
        previous = int(created[-1])
        read_rows += len(created)
        in_grid = created >= T0
        bins = ((created[in_grid] - T0) // BIN_US).astype(np.int64)
        np.add.at(global_bins, bins, 1)

        model_col = batch.column("model").slice(0, stop)
        finished_col = batch.column("finished_utc_us").slice(0, stop)
        finished_present = finished_col.is_valid().to_numpy(zero_copy_only=False)
        finished = _as_int(finished_col)
        elapsed = _as_int(batch.column("elapsed_us").slice(0, stop))
        if np.any((elapsed > 0) & finished_present & (finished <= created)):
            raise ValueError("Positive compact elapsed time disagrees with the finish timestamp")
        error = _eq(batch.column("status").slice(0, stop), "ERROR")
        for alias in ALIASES:
            selected = _eq(model_col, names[alias])
            if not selected.any():
                continue
            own_bins = ((created[selected & in_grid] - T0) // BIN_US).astype(np.int64)
            np.add.at(model_bins[alias], own_bins, 1)
            valid = selected & finished_present & (elapsed > 0)
            available = finished[valid] + FEEDBACK_DELAY_US
            relevant = (available >= T0 - LOOKBACK_US) & (available < T_END)
            feedback_parts[alias]["available_us"].append(available[relevant])
            feedback_parts[alias]["duration_us"].append(elapsed[valid][relevant])
            feedback_parts[alias]["is_error"].append(error[valid][relevant].astype(np.int8))
        if stop < len(created_full):
            break

    feedback: dict[str, dict[str, np.ndarray]] = {}
    for alias in ALIASES:
        joined = {key: np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
                  for key, parts in feedback_parts[alias].items()}
        order = np.argsort(joined["available_us"], kind="stable")
        feedback[alias] = {key: values[order] for key, values in joined.items()}
    ingest = {"source_rows_before_2025_08_16": read_rows,
              "global_arrivals_in_grid": int(global_bins.sum()),
              "selected_arrivals_in_grid": {alias: int(model_bins[alias].sum()) for alias in ALIASES},
              "known_6h_gap_ends_before_holdout": len(gap_ends),
              "feedback_events_before_holdout": {alias: len(feedback[alias]["available_us"]) for alias in ALIASES}}
    return global_bins, model_bins, gap_ends, feedback, ingest


def causal_restart_mask(gap_ends: list[int], n_bins: int = N_BINS) -> np.ndarray:
    """The gap end b is known only after the arrival at b; never mask t<b."""
    blocked = np.zeros(n_bins, dtype=bool)
    for b in gap_ends:
        left = max(0, int(math.ceil((b - T0) / BIN_US)))
        right = min(n_bins, int(math.ceil((b + LOOKBACK_US - T0) / BIN_US)))
        if left < right:
            blocked[left:right] = True
    return blocked


def split_decisions(global_bins: np.ndarray, model_bins: dict[str, np.ndarray], gap_ends: list[int]) -> tuple[dict[str, dict[str, np.ndarray]], dict]:
    gc = np.r_[0, np.cumsum(global_bins, dtype=np.int64)]
    restarted = causal_restart_mask(gap_ends, len(global_bins))
    decisions: dict[str, dict[str, list[np.ndarray]]] = {
        alias: {"idx": [], "split": [], "y": []} for alias in ALIASES
    }
    audit: dict = {"by_split": {}, "by_model_split": {}}
    for split_code, (start, end) in enumerate(SPLIT_BOUNDS):
        first = max((start - T0) // BIN_US, 24)
        last_exclusive = (end - T0) // BIN_US - 1
        idx = np.arange(first, last_exclusive, dtype=np.int64)
        recent_global = gc[idx] - gc[idx - 2] > 0
        common = recent_global & ~restarted[idx]
        name = SPLITS[split_code]
        audit["by_split"][name] = {
            "grid_decisions": int(len(idx)), "causal_global_eligible": int(common.sum()),
            "no_global_arrival_previous_10m": int((~recent_global).sum()),
            "within_120m_after_known_6h_gap": int(restarted[idx].sum()),
            "raw_arrivals": int(global_bins[(start - T0)//BIN_US:(end - T0)//BIN_US].sum()),
        }
        for alias in ALIASES:
            mc = np.r_[0, np.cumsum(model_bins[alias], dtype=np.int64)]
            active = mc[idx] - mc[idx - 12] > 0
            keep = common & active
            ids = idx[keep]
            y = mc[ids + 2] - mc[ids]
            decisions[alias]["idx"].append(ids)
            decisions[alias]["split"].append(np.full(len(ids), split_code, dtype=np.int8))
            decisions[alias]["y"].append(y)
            audit["by_model_split"][f"{alias}:{name}"] = {
                "active_decisions": int(active.sum()), "eligible_decisions": int(keep.sum()),
                "positive_targets": int(np.count_nonzero(y)), "zero_targets": int(np.count_nonzero(y == 0)),
                "model_arrivals": int(model_bins[alias][(start - T0)//BIN_US:(end - T0)//BIN_US].sum()),
            }
    merged = {alias: {key: np.concatenate(chunks) for key, chunks in decisions[alias].items()}
              for alias in ALIASES}
    return merged, audit


@njit(cache=True)
def _rolling_nearest_rank_q90(starts: np.ndarray, ends: np.ndarray, ranks: np.ndarray, unique_values: np.ndarray) -> np.ndarray:
    """Exact sliding nearest-rank quantile using a Fenwick frequency tree."""
    n_unique = len(unique_values)
    tree = np.zeros(n_unique + 1, dtype=np.int64)
    answer = np.zeros(len(starts), dtype=np.int64)
    left = 0
    right = 0
    for i in range(len(starts)):
        while right < ends[i]:
            x = ranks[right] + 1
            while x <= n_unique:
                tree[x] += 1
                x += x & -x
            right += 1
        while left < starts[i]:
            x = ranks[left] + 1
            while x <= n_unique:
                tree[x] -= 1
                x += x & -x
            left += 1
        count = ends[i] - starts[i]
        if count == 0:
            continue
        desired = (9 * count + 9) // 10  # ceil(0.9*count), 1-based rank
        step = 1
        while step * 2 <= n_unique:
            step *= 2
        pos = 0
        while step > 0:
            nxt = pos + step
            if nxt <= n_unique and tree[nxt] < desired:
                desired -= tree[nxt]
                pos = nxt
            step //= 2
        answer[i] = unique_values[pos]
    return answer


def feedback_features(decision_us: np.ndarray, feedback: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    available = feedback["available_us"]
    n = len(decision_us)
    if not len(available):
        return {"c30": np.zeros(n), "c120": np.zeros(n), "duration_p90_logsec": np.zeros(n),
                "error_rate120": np.zeros(n), "completion_empty120": np.ones(n)}
    end = np.searchsorted(available, decision_us, side="left")  # available_at < t
    start30 = np.searchsorted(available, decision_us - 30 * MIN_US, side="left")
    start120 = np.searchsorted(available, decision_us - LOOKBACK_US, side="left")
    count120 = end - start120
    error_prefix = np.r_[0, np.cumsum(feedback["is_error"], dtype=np.int64)]
    error_count = error_prefix[end] - error_prefix[start120]
    duration_capped = np.minimum(feedback["duration_us"], 3600 * US).astype(np.int64)
    unique, ranks = np.unique(duration_capped, return_inverse=True)
    q_us = _rolling_nearest_rank_q90(start120.astype(np.int64), end.astype(np.int64),
                                     ranks.astype(np.int32), unique.astype(np.int64))
    error_rate = np.divide(error_count, count120, out=np.zeros(n, dtype=np.float64), where=count120 > 0)
    return {"c30": (end - start30).astype(np.float64), "c120": count120.astype(np.float64),
            "duration_p90_logsec": np.log1p(q_us / US), "error_rate120": error_rate,
            "completion_empty120": (count120 == 0).astype(np.float64)}


def rows_for_model(alias: str, model_code: int, chosen: dict[str, np.ndarray],
                   global_bins: np.ndarray, own_bins: np.ndarray, feedback: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    idx = chosen["idx"]
    t = T0 + idx * BIN_US
    own = np.r_[0, np.cumsum(own_bins, dtype=np.int64)]
    all_ = np.r_[0, np.cumsum(global_bins, dtype=np.int64)]
    data: dict[str, np.ndarray] = {
        "t_us": t, "model_alias": np.full(len(idx), alias), "model_code": np.full(len(idx), model_code),
        "split": chosen["split"], "y": chosen["y"],
    }
    for minutes in (5, 10, 30, 60, 120):
        bins = minutes // 5
        data[f"m{minutes}"] = own[idx] - own[idx - bins]
        data[f"g{minutes}"] = all_[idx] - all_[idx - bins]
    minute_of_day = (t // MIN_US) % 1440
    day_of_week = ((t // DAY_US) + 3) % 7  # 1970-01-01 was Thursday
    minute_of_week = day_of_week * 1440 + minute_of_day
    data["sin_day"] = np.sin(2 * np.pi * minute_of_day / 1440)
    data["cos_day"] = np.cos(2 * np.pi * minute_of_day / 1440)
    data["sin_week"] = np.sin(2 * np.pi * minute_of_week / (7 * 1440))
    data["cos_week"] = np.cos(2 * np.pi * minute_of_week / (7 * 1440))
    data.update(feedback_features(t, feedback))
    data["persistence"] = data["m10"]
    recent = np.stack([own[idx - 2 * k] - own[idx - 2 * (k + 1)] for k in range(12)], axis=1)
    data["recent_p90"] = np.sort(recent, axis=1)[:, 10]
    train_y = data["y"][data["split"] == 0]
    if not len(train_y):
        raise ValueError("Selected model has no train decisions")
    fixed = np.sort(train_y)[math.ceil(Q * len(train_y)) - 1]
    data["fixed_p90"] = np.full(len(idx), fixed, dtype=np.int64)
    return data


def quality_gate(audit: dict) -> dict:
    reasons = []
    for key, expected in EXPECTED_DECISION_AUDIT.items():
        actual = audit["by_model_split"][key]
        observed = tuple(actual[field] for field in
                         ("active_decisions", "eligible_decisions", "positive_targets", "zero_targets"))
        if observed != expected:
            reasons.append(f"{key}: frozen pre-score decision counts changed: {observed} != {expected}")
    for split in SPLITS:
        record = audit["by_split"][split]
        if record["causal_global_eligible"] / record["grid_decisions"] < 0.20:
            reasons.append(f"{split}: fewer than 20% of global grid decisions eligible")
        for alias in ALIASES:
            group = audit["by_model_split"][f"{alias}:{split}"]
            if group["eligible_decisions"] < 500:
                reasons.append(f"{alias}:{split}: fewer than 500 eligible decisions")
            if min(group["positive_targets"], group["zero_targets"]) < 100:
                reasons.append(f"{alias}:{split}: fewer than 100 positive or zero targets")
            if group["eligible_decisions"] / max(group["active_decisions"], 1) < 0.50:
                reasons.append(f"{alias}:{split}: fewer than 50% of active decisions retained")
    return {"passes": not reasons, "reasons": reasons}


def write_dry_run() -> dict:
    if not HAS_NUMBA:
        raise RuntimeError("Full SwissAI dry-run requires numba; use /usr/bin/python in this workspace")
    hashes = assert_sources()
    audit = json.loads((SOURCE / "audit.json").read_text())
    selected_names = select_models(audit)
    global_bins, model_bins, gap_ends, feedback, ingest = ingest_before_holdout(
        SOURCE / "requests_sorted.parquet", selected_names)
    chosen, data_audit = split_decisions(global_bins, model_bins, gap_ends)
    gate = quality_gate(data_audit)
    data = [rows_for_model(alias, code, chosen[alias], global_bins, model_bins[alias], feedback[alias])
            for code, alias in enumerate(ALIASES)]
    columns = list(data[0])
    table = pa.table({key: np.concatenate([item[key] for item in data]) for key in columns})
    if int(pc.max(table["t_us"]).as_py()) >= T_END:
        raise ValueError("Holdout-period rows reached the demand dataset")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    dataset_path = OUTPUT / "decision_rows.parquet"
    pq.write_table(table, dataset_path, compression="zstd")
    result = {"status": "dry_run", "hashes": hashes, "script_sha256": sha256(Path(__file__)),
              "dataset_sha256": sha256(dataset_path), "dataset_rows": table.num_rows,
              "selected_aliases": list(ALIASES), "ingest": ingest, "decision_audit": data_audit,
              "quality_gate": gate, "dataset_columns": table.column_names,
              "max_decision_t_us": int(pc.max(table["t_us"]).as_py()),
              "reserved_holdout_begins_utc": TEST_END}
    (OUTPUT / "dry_run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def pinball(y: np.ndarray, q: np.ndarray) -> np.ndarray:
    err = y - q
    return np.where(err >= 0, Q * err, (1 - Q) * -err)


def _metric(y: np.ndarray, pred: np.ndarray) -> dict:
    return {"rows": len(y), "mean_pinball": float(np.mean(pinball(y, pred))),
            "coverage": float(np.mean(y <= pred)), "mean_prediction": float(np.mean(pred)),
            "mean_actual": float(np.mean(y))}


def summarize(y: np.ndarray, pred: np.ndarray, aliases: np.ndarray, mask: np.ndarray) -> dict:
    by_model = {}
    for alias in ALIASES:
        ix = mask & (aliases == alias)
        by_model[alias] = _metric(y[ix], pred[ix]) if ix.any() else None
    ix = mask
    pooled = _metric(y[ix], pred[ix]) if ix.any() else None
    equal = float(np.mean([by_model[alias]["mean_pinball"] for alias in ALIASES])) if all(by_model.values()) else None
    return {"equal_model_mean_pinball": equal, "pooled": pooled, "by_model": by_model}


def score() -> dict:
    hashes = assert_sources()
    dry_path = OUTPUT / "dry_run.json"
    data_path = OUTPUT / "decision_rows.parquet"
    dry = json.loads(dry_path.read_text())
    if dry["hashes"] != hashes or dry["script_sha256"] != sha256(Path(__file__)):
        raise ValueError("Dry-run source, protocol, or script hash changed")
    if dry["dataset_sha256"] != sha256(data_path) or not dry["quality_gate"]["passes"]:
        raise ValueError("Dry-run dataset mismatch or quality gate failed")
    table = pq.read_table(data_path)
    if table.num_rows != dry["dataset_rows"]:
        raise ValueError("Decision-row count changed")
    vals = {name: table[name].to_numpy(zero_copy_only=False) for name in table.column_names}
    if np.max(vals["t_us"]) >= T_END:
        raise ValueError("Reserved later holdout entered score data")
    aliases = vals["model_alias"]
    split = vals["split"]
    y = vals["y"].astype(np.float64)
    train = split == 0
    validation = split == 1
    test = split == 2
    train_n = {a: int(np.count_nonzero(train & (aliases == a))) for a in ALIASES}
    weight = np.zeros(len(y), dtype=np.float64)
    for alias in ALIASES:
        weight[train & (aliases == alias)] = int(train.sum()) / (2 * train_n[alias])
    candidates = {"arrival_only": ARRIVAL_FEATURES, "with_completion": ARRIVAL_FEATURES + FEEDBACK_FEATURES}
    models = {}
    predictions = {}
    timings = {}
    for name, features in candidates.items():
        x = np.column_stack([vals[feature] for feature in features]).astype(np.float64)
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=Q, max_iter=200, learning_rate=0.05,
            max_leaf_nodes=15, min_samples_leaf=100, l2_regularization=1.0,
            early_stopping=False, random_state=42)
        t0 = time.perf_counter()
        model.fit(x[train], y[train], sample_weight=weight[train])
        fit_seconds = time.perf_counter() - t0
        t1 = time.perf_counter()
        pred = np.maximum(0.0, model.predict(x))
        predict_seconds = time.perf_counter() - t1
        t_test = time.perf_counter()
        _ = model.predict(x[test])
        predict_test_seconds = time.perf_counter() - t_test
        if not np.isfinite(pred).all():
            raise ValueError(f"Non-finite predictions from {name}")
        models[name] = model
        predictions[name] = pred
        timings[name] = {"fit_seconds": fit_seconds, "predict_all_seconds": predict_seconds,
                         "predict_test_seconds": predict_test_seconds,
                         "predict_test_ms_per_decision": 1000 * predict_test_seconds / int(test.sum()),
                         "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}

    for baseline in SIMPLE_METHODS:
        predictions[baseline] = vals[baseline].astype(np.float64)
    val_summary = {name: summarize(y, pred, aliases, validation) for name, pred in predictions.items()}
    best_simple = min(SIMPLE_METHODS, key=lambda name: (val_summary[name]["equal_model_mean_pinball"],
                                                        SIMPLE_METHODS.index(name)))
    test_summary = {name: summarize(y, pred, aliases, test) for name, pred in predictions.items()}
    weekly = []
    for week in range(5):
        start = T_VAL + week * 7 * DAY_US
        end = min(T_END, start + 7 * DAY_US)
        mask = test & (vals["t_us"] >= start) & (vals["t_us"] < end)
        record = {"week": week + 1, "start_us": start, "end_us": end, "complete_7d": end - start == 7 * DAY_US,
                  "rows": int(mask.sum()),
                  "arrival_only": summarize(y, predictions["arrival_only"], aliases, mask),
                  "with_completion": summarize(y, predictions["with_completion"], aliases, mask)}
        weekly.append(record)
    aug = test_summary["with_completion"]
    arrival = test_summary["arrival_only"]
    simple = test_summary[best_simple]
    base_loss = arrival["equal_model_mean_pinball"]
    simple_loss = simple["equal_model_mean_pinball"]
    aug_loss = aug["equal_model_mean_pinball"]
    valid_weeks = [w for w in weekly if w["complete_7d"] and w["rows"] >= 100
                   and w["arrival_only"]["equal_model_mean_pinball"] is not None]
    weekly_wins = sum(w["with_completion"]["equal_model_mean_pinball"] <
                      w["arrival_only"]["equal_model_mean_pinball"] for w in valid_weeks)
    success = (len(valid_weeks) == 4 and weekly_wins >= 3 and
               aug_loss <= 0.95 * base_loss and aug_loss <= 0.95 * simple_loss and
               all(aug["by_model"][a]["mean_pinball"] <= arrival["by_model"][a]["mean_pinball"]
                   and aug["by_model"][a]["coverage"] >= 0.85 for a in ALIASES))
    output = {"status": "scored", "scope": "exploratory_demand_signal_only", "hashes": hashes,
              "script_sha256": sha256(Path(__file__)), "dataset_sha256": dry["dataset_sha256"],
              "source_rows": len(y), "train_rows": int(train.sum()), "validation_rows": int(validation.sum()),
              "test_rows": int(test.sum()), "selected_aliases": list(ALIASES),
              "features": {name: features for name, features in candidates.items()},
              "validation": val_summary, "selected_simple_baseline": best_simple,
              "test": test_summary, "weekly_test": weekly,
              "success": {"passes": bool(success), "augmented_vs_arrival_relative_gain":
                          float((base_loss - aug_loss) / base_loss), "augmented_vs_simple_relative_gain":
                          float((simple_loss - aug_loss) / simple_loss), "qualifying_full_weeks": len(valid_weeks),
                          "qualifying_full_week_wins": int(weekly_wins)},
              "timing": timings, "sklearn_version": sklearn.__version__,
              "reserved_holdout_begins_utc": TEST_END}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, model in models.items():
        joblib.dump(model, OUTPUT / f"{name}.joblib")
    pred_table = pa.table({"t_us": vals["t_us"][test], "model_alias": aliases[test], "y": vals["y"][test],
                           **{name: pred[test] for name, pred in predictions.items()}})
    pred_path = OUTPUT / "test_predictions.parquet"
    pq.write_table(pred_table, pred_path, compression="zstd")
    output["prediction_sha256"] = sha256(pred_path)
    output["model_sha256"] = {name: sha256(OUTPUT / f"{name}.joblib") for name in models}
    (OUTPUT / "metrics.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--score", action="store_true")
    args = parser.parse_args()
    result = write_dry_run() if args.dry_run else score()
    print(json.dumps({"status": result["status"], "quality_gate": result.get("quality_gate"),
                      "dataset_rows": result.get("dataset_rows"), "success": result.get("success")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
