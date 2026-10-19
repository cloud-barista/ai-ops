#!/usr/bin/env python3
"""Frozen chronological prediction of the SwissAI trace's logged ERROR status.

The target is a request log status, not an infrastructure action or SLO event.
Run `prepare` first. `test` verifies the source, protocol, script, and model hashes
before it loads held-out outcome columns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/swissai_serving_trace/requests_sorted.parquet"
PROTOCOL = RESEARCH / "reports/swissai_error_status_protocol.md"
DEFAULT_OUTPUT = RESEARCH / "artifacts/swissai_error_status_v1"
SOURCE_SHA256 = "d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b"
HOUR_US = 3_600_000_000
DAY_US = 24 * HOUR_US
FEEDBACK_LAG_US = HOUR_US
HISTORY_START = "2025-05-15"
TRAIN_START = "2025-05-16"
VAL_START = "2025-08-16"
TEST_START = "2025-09-16"
TEST_END = "2025-10-24"
RANDOM_SEED = 42
SAMPLE_PER_TRAIN_DAY = 5_000
HGB_PARAMS = {
    "max_iter": 100,
    "max_leaf_nodes": 15,
    "max_depth": 4,
    "learning_rate": 0.05,
    "min_samples_leaf": 200,
    "l2_regularization": 10.0,
    "early_stopping": False,
    "random_state": RANDOM_SEED,
}
BASELINES = ("static_global", "static_model", "rolling_global", "rolling_model")
FEATURE_NAMES = (
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos",
    "log_global_done_1h", "global_error_rate_1h",
    "log_global_done_24h", "global_error_rate_24h",
    "log_model_done_1h", "model_error_rate_1h",
    "log_model_done_24h", "model_error_rate_24h",
    "log_global_done_all", "global_error_rate_all",
    "log_model_done_all", "model_error_rate_all",
    "log_global_arrivals_1h", "log_model_arrivals_1h",
)


def utc_us(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1_000_000)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def verify_source() -> None:
    actual = sha256(SOURCE)
    if actual != SOURCE_SHA256:
        raise ValueError(f"SwissAI source hash changed: {actual}")


@dataclass
class Rows:
    created: np.ndarray
    finished: np.ndarray
    finished_valid: np.ndarray
    finished_naive: np.ndarray
    model: np.ndarray
    model_code: np.ndarray
    status: np.ndarray
    source_row: np.ndarray
    n_models: int


def read_rows(end_us: int) -> Rows:
    """Read ordered source rows since history start, stopping at the end cutoff."""
    parquet = pq.ParquetFile(SOURCE)
    pieces: list[pa.Table] = []
    begin = utc_us(HISTORY_START)
    cols = ["created_utc_us", "finished_utc_us", "finished_naive", "model", "status", "source_row"]
    for group in range(parquet.num_row_groups):
        stats = parquet.metadata.row_group(group).column(0).statistics
        if stats.max < begin:
            continue
        if stats.min >= end_us:
            break
        table = parquet.read_row_group(group, columns=cols)
        times = table["created_utc_us"].to_numpy(zero_copy_only=False)
        keep = (times >= begin) & (times < end_us)
        if np.any(keep):
            pieces.append(table.filter(pa.array(keep)))
    if not pieces:
        raise ValueError("No rows in selected source time range")
    table = pa.concat_tables(pieces).combine_chunks()
    created = table["created_utc_us"].to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    finished_column = table["finished_utc_us"]
    finished_valid = np.asarray(finished_column.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    finished = np.zeros(len(created), dtype=np.int64)
    finished[finished_valid] = finished_column.fill_null(0).to_numpy(zero_copy_only=False)[finished_valid]
    finished_naive = table["finished_naive"].fill_null(False).to_numpy(zero_copy_only=False).astype(bool)
    model = table["model"].to_pandas().to_numpy()
    model_code, model_names = pd.factorize(model, sort=False)
    status = table["status"].to_pandas().to_numpy()
    source_row = table["source_row"].to_numpy(zero_copy_only=False).astype(np.int32, copy=False)
    if np.any(np.diff(created) < 0):
        raise ValueError("Source order is not chronological")
    if np.any(model_code < 0):
        raise ValueError("Missing model IDs must be represented as literal values")
    if not np.all(np.isin(status, ("DEFAULT", "ERROR"))):
        raise ValueError("Unexpected status values in source")
    return Rows(created, finished, finished_valid, finished_naive, model,
                model_code.astype(np.int32), status == "ERROR", source_row, len(model_names))


@dataclass
class History:
    origin_hour: int
    completed: np.ndarray
    errors: np.ndarray
    arrivals: np.ndarray
    global_completed: np.ndarray
    global_errors: np.ndarray
    global_arrivals: np.ndarray
    last_model_feedback_hour: np.ndarray


def history(rows: Rows, *, exclude_naive: bool = False) -> History:
    """Cumulative hour arrays; history at hour h never contains hour h outcomes."""
    origin_hour = utc_us(HISTORY_START) // HOUR_US
    h = rows.created // HOUR_US - origin_hour
    valid = rows.finished_valid & (~rows.finished_naive if exclude_naive else True)
    available_hour = np.zeros(len(rows.created), dtype=np.int64)
    available_hour[valid] = (rows.finished[valid] + FEEDBACK_LAG_US) // HOUR_US - origin_hour
    n_hours = int(max(np.max(h), np.max(available_hour[valid], initial=0)) + 2)
    n_models = rows.n_models
    active = valid & (available_hour >= 0) & (available_hour < n_hours)
    event_flat = available_hour[active] * n_models + rows.model_code[active]
    size = n_hours * n_models
    event_counts = np.bincount(event_flat, minlength=size).reshape(n_hours, n_models)
    event_errors = np.bincount(event_flat[rows.status[active]], minlength=size).reshape(n_hours, n_models)
    arrival_flat = h * n_models + rows.model_code
    arrival_counts = np.bincount(arrival_flat, minlength=size).reshape(n_hours, n_models)
    zeros = np.zeros((1, n_models), dtype=np.int64)
    completed = np.vstack((zeros, np.cumsum(event_counts, axis=0, dtype=np.int64)))
    errors = np.vstack((zeros, np.cumsum(event_errors, axis=0, dtype=np.int64)))
    arrivals = np.vstack((zeros, np.cumsum(arrival_counts, axis=0, dtype=np.int64)))
    hour_grid = np.arange(n_hours, dtype=np.int64)[:, None]
    last_model_feedback_hour = np.maximum.accumulate(
        np.where(event_counts > 0, hour_grid, -1), axis=0)
    return History(origin_hour, completed, errors, arrivals,
                   completed.sum(axis=1), errors.sum(axis=1), arrivals.sum(axis=1),
                   last_model_feedback_hour)


def past(prefix: np.ndarray, h: np.ndarray, window: int, model: np.ndarray | None = None) -> np.ndarray:
    low = np.maximum(h - window, 0)
    if model is None:
        return prefix[h] - prefix[low]
    return prefix[h, model] - prefix[low, model]


def all_past(prefix: np.ndarray, h: np.ndarray, model: np.ndarray | None = None) -> np.ndarray:
    if model is None:
        return prefix[h]
    return prefix[h, model]


def smooth_rate(errors: np.ndarray, counts: np.ndarray) -> np.ndarray:
    return (errors + 1.0) / (counts + 2.0)


def feature_matrix(rows: Rows, hist: History, idx: np.ndarray) -> np.ndarray:
    h = (rows.created[idx] // HOUR_US - hist.origin_hour).astype(np.int64)
    m = rows.model_code[idx]
    actual_hour = rows.created[idx] // HOUR_US
    hour = actual_hour % 24
    weekday = (actual_hour // 24 + 3) % 7
    gc1 = past(hist.global_completed, h, 1)
    ge1 = past(hist.global_errors, h, 1)
    gc24 = past(hist.global_completed, h, 24)
    ge24 = past(hist.global_errors, h, 24)
    mc1 = past(hist.completed, h, 1, m)
    me1 = past(hist.errors, h, 1, m)
    mc24 = past(hist.completed, h, 24, m)
    me24 = past(hist.errors, h, 24, m)
    gca = all_past(hist.global_completed, h)
    gea = all_past(hist.global_errors, h)
    mca = all_past(hist.completed, h, m)
    mea = all_past(hist.errors, h, m)
    ga1 = past(hist.global_arrivals, h, 1)
    ma1 = past(hist.arrivals, h, 1, m)
    x = np.column_stack((
        np.sin(2*np.pi*hour/24), np.cos(2*np.pi*hour/24),
        np.sin(2*np.pi*weekday/7), np.cos(2*np.pi*weekday/7),
        np.log1p(gc1), smooth_rate(ge1, gc1),
        np.log1p(gc24), smooth_rate(ge24, gc24),
        np.log1p(mc1), smooth_rate(me1, mc1),
        np.log1p(mc24), smooth_rate(me24, mc24),
        np.log1p(gca), smooth_rate(gea, gca),
        np.log1p(mca), smooth_rate(mea, mca),
        np.log1p(ga1), np.log1p(ma1),
    )).astype(np.float32)
    if x.shape[1] != len(FEATURE_NAMES) or not np.all(np.isfinite(x)):
        raise ValueError("Feature matrix has wrong shape or nonfinite values")
    return x


def train_static_rates(rows: Rows, *, exclude_naive: bool = False) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    training = (rows.created >= utc_us(TRAIN_START)) & (rows.created < utc_us(VAL_START))
    available = rows.finished_valid & (rows.finished + FEEDBACK_LAG_US < utc_us(VAL_START))
    if exclude_naive:
        available &= ~rows.finished_naive
    keep = training & available
    counts = np.bincount(rows.model_code[keep], minlength=rows.n_models)
    errors = np.bincount(rows.model_code[keep & rows.status], minlength=rows.n_models)
    p_global = (int(errors.sum()) + 1.0) / (int(counts.sum()) + 2.0)
    p_model = (errors + 100.0*p_global) / (counts + 100.0)
    return p_global, p_model, counts, errors


def baseline_predictions(rows: Rows, hist: History, idx: np.ndarray,
                         p_global: float, p_model: np.ndarray) -> dict[str, np.ndarray]:
    h = (rows.created[idx] // HOUR_US - hist.origin_hour).astype(np.int64)
    m = rows.model_code[idx]
    gc24 = past(hist.global_completed, h, 24)
    ge24 = past(hist.global_errors, h, 24)
    mc24 = past(hist.completed, h, 24, m)
    me24 = past(hist.errors, h, 24, m)
    global_recent = (ge24 + 1_000.0*p_global) / (gc24 + 1_000.0)
    model_recent = (me24 + 100.0*global_recent) / (mc24 + 100.0)
    return {
        "static_global": np.full(len(idx), p_global, dtype=np.float64),
        "static_model": p_model[m],
        "rolling_global": global_recent,
        "rolling_model": model_recent,
    }


def support_acceptance(rows: Rows, hist: History, idx: np.ndarray,
                       train_counts: np.ndarray) -> np.ndarray:
    """Fixed predeclared support/freshness gate for the learned probability."""
    h = (rows.created[idx] // HOUR_US - hist.origin_hour).astype(np.int64)
    m = rows.model_code[idx]
    recent_model_count = past(hist.completed, h, 24, m)
    last = hist.last_model_feedback_hour[np.maximum(h-1, 0), m]
    freshness = np.where((h > 0) & (last >= 0), h-1-last, np.iinfo(np.int64).max)
    return ((recent_model_count >= 100) & (freshness <= 6)
            & ((train_counts[m] >= 100) | (recent_model_count >= 500)))


def daily_sample(rows: Rows) -> np.ndarray:
    chosen = []
    rng = np.random.default_rng(RANDOM_SEED)
    days = rows.created // DAY_US
    available_at_fit = rows.finished_valid & (rows.finished + FEEDBACK_LAG_US < utc_us(VAL_START))
    lo = utc_us(TRAIN_START) // DAY_US
    hi = utc_us(VAL_START) // DAY_US
    for day in range(lo, hi):
        candidates = np.flatnonzero((days == day) & available_at_fit)
        if len(candidates) > SAMPLE_PER_TRAIN_DAY:
            candidates = np.sort(rng.choice(candidates, size=SAMPLE_PER_TRAIN_DAY, replace=False))
        chosen.append(candidates)
    return np.concatenate(chosen)


def score(y: np.ndarray, p: np.ndarray) -> dict:
    yy = y.astype(np.float64)
    pp = np.clip(np.asarray(p, dtype=np.float64), 1e-8, 1-1e-8)
    return {
        "n": int(len(y)), "errors": int(y.sum()), "error_rate": float(yy.mean()),
        "mean_probability": float(pp.mean()),
        "brier": float(np.mean((pp - yy)**2)),
        "log_loss": float(np.mean(-yy*np.log(pp) - (1-yy)*np.log1p(-pp))),
        "average_precision": float(average_precision_score(yy, pp)) if np.any(y) else 0.0,
    }


def scored_methods(y: np.ndarray, predictions: dict[str, np.ndarray]) -> dict:
    return {name: score(y, p) for name, p in predictions.items()}


def status_frequencies(rows: Rows, idx: np.ndarray, train_counts: np.ndarray) -> dict:
    days = rows.created[idx] // DAY_US
    dates = {}
    for day in np.unique(days):
        mask = days == day
        dates[datetime.fromtimestamp(int(day)*86400, timezone.utc).date().isoformat()] = {
            "n": int(mask.sum()), "errors": int(rows.status[idx[mask]].sum())}
    model_counts = np.bincount(rows.model_code[idx], minlength=rows.n_models)
    model_errors = np.bincount(rows.model_code[idx[rows.status[idx]]], minlength=rows.n_models)
    models = [
        {"anonymous_model_code": int(j), "seen_in_train": bool(train_counts[j]),
         "n": int(model_counts[j]), "errors": int(model_errors[j])}
        for j in np.flatnonzero(model_counts)
    ]
    return {"by_date": dates, "by_anonymous_model_code": models}


def source_contract(rows: Rows) -> dict:
    parts = {}
    for name, lo, hi in (("train", TRAIN_START, VAL_START),
                         ("validation", VAL_START, TEST_START),
                         ("test", TEST_START, TEST_END)):
        m = (rows.created >= utc_us(lo)) & (rows.created < utc_us(hi))
        if np.any(m):
            parts[name] = {"rows": int(m.sum()), "missing_finish": int((~rows.finished_valid[m]).sum()),
                           "naive_finish": int(rows.finished_naive[m].sum())}
    return parts


def prepare(output: Path) -> None:
    model_path = output / "model.joblib"
    manifest_path = output / "selection_manifest.json"
    validation_path = output / "validation_metrics.json"
    if any(p.exists() for p in (model_path, manifest_path, validation_path)):
        raise FileExistsError("Refusing to overwrite a frozen selection")
    verify_source()
    rows = read_rows(utc_us(TEST_START))
    hist = history(rows)
    train_idx = daily_sample(rows)
    val_idx = np.flatnonzero((rows.created >= utc_us(VAL_START)) & (rows.created < utc_us(TEST_START)))
    if len(val_idx) < 100_000 or min(int(rows.status[val_idx].sum()), len(val_idx)-int(rows.status[val_idx].sum())) < 1_000:
        raise ValueError("Validation support gate failed")
    x_train = feature_matrix(rows, hist, train_idx)
    x_val = feature_matrix(rows, hist, val_idx)
    model = HistGradientBoostingClassifier(**HGB_PARAMS)
    model.fit(x_train, rows.status[train_idx].astype(np.uint8))
    p_global, p_model, train_counts, train_errors = train_static_rates(rows)
    val_preds = baseline_predictions(rows, hist, val_idx, p_global, p_model)
    val_preds["hgb"] = model.predict_proba(x_val)[:, 1]
    val_scores = scored_methods(rows.status[val_idx], val_preds)
    selected = min(BASELINES, key=lambda name: (val_scores[name]["brier"], name))
    val_accept = support_acceptance(rows, hist, val_idx, train_counts)
    val_preds["support_hybrid"] = np.where(val_accept, val_preds["hgb"], val_preds[selected])
    val_scores["support_hybrid"] = score(rows.status[val_idx], val_preds["support_hybrid"])
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path, compress=3)
    validation = {
        "source_contract": source_contract(rows),
        "train_sample_rows": int(len(train_idx)), "train_sample_errors": int(rows.status[train_idx].sum()),
        "train_static_available_rows": int(train_counts.sum()),
        "train_static_available_errors": int(train_errors.sum()),
        "validation_scores": val_scores, "selected_baseline": selected,
        "support_gate_accepted": int(val_accept.sum()),
        "support_gate_acceptance_rate": float(val_accept.mean()),
        "validation_frequencies": status_frequencies(rows, val_idx, train_counts),
    }
    write_json(validation_path, validation)
    manifest = {
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(model_path),
        "validation_metrics_sha256": sha256(validation_path),
        "selected_baseline": selected,
        "feature_names": list(FEATURE_NAMES),
        "hgb_params": HGB_PARAMS,
        "train_static_available_rows": int(train_counts.sum()),
        "train_static_available_errors": int(train_errors.sum()),
        "time_contract": {"history_start": HISTORY_START, "train_start": TRAIN_START,
                          "validation_start": VAL_START, "test_start": TEST_START,
                          "test_end": TEST_END, "feedback_lag_us": FEEDBACK_LAG_US},
    }
    write_json(manifest_path, manifest)
    print(json.dumps({"selection_manifest_sha256": sha256(manifest_path),
                      "selected_baseline": selected,
                      "validation_brier": {k: v["brier"] for k, v in val_scores.items()}}, indent=2))


def verify_manifest(output: Path) -> tuple[dict, object]:
    manifest_path = output / "selection_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    expected = {
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(output / "model.joblib"),
        "validation_metrics_sha256": sha256(output / "validation_metrics.json"),
        "feature_names": list(FEATURE_NAMES),
        "hgb_params": HGB_PARAMS,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Frozen {key} differs from manifest")
    verify_source()
    return manifest, joblib.load(output / "model.joblib")


def test(output: Path) -> None:
    metrics_path = output / "test_metrics.json"
    prediction_path = output / "test_predictions.parquet"
    if metrics_path.exists() or prediction_path.exists():
        raise FileExistsError("Refusing to overwrite a frozen test")
    manifest, model = verify_manifest(output)
    rows = read_rows(utc_us(TEST_END))
    hist = history(rows)
    test_idx = np.flatnonzero((rows.created >= utc_us(TEST_START)) & (rows.created < utc_us(TEST_END)))
    y = rows.status[test_idx]
    p_global, p_model, train_counts, train_errors = train_static_rates(rows)
    if int(train_counts.sum()) != manifest["train_static_available_rows"] or int(train_errors.sum()) != manifest["train_static_available_errors"]:
        raise ValueError("Training static-rate population changed")
    preds = baseline_predictions(rows, hist, test_idx, p_global, p_model)
    preds["hgb"] = model.predict_proba(feature_matrix(rows, hist, test_idx))[:, 1]
    selected = manifest["selected_baseline"]
    if selected not in BASELINES:
        raise ValueError("Unrecognized frozen baseline")
    accepted = support_acceptance(rows, hist, test_idx, train_counts)
    preds["support_hybrid"] = np.where(accepted, preds["hgb"], preds[selected])
    summary = scored_methods(y, preds)
    full_blocks = []
    begin_day = utc_us(TEST_START) // DAY_US
    day = rows.created[test_idx] // DAY_US
    for block in range(5):
        mask = (day >= begin_day + 7*block) & (day < begin_day + 7*(block+1))
        full_blocks.append({"index": block, "n": int(mask.sum()),
                            "scores": scored_methods(y[mask], {name: p[mask] for name, p in preds.items()})
                            if np.any(mask) else None})
    seen = train_counts[rows.model_code[test_idx]] > 0
    support = {}
    for name, mask in (("seen_in_train", seen), ("new_to_train", ~seen)):
        if np.any(mask):
            support[name] = scored_methods(y[mask], {key: value[mask] for key, value in preds.items()})
    valid = len(test_idx) >= 100_000 and min(int(y.sum()), len(y)-int(y.sum())) >= 1_000 and all(b["n"] >= 1_000 for b in full_blocks)
    relative_gain = (summary[selected]["brier"] - summary["hgb"]["brier"]) / summary[selected]["brier"]
    block_wins = sum(b["scores"] is not None and
                     b["scores"]["hgb"]["brier"] < b["scores"][selected]["brier"]
                     for b in full_blocks)
    support_no_harm = all(v["hgb"]["brier"] <= v[selected]["brier"]
                          for v in support.values() if v["hgb"]["n"] >= 1_000)
    gates = {"quality": bool(valid), "relative_gain_at_least_5pct": bool(relative_gain >= .05),
             "at_least_4_of_5_full_week_wins": bool(block_wins >= 4),
             "seen_new_strata_no_harm": bool(support_no_harm)}
    accepted_scores = (scored_methods(y[accepted], {name: p[accepted] for name, p in preds.items()})
                       if np.any(accepted) else None)
    gate_gain_accepted = ((accepted_scores[selected]["brier"] - accepted_scores["hgb"]["brier"])
                          / accepted_scores[selected]["brier"] if accepted_scores else None)
    gate_gain_vs_hgb = ((summary["hgb"]["brier"] - summary["support_hybrid"]["brier"])
                        / summary["hgb"]["brier"])
    gate_gain_vs_baseline = ((summary[selected]["brier"] - summary["support_hybrid"]["brier"])
                             / summary[selected]["brier"])
    support_gates = {
        "at_least_20pct_and_10000_accepted": bool(accepted.mean() >= .20 and int(accepted.sum()) >= 10_000),
        "accepted_gain_at_least_5pct": bool(gate_gain_accepted is not None and gate_gain_accepted >= .05),
        "hybrid_gain_vs_ungated_at_least_2pct": bool(gate_gain_vs_hgb >= .02),
        "hybrid_gain_vs_baseline_at_least_2pct": bool(gate_gain_vs_baseline >= .02),
    }
    # Same fixed model under a more conservative interpretation of naive timestamps.
    strict_hist = history(rows, exclude_naive=True)
    strict_global, strict_model, _, _ = train_static_rates(rows, exclude_naive=True)
    strict_preds = baseline_predictions(rows, strict_hist, test_idx, strict_global, strict_model)
    strict_preds["hgb"] = model.predict_proba(feature_matrix(rows, strict_hist, test_idx))[:, 1]
    strict_accept = support_acceptance(rows, strict_hist, test_idx, train_counts)
    strict_preds["support_hybrid"] = np.where(strict_accept, strict_preds["hgb"], strict_preds[selected])
    strict_scores = scored_methods(y, strict_preds)
    counts = status_frequencies(rows, test_idx, train_counts)
    output.mkdir(parents=True, exist_ok=True)
    prediction_table = pa.table({
        "source_row": rows.source_row[test_idx], "created_utc_us": rows.created[test_idx],
        "anonymous_model_code": rows.model_code[test_idx], "seen_in_train": seen,
        "logged_error": y, "support_gate_accepted": accepted,
        **{f"p_{name}": pa.array(preds[name]) for name in (*BASELINES, "hgb", "support_hybrid")},
    })
    pq.write_table(prediction_table, prediction_path, compression="zstd")
    metrics = {
        "source_sha256": SOURCE_SHA256,
        "manifest_sha256": sha256(output / "selection_manifest.json"),
        "source_contract": source_contract(rows),
        "selected_baseline": selected,
        "scores": summary, "full_seven_day_blocks": full_blocks,
        "model_support_strata": support,
        "support_gate": {
            "accepted": int(accepted.sum()), "acceptance_rate": float(accepted.mean()),
            "accepted_scores": accepted_scores,
            "accepted_gain_vs_baseline": gate_gain_accepted,
            "hybrid_gain_vs_ungated_hgb": float(gate_gain_vs_hgb),
            "hybrid_gain_vs_baseline": float(gate_gain_vs_baseline),
            "gates": support_gates,
            "exploratory_success": bool(valid and all(support_gates.values())),
        },
        "frequencies": counts,
        "naive_finish_excluded_history_sensitivity": strict_scores,
        "naive_finish_excluded_gate_accepted": int(strict_accept.sum()),
        "naive_finish_history_events": int(np.sum(rows.finished_valid & rows.finished_naive)),
        "relative_brier_gain": float(relative_gain), "full_week_wins": int(block_wins),
        "gates": gates, "exploratory_success": bool(all(gates.values())),
        "test_predictions_sha256": sha256(prediction_path),
    }
    write_json(metrics_path, metrics)
    print(json.dumps({"test_metrics_sha256": sha256(metrics_path),
                      "selected_baseline": selected,
                      "brier": {k: v["brier"] for k, v in summary.items()},
                      "gates": gates}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "test"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare(args.output)
    else:
        test(args.output)


if __name__ == "__main__":
    main()
