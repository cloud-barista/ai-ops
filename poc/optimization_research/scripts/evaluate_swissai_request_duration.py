"""Pre-registered chronological p90 replay of the SwissAI serving trace.

Run only after the full raw-data audit and its quality gate have been reviewed.
This is a request *termination-time* study, including completed error records;
it cannot estimate scale-action effects or successful-response SLO guarantees.
The fixed procedure is described in reports/swissai_external_validation_protocol.md.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import platform
import re
from typing import Any

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from rolling_quantile_buffer import RollingQuantileBuffer


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data/external/swissai_serving_trace"
OUTPUT = ROOT / "artifacts/swissai_request_duration_v1"
PINNED_SOURCE_SHA256 = "79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7"
PINNED_COMPACT_SHA256 = "d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b"
US = 1_000_000
HOUR_US = 3600 * US
DAY_US = 24 * HOUR_US
Q = 0.9
FIT_END_DAY = 30
FIRST_TEST_DAY = 40
BLOCK_DAYS = 10
TRAIN_CAP = 500_000
MIN_TRAIN = 10_000
GLOBAL_WINDOW = 2000
MODEL_WINDOW = 500
MODEL_MINIMUM = 50
MODEL_SHRINKAGE = 200
GUARD_TARGET = 0.92
GUARD_MINIMUM = 100
GUARD_VALIDATION_MINIMUM = 1000
MAX_MODEL_CATEGORIES = 254
FEEDBACK_DELAYS_HOURS = (1, 0, 6)
TIMEZONE_OFFSETS_HOURS = (0, 1, 2)
EXPLORATORY_PROTOCOL = ROOT / "reports/swissai_exploratory_followup_protocol.md"
PREDICTION_CHUNK = 500_000
REQUIRED = (
    "created_utc_us", "finished_utc_us", "elapsed_us", "model", "status",
    "created_naive", "finished_naive", "source_row",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_model_name(name: str) -> str:
    """Keep possible credentials out of metrics and model metadata."""
    if re.search(r"sk-[A-Za-z0-9_-]{16,}", name):
        suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        return f"<REDACTED_MODEL_SHA256_{suffix}>"
    return name


def safe_model_names(names: list[str]) -> list[str]:
    safe = [safe_model_name(name) for name in names]
    if len(set(safe)) != len(safe):
        raise ValueError("Safe model aliases collide")
    return safe


def _arrow_int(column: pa.ChunkedArray) -> tuple[np.ndarray, np.ndarray]:
    array = column.combine_chunks()
    present = np.asarray(array.is_valid().to_numpy(zero_copy_only=False), bool)
    values = pc.fill_null(array, 0).to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    return values, present


def _arrow_category(column: pa.ChunkedArray) -> tuple[np.ndarray, list[str]]:
    array = pc.dictionary_encode(pc.fill_null(column.combine_chunks(), "<MISSING>"))
    values = array.indices.to_numpy(zero_copy_only=False).astype(np.int32, copy=False)
    names = [str(value).replace("<missing>", "<MISSING>") for value in array.dictionary.to_pylist()]
    if len(set(names)) != len(names):
        raise ValueError("Case-normalized categories collide")
    return values, names


@dataclass
class Trace:
    created_us: np.ndarray
    finished_us: np.ndarray
    finished_present: np.ndarray
    model_code: np.ndarray
    model_names: list[str]
    status_code: np.ndarray
    status_names: list[str]
    created_naive: np.ndarray
    finished_naive: np.ndarray
    source_row: np.ndarray
    audit: dict[str, Any]
    compact_sha256: str
    audit_sha256: str


@dataclass
class Prepared:
    created_us: np.ndarray
    finished_us: np.ndarray
    finished_present: np.ndarray
    positive: np.ndarray
    duration_seconds: np.ndarray
    model_code: np.ndarray
    status_code: np.ndarray
    created_naive: np.ndarray
    finished_naive: np.ndarray
    source_row: np.ndarray
    day0_us: int
    day: np.ndarray
    complete_blocks: list[int]
    full_test_end_day: int


def load_trace(source_dir: Path = SOURCE_DIR) -> Trace:
    audit_path = source_dir / "audit.json"
    compact_path = source_dir / "requests_sorted.parquet"
    audit = json.loads(audit_path.read_text())
    if audit["source"]["sha256"] != PINNED_SOURCE_SHA256:
        raise ValueError("SwissAI source SHA256 differs from the pinned raw audit")
    compact_digest = sha256(compact_path)
    if compact_digest != PINNED_COMPACT_SHA256:
        raise ValueError("Sorted SwissAI compact SHA256 differs from the reviewed audit")
    table = pq.read_table(compact_path, columns=list(REQUIRED))
    if not set(REQUIRED).issubset(table.column_names):
        raise ValueError("The compact table lacks required audit columns")
    if table.num_rows != audit["compact"]["rows"]:
        raise ValueError("Compact row count differs from the raw audit")
    created, created_present = _arrow_int(table["created_utc_us"])
    if not created_present.all():
        raise ValueError("Compact table contains unparsed creation times")
    finished, finished_present = _arrow_int(table["finished_utc_us"])
    model_code, model_names = _arrow_category(table["model"])
    status_code, status_names = _arrow_category(table["status"])
    created_naive = table["created_naive"].to_numpy(zero_copy_only=False).astype(bool)
    finished_naive = table["finished_naive"].to_numpy(zero_copy_only=False).astype(bool)
    source_row = table["source_row"].to_numpy(zero_copy_only=False).astype(np.int32)
    if np.any(np.diff(created) < 0):
        raise ValueError("Compact table is not sorted by request creation")
    ties = np.diff(created) == 0
    if np.any(np.diff(source_row)[ties] <= 0):
        raise ValueError("Compact table does not use source-row tie breaking")
    return Trace(
        created, finished, finished_present, model_code, model_names,
        status_code, status_names, created_naive, finished_naive, source_row,
        audit, compact_digest, sha256(audit_path),
    )


def prepare(trace: Trace, timezone_offset_hours: int = 0) -> Prepared:
    """Reinterpret naive clock fields as UTC+offset, then sort again."""
    if timezone_offset_hours < 0:
        raise ValueError("Only the pre-registered timezone cases 0, +1, +2 are supported")
    offset_us = timezone_offset_hours * HOUR_US
    created = trace.created_us - trace.created_naive.astype(np.int64) * offset_us
    finished = trace.finished_us - trace.finished_naive.astype(np.int64) * offset_us
    if timezone_offset_hours:
        order = np.lexsort((trace.source_row, created))
        created, finished = created[order], finished[order]
    else:
        order = slice(None)
    finished_present = trace.finished_present[order]
    positive = finished_present & (finished > created)
    duration = np.full(len(created), np.nan, dtype=np.float64)
    duration[positive] = (finished[positive] - created[positive]) / US
    # A valid creation timestamp is enough to anchor the calendar, regardless
    # of whether that request has a usable termination-time label.
    day0 = int(created.min() // DAY_US * DAY_US)
    day = ((created - day0) // DAY_US).astype(np.int32)
    # Seeing a request on day 109 does not establish that the rest of day 109
    # was observed. A complete block needs a request at/after its end midnight.
    max_day = int((created.max() - day0) // DAY_US)
    complete_count = max(0, (max_day - FIRST_TEST_DAY) // BLOCK_DAYS)
    blocks = [FIRST_TEST_DAY + i * BLOCK_DAYS for i in range(complete_count)]
    return Prepared(
        created, finished, finished_present, positive, duration,
        trace.model_code[order], trace.status_code[order],
        trace.created_naive[order], trace.finished_naive[order],
        trace.source_row[order], day0, day, blocks,
        FIRST_TEST_DAY + complete_count * BLOCK_DAYS,
    )


def reanchor(p: Prepared, new_day0_us: int) -> Prepared:
    """Keep the absolute arrival order but use a separately registered origin."""
    if new_day0_us % DAY_US:
        raise ValueError("Exploratory start must be UTC midnight")
    day = ((p.created_us - new_day0_us) // DAY_US).astype(np.int32)
    max_day = int(day.max())
    complete_count = max(0, (max_day - FIRST_TEST_DAY) // BLOCK_DAYS)
    blocks = [FIRST_TEST_DAY + i * BLOCK_DAYS for i in range(complete_count)]
    return replace(p, day0_us=new_day0_us, day=day, complete_blocks=blocks,
                   full_test_end_day=FIRST_TEST_DAY + complete_count * BLOCK_DAYS)


def select_exploratory_start(
    created_utc_us: np.ndarray, *, min_fit_requests: int = 500_000,
    min_initialization_requests: int = 100_000,
    min_complete_test_blocks: int = 7,
    original_day0_utc_us: int | None = None,
) -> dict[str, int] | None:
    """Select the first date using arrival counts alone; no outcome is read."""
    original_day0 = (
        int(np.min(created_utc_us) // DAY_US * DAY_US)
        if original_day0_utc_us is None else int(original_day0_utc_us)
    )
    if original_day0 % DAY_US:
        raise ValueError("Original day zero must be UTC midnight")
    day = ((created_utc_us - original_day0) // DAY_US).astype(np.int32)
    counts = np.bincount(day[day >= 0], minlength=int(day.max()) + 1)
    cumulative = np.r_[0, np.cumsum(counts, dtype=np.int64)]
    latest_start = int(day.max()) - (FIRST_TEST_DAY + min_complete_test_blocks * BLOCK_DAYS)
    for candidate in range(max(0, latest_start + 1)):
        fit_requests = int(cumulative[candidate + FIT_END_DAY] - cumulative[candidate])
        validation_requests = int(
            cumulative[candidate + FIRST_TEST_DAY] - cumulative[candidate + FIT_END_DAY]
        )
        if fit_requests >= min_fit_requests and validation_requests >= min_initialization_requests:
            return {
                "original_created_day0_utc_us": original_day0,
                "selected_offset_days": candidate,
                "selected_day0_utc_us": original_day0 + candidate * DAY_US,
                "fit_window_all_created_requests": fit_requests,
                "initialization_window_all_created_requests": validation_requests,
                "complete_test_blocks_available": (
                    int(day.max()) - candidate - FIRST_TEST_DAY
                ) // BLOCK_DAYS,
            }
    return None


def _fractions_by_code(codes: np.ndarray, positive: np.ndarray, names: list[str],
                       minimum: int = 1) -> dict[str, dict[str, float | int]]:
    n = np.bincount(codes, minlength=len(names))
    labelled = np.bincount(codes, weights=positive.astype(np.int8), minlength=len(names))
    return {
        names[i]: {"all": int(n[i]), "positive": int(labelled[i]),
                   "positive_fraction": float(labelled[i] / n[i])}
        for i in range(len(names)) if n[i] >= minimum
    }


def quality_gate(prepared: Prepared, trace: Trace) -> dict[str, Any]:
    """Apply every pre-registered completeness threshold before fitting."""
    p = prepared
    model_names = safe_model_names(trace.model_names)
    n_source = int(trace.audit["counts"]["source_rows"])
    parse_failure_rate = (n_source - len(p.created_us)) / n_source
    overall_fraction = float(np.mean(p.positive))
    blocks: dict[str, dict[str, float | int | None]] = {}
    failing_blocks: list[str] = []
    empty_blocks: list[str] = []
    block_model_requests: dict[str, int] = {}
    for start in p.complete_blocks:
        in_block = (p.day >= start) & (p.day < start + BLOCK_DAYS)
        n = int(in_block.sum())
        positives = int(np.sum(p.positive[in_block]))
        ratio = positives / n if n else None
        blocks[str(start)] = {"all": n, "positive": positives, "positive_fraction": ratio}
        if n == 0:
            empty_blocks.append(str(start))
        requests_by_model = np.bincount(p.model_code[in_block], minlength=len(trace.model_names))
        for code in np.flatnonzero(requests_by_model):
            block_model_requests[f"{start} | {model_names[code]}"] = int(requests_by_model[code])
        if ratio is not None and ratio < 0.95:
            failing_blocks.append(str(start))
    in_test = (p.day >= FIRST_TEST_DAY) & (p.day < p.full_test_end_day)
    model_test = _fractions_by_code(
        p.model_code[in_test], p.positive[in_test], model_names, minimum=1000,
    )
    failing_models = sorted(name for name, record in model_test.items()
                            if record["positive_fraction"] < 0.95)
    # A missing completion, nonpositive duration, or error can have different meanings.
    status_all = _fractions_by_code(p.status_code, p.positive, trace.status_names)
    status_test = _fractions_by_code(
        p.status_code[in_test], p.positive[in_test], trace.status_names,
    )
    mixed_time_formats = {
        "created_naive_finished_explicit": int(np.sum(
            p.created_naive & p.finished_present & ~p.finished_naive)),
        "created_explicit_finished_naive": int(np.sum(
            ~p.created_naive & p.finished_present & p.finished_naive)),
        "both_naive": int(np.sum(p.created_naive & p.finished_present & p.finished_naive)),
        "both_explicit": int(np.sum(~p.created_naive & p.finished_present & ~p.finished_naive)),
    }
    reasons: list[str] = []
    if parse_failure_rate > 0.001:
        reasons.append("created_at parse failures exceed 0.1%")
    if overall_fraction < 0.95:
        reasons.append("whole-trace positive termination-time labels below 95%")
    if failing_blocks:
        reasons.append("complete test blocks with positive labels below 95%")
    if empty_blocks:
        reasons.append("complete test blocks contain no requests")
    if failing_models:
        reasons.append("test models with >=1000 requests and positive labels below 95%")
    training_eligible = int(np.sum(
        p.positive & (p.created_us >= p.day0_us)
        & (p.created_us < p.day0_us + FIT_END_DAY * DAY_US)
        & (p.finished_us + HOUR_US <= p.day0_us + FIT_END_DAY * DAY_US)
    ))
    first_calibration_available = int(np.sum(
        p.positive & (p.day >= FIT_END_DAY) & (p.day < FIRST_TEST_DAY)
        & (p.finished_us + HOUR_US <= p.day0_us + FIRST_TEST_DAY * DAY_US)
    ))
    if training_eligible < MIN_TRAIN:
        reasons.append("fewer than 10000 day0-30 training labels available at day30")
    if first_calibration_available < GUARD_VALIDATION_MINIMUM:
        reasons.append("fewer than 1000 day30-40 calibration labels available at day40")
    return {
        "passes": not reasons,
        "reasons": reasons,
        "source_rows": n_source,
        "parsed_creation_rows": len(p.created_us),
        "created_parse_failure_rate": parse_failure_rate,
        "positive_duration_rows": int(p.positive.sum()),
        "whole_positive_fraction": overall_fraction,
        "missing_finished_rows": int(np.sum(~p.finished_present)),
        "zero_duration_rows": int(np.sum(p.finished_present & (p.finished_us == p.created_us))),
        "negative_duration_rows": int(np.sum(p.finished_present & (p.finished_us < p.created_us))),
        "created_naive_rows": int(np.sum(p.created_naive)),
        "finished_naive_rows": int(np.sum(p.finished_naive)),
        "time_formats": mixed_time_formats,
        "complete_test_blocks": blocks,
        "failing_blocks": failing_blocks,
        "empty_blocks": empty_blocks,
        "training_eligible_labels_at_day30": training_eligible,
        "first_calibration_available_labels_at_day40": first_calibration_available,
        "test_model_label_availability": model_test,
        "test_block_model_request_counts": block_model_requests,
        "failing_models": failing_models,
        "by_status_all": status_all,
        "by_status_test": status_test,
        "day0_utc_us": p.day0_us,
        "last_created_utc_us": int(p.created_us.max()),
        "full_test_end_day_exclusive": p.full_test_end_day,
        "incomplete_tail_rows": int(np.sum(p.day >= p.full_test_end_day)),
    }


def arrival_counts(created_us: np.ndarray, model_code: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Count [t-60min, t) arrivals; simultaneous requests see identical history."""
    created_us = np.asarray(created_us, np.int64)
    model_code = np.asarray(model_code, np.int32)
    if len(created_us) != len(model_code) or np.any(np.diff(created_us) < 0):
        raise ValueError("arrival arrays must have matching lengths and sorted times")
    window_left = np.searchsorted(created_us, created_us - HOUR_US, side="left")
    same_time_left = np.searchsorted(created_us, created_us, side="left")
    global_count = (same_time_left - window_left).astype(np.int32)
    model_count = np.empty(len(created_us), np.int32)
    order = np.argsort(model_code, kind="stable")
    groups = model_code[order]
    boundaries = np.r_[0, 1 + np.flatnonzero(groups[1:] != groups[:-1]), len(groups)]
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        indices = order[left:right]
        t = created_us[indices]
        model_count[indices] = (
            np.searchsorted(t, t, side="left")
            - np.searchsorted(t, t - HOUR_US, side="left")
        )
    return global_count, model_count


def eligible_training_indices(p: Prepared, delay_hours: int = 1) -> np.ndarray:
    cutoff = p.day0_us + FIT_END_DAY * DAY_US
    return np.flatnonzero(
        p.positive & (p.created_us >= p.day0_us) & (p.created_us < cutoff)
        & (p.finished_us + delay_hours * HOUR_US <= cutoff)
    )


def training_indices(p: Prepared, delay_hours: int = 1) -> np.ndarray:
    eligible = eligible_training_indices(p, delay_hours)
    if len(eligible) > TRAIN_CAP:
        sampled = np.random.default_rng(42).choice(eligible, TRAIN_CAP, replace=False)
        return np.sort(sampled)
    return eligible


def category_mapping(model_codes: np.ndarray, indices: np.ndarray,
                     model_names: list[str]) -> tuple[np.ndarray, list[str]]:
    counts = np.bincount(model_codes[indices], minlength=len(model_names))
    ranked = sorted((i for i, count in enumerate(counts)
                     if count and model_names[i] not in ("<MISSING>", "\\N")),
                    key=lambda i: (-int(counts[i]), model_names[i]))
    top = ranked[:MAX_MODEL_CATEGORIES]
    mapping = np.full(len(model_names), MAX_MODEL_CATEGORIES, np.uint8)
    for feature_code, raw_code in enumerate(top):
        mapping[raw_code] = feature_code
    return mapping, [safe_model_name(model_names[i]) for i in top]


def build_features(p: Prepared, model_mapping: np.ndarray) -> np.ndarray:
    global_count, model_count = arrival_counts(p.created_us, p.model_code)
    features = np.empty((len(p.created_us), 5), np.float32)
    features[:, 0] = model_mapping[p.model_code]
    features[:, 1] = (p.created_us // HOUR_US) % 24
    # 1970-01-01 was Thursday, with Monday encoded as zero.
    features[:, 2] = ((p.created_us // DAY_US) + 3) % 7
    features[:, 3] = np.log1p(global_count)
    features[:, 4] = np.log1p(model_count)
    return features


def fit_base(features: np.ndarray, p: Prepared, train: np.ndarray) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(
        loss="quantile", quantile=Q, categorical_features=[0],
        max_iter=140, max_leaf_nodes=15, min_samples_leaf=60,
        l2_regularization=10, learning_rate=0.05, random_state=42,
    )
    model.fit(features[train], p.duration_seconds[train])
    return model


def predict_in_chunks(model: HistGradientBoostingRegressor, features: np.ndarray,
                      indices: np.ndarray) -> np.ndarray:
    result = np.empty(len(indices), np.float64)
    for start in range(0, len(indices), PREDICTION_CHUNK):
        stop = min(start + PREDICTION_CHUNK, len(indices))
        result[start:stop] = np.maximum(0.0, model.predict(features[indices[start:stop]]))
    return result


def causal_rolling_predictions(
    created_us: np.ndarray, finished_us: np.ndarray, labels: np.ndarray,
    base: np.ndarray, model_code: np.ndarray, *, delay_hours: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reveal feedback only after finished_at + assumed delivery delay."""
    n = len(created_us)
    if not (n == len(finished_us) == len(labels) == len(base) == len(model_code)):
        raise ValueError("Replay array lengths differ")
    if np.any(np.diff(created_us) < 0) or np.any(labels <= 0) or delay_hours < 0:
        raise ValueError("Replay requires sorted arrival, positive labels and delay")
    global_buffer = RollingQuantileBuffer(GLOBAL_WINDOW, Q)
    model_buffers: dict[int, RollingQuantileBuffer] = {}
    pending: list[tuple[int, int]] = []
    global_pred = np.empty(n, np.float64)
    model_pred = np.empty(n, np.float64)
    support = np.empty(n, np.int16)
    delay_us = delay_hours * HOUR_US
    for i in range(n):
        now = int(created_us[i])
        while pending and pending[0][0] <= now:
            _, j = heapq.heappop(pending)
            residual = float(labels[j] - base[j])
            global_buffer.add(residual)
            key = int(model_code[j])
            local = model_buffers.get(key)
            if local is None:
                local = model_buffers[key] = RollingQuantileBuffer(MODEL_WINDOW, Q)
            local.add(residual)
        global_offset = global_buffer.value() or 0.0
        current = model_buffers.get(int(model_code[i]))
        n_local = len(current) if current is not None else 0
        support[i] = n_local
        if n_local >= MODEL_MINIMUM:
            local_value = current.value()
            assert local_value is not None
            weight = n_local / (n_local + MODEL_SHRINKAGE)
            offset = weight * local_value + (1.0 - weight) * global_offset
        else:
            offset = global_offset
        global_pred[i] = max(0.0, base[i] + global_offset)
        model_pred[i] = max(0.0, base[i] + offset)
        heapq.heappush(pending, (int(finished_us[i]) + delay_us, i))
    return global_pred, model_pred, support


def minimum_margin(labels: np.ndarray, predictions: np.ndarray,
                   target: float = GUARD_TARGET) -> float:
    if len(labels) == 0 or len(labels) != len(predictions):
        raise ValueError("Guard needs matching nonempty labels and predictions")
    residual = np.asarray(labels, float) - np.asarray(predictions, float)
    rank = int(math.ceil(target * len(residual))) - 1
    return max(0.0, float(np.partition(residual, rank)[rank]))


def fit_guard(labels: np.ndarray, predictions: np.ndarray, model_code: np.ndarray,
              n_models: int) -> dict[str, Any]:
    global_margin = minimum_margin(labels, predictions)
    additional = np.zeros(n_models, np.float64)
    support = np.bincount(model_code, minlength=n_models)
    for model in np.flatnonzero(support >= GUARD_MINIMUM):
        take = model_code == model
        additional[model] = minimum_margin(labels[take], predictions[take] + global_margin)
    guarded = predictions + global_margin + additional[model_code]
    return {
        "global_margin_seconds": global_margin,
        "model_extra_margin_seconds": additional,
        "model_validation_support": support,
        "selection_rows": int(len(labels)),
        "selection_coverage": float(np.mean(labels <= guarded)),
    }


def apply_guard(predictions: np.ndarray, model_code: np.ndarray,
                policy: dict[str, Any]) -> np.ndarray:
    return predictions + policy["global_margin_seconds"] + policy["model_extra_margin_seconds"][model_code]


def block_policies(p: Prepared, replay_indices: np.ndarray,
                   model_predictions: np.ndarray, *, delay_hours: int,
                   n_models: int) -> tuple[np.ndarray, list[dict[str, Any]], list[int]]:
    created = p.created_us[replay_indices]
    finished = p.finished_us[replay_indices]
    day = p.day[replay_indices]
    label = p.duration_seconds[replay_indices]
    model_code = p.model_code[replay_indices]
    guarded = np.full(len(replay_indices), np.nan, np.float64)
    policies: list[dict[str, Any]] = []
    unavailable: list[int] = []
    for start in p.complete_blocks:
        cutoff = p.day0_us + start * DAY_US
        selection = (
            (day >= start - BLOCK_DAYS) & (day < start)
            & (finished + delay_hours * HOUR_US <= cutoff)
        )
        target = (day >= start) & (day < start + BLOCK_DAYS)
        selection_count = int(selection.sum())
        record: dict[str, Any] = {
            "test_start_day": start, "test_end_day_exclusive": start + BLOCK_DAYS,
            "calibration_available_labels": selection_count,
            "test_positive_labels": int(target.sum()),
        }
        if not target.any():
            record["usable"] = False
            record["reason"] = "empty_test_block"
            unavailable.append(start)
        elif selection_count < GUARD_VALIDATION_MINIMUM:
            record["usable"] = False
            record["reason"] = "fewer_than_1000_available_calibration_labels"
            unavailable.append(start)
        else:
            policy = fit_guard(label[selection], model_predictions[selection],
                               model_code[selection], n_models)
            guarded[target] = apply_guard(model_predictions[target], model_code[target], policy)
            record.update({
                "usable": True,
                "global_margin_seconds": policy["global_margin_seconds"],
                "model_extra_margin_seconds": {
                    str(i): float(value) for i, value in enumerate(policy["model_extra_margin_seconds"])
                    if value > 0
                },
                "model_validation_support": {
                    str(i): int(value) for i, value in enumerate(policy["model_validation_support"])
                    if value > 0
                },
                "calibration_coverage": policy["selection_coverage"],
            })
        policies.append(record)
    return guarded, policies, unavailable


def _score_one(labels: np.ndarray, prediction: np.ndarray) -> dict[str, float | int] | None:
    usable = np.isfinite(prediction)
    if not usable.any():
        return None
    y = labels[usable]
    pred = prediction[usable]
    error = y - pred
    return {
        "n": int(len(y)),
        "pinball_seconds": float(np.mean(np.maximum(Q * error, (Q - 1) * error))),
        "coverage": float(np.mean(y <= pred)),
        "mean_predicted_seconds": float(np.mean(pred)),
        "mean_target_seconds": float(np.mean(y)),
    }


def _group_scores(labels: np.ndarray, predictions: dict[str, np.ndarray],
                  codes: np.ndarray, names: list[str]) -> dict[str, Any]:
    """Aggregate millions of rows in O(rows + groups), without group masks."""
    n_groups = len(names)
    count = np.bincount(codes, minlength=n_groups)
    if len(count) != n_groups:
        raise ValueError("Group code exceeds the declared category range")
    method_stats: dict[str, dict[str, np.ndarray]] = {}
    for method, prediction in predictions.items():
        valid = np.isfinite(prediction)
        key = codes[valid]
        y = labels[valid]
        pred = prediction[valid]
        error = y - pred
        loss = np.maximum(Q * error, (Q - 1.0) * error)
        method_stats[method] = {
            "n": np.bincount(key, minlength=n_groups),
            "loss": np.bincount(key, weights=loss, minlength=n_groups),
            "covered": np.bincount(key, weights=(y <= pred).astype(np.int8), minlength=n_groups),
            "prediction": np.bincount(key, weights=pred, minlength=n_groups),
            "target": np.bincount(key, weights=y, minlength=n_groups),
        }
    out: dict[str, Any] = {}
    for i in np.flatnonzero(count):
        scores = {}
        for method, stats in method_stats.items():
            n = int(stats["n"][i])
            scores[method] = None if n == 0 else {
                "n": n,
                "pinball_seconds": float(stats["loss"][i] / n),
                "coverage": float(stats["covered"][i] / n),
                "mean_predicted_seconds": float(stats["prediction"][i] / n),
                "mean_target_seconds": float(stats["target"][i] / n),
            }
        out[names[i]] = {"positive_labels": int(count[i]), "methods": scores}
    return out


def summarize_scores(p: Prepared, trace: Trace, replay_indices: np.ndarray,
                     base: np.ndarray, global_pred: np.ndarray,
                     model_pred: np.ndarray, guarded: np.ndarray,
                     support: np.ndarray) -> dict[str, Any]:
    replay_day = p.day[replay_indices]
    test = (replay_day >= FIRST_TEST_DAY) & (replay_day < p.full_test_end_day)
    y = p.duration_seconds[replay_indices][test]
    predictions = {
        "base": base[test], "global": global_pred[test],
        "model": model_pred[test], "guard92": guarded[test],
    }


    if len(y) == 0:
        return {"all": {}, "by_day": {}, "by_block": {}, "by_model": {},
                "by_status": {}, "by_block_model": {}, "by_block_status": {},
                "paired_day_model_minus_global": [], "feedback_support": {},
                "paired_guard_available": {}}
    day = replay_day[test]
    model_code = p.model_code[replay_indices][test]
    status_code = p.status_code[replay_indices][test]
    blocks = (day - FIRST_TEST_DAY) // BLOCK_DAYS
    n_blocks = len(p.complete_blocks)
    block_names = [str(start) for start in p.complete_blocks]
    by_day = _group_scores(y, predictions, day - FIRST_TEST_DAY,
                           [str(d) for d in range(FIRST_TEST_DAY, p.full_test_end_day)])
    by_block = _group_scores(y, predictions, blocks, block_names)
    safe_names = safe_model_names(trace.model_names)
    by_model = _group_scores(y, predictions, model_code, safe_names)
    by_status = _group_scores(y, predictions, status_code, trace.status_names)
    n_models = len(trace.model_names)
    n_statuses = len(trace.status_names)
    by_block_model = _group_scores(
        y, predictions, blocks * n_models + model_code,
        [f"{block_names[b]} | {safe_names[m]}"
         for b in range(n_blocks) for m in range(n_models)],
    )
    by_block_status = _group_scores(
        y, predictions, blocks * n_statuses + status_code,
        [f"{block_names[b]} | {trace.status_names[s]}"
         for b in range(n_blocks) for s in range(n_statuses)],
    )
    paired_daily = [
        {"day": name, "n": value["positive_labels"],
         "model_minus_global_pinball_seconds":
         value["methods"]["model"]["pinball_seconds"]
         - value["methods"]["global"]["pinball_seconds"]}
        for name, value in by_day.items()
    ]
    return {
        "all": {method: _score_one(y, pred) for method, pred in predictions.items()},
        "paired_guard_available": {
            "rows": int(np.isfinite(predictions["guard92"]).sum()),
            "methods": {
                method: _score_one(y[np.isfinite(predictions["guard92"])],
                                   pred[np.isfinite(predictions["guard92"])])
                for method, pred in predictions.items()
            },
        },
        "by_day": by_day, "by_block": by_block,
        "by_model": by_model, "by_status": by_status,
        "by_block_model": by_block_model, "by_block_status": by_block_status,
        "paired_day_model_minus_global": paired_daily,
        "feedback_support": {
            "model_window_active_count": int(np.sum(support[test] >= MODEL_MINIMUM)),
            "test_positive_rows": int(test.sum()),
        },
    }


def save_test_predictions(
    output: Path, p: Prepared, replay_indices: np.ndarray,
    base: np.ndarray, global_pred: np.ndarray, model_pred: np.ndarray,
    guarded: np.ndarray,
) -> dict[str, Any]:
    """Write the numeric complete-test replay without IDs, tokens or text."""
    path = output / "exploratory_utc0_feedback1h_test_predictions.parquet"
    temporary = path.with_suffix(".parquet.tmp")
    fields = [
        pa.field("created_utc_us", pa.int64(), nullable=False),
        pa.field("duration_seconds", pa.float64(), nullable=False),
        pa.field("model_code", pa.int32(), nullable=False),
        pa.field("status_code", pa.int32(), nullable=False),
        pa.field("day", pa.int32(), nullable=False),
        pa.field("base_p90_seconds", pa.float64(), nullable=False),
        pa.field("global_p90_seconds", pa.float64(), nullable=False),
        pa.field("model_p90_seconds", pa.float64(), nullable=False),
        pa.field("guard92_p90_seconds", pa.float64()),
    ]
    schema = pa.schema(fields)
    replay_day = p.day[replay_indices]
    left = int(np.searchsorted(replay_day, FIRST_TEST_DAY, side="left"))
    right = int(np.searchsorted(replay_day, p.full_test_end_day, side="left"))
    if not np.all(np.diff(replay_day) >= 0):
        raise ValueError("Replay days must be sorted before exporting test predictions")
    output.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(temporary, schema, compression="zstd", use_dictionary=False) as writer:
        for start in range(left, right, 200_000):
            stop = min(start + 200_000, right)
            source = replay_indices[start:stop]
            guard_values = guarded[start:stop]
            batch = pa.Table.from_arrays([
                pa.array(p.created_us[source], type=pa.int64()),
                pa.array(p.duration_seconds[source], type=pa.float64()),
                pa.array(p.model_code[source], type=pa.int32()),
                pa.array(p.status_code[source], type=pa.int32()),
                pa.array(p.day[source], type=pa.int32()),
                pa.array(base[start:stop], type=pa.float64()),
                pa.array(global_pred[start:stop], type=pa.float64()),
                pa.array(model_pred[start:stop], type=pa.float64()),
                pa.array(guard_values, mask=~np.isfinite(guard_values), type=pa.float64()),
            ], schema=schema)
            writer.write_table(batch)
    os.replace(temporary, path)
    if pq.ParquetFile(path).metadata.num_rows != right - left:
        raise ValueError("Saved prediction count differs from complete test rows")
    return {
        "path": str(path),
        "rows": right - left,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "columns": [field.name for field in fields],
        "compression": "zstd",
    }

def success_check(metrics: dict[str, Any], quality: dict[str, Any],
                  unavailable_policies: list[int], trace: Trace) -> dict[str, Any]:
    block_scores = metrics["by_block"]
    nonempty = [record for record in block_scores.values() if record["positive_labels"]]
    reasons: list[str] = []
    if not quality["passes"]:
        reasons.append("data completeness or minimum-sample gate failed")
    if quality["empty_blocks"]:
        reasons.append("one or more complete test blocks contain no requests")
    if len(nonempty) < 7:
        reasons.append("fewer than seven complete nonempty 10-day test blocks")
    if unavailable_policies:
        reasons.append("fewer than 1000 available calibration labels for a test block")
    all_scores = metrics["all"]
    if not all_scores or all_scores.get("guard92") is None:
        reasons.append("guarded scores unavailable")
        return {"passes": False, "reasons": reasons}
    model_better_pooled = (
        all_scores["model"]["pinball_seconds"] < all_scores["global"]["pinball_seconds"]
    )
    better_blocks = sum(
        record["methods"]["model"]["pinball_seconds"]
        < record["methods"]["global"]["pinball_seconds"]
        for record in nonempty
    )
    needed = math.ceil(2 * len(nonempty) / 3)
    if not model_better_pooled or better_blocks < needed:
        reasons.append("model residual failed paired global pinball criterion")
    failing_coverage_blocks = [
        name for name, record in block_scores.items()
        if record["methods"]["guard92"] is None
        or record["methods"]["guard92"]["coverage"] < 0.90
    ]
    failing_coverage_models = []
    for name, requests in quality["test_block_model_request_counts"].items():
        if requests < 1000:
            continue
        record = metrics["by_block_model"].get(name)
        guard = None if record is None else record["methods"]["guard92"]
        if guard is None or guard["coverage"] < 0.90:
            failing_coverage_models.append(name)
    if failing_coverage_blocks or failing_coverage_models:
        reasons.append("guarded p90 coverage below 90% in a block or supported model")
    guard_vs_global_comparable = not unavailable_policies and (
        metrics["paired_guard_available"]["rows"] == all_scores["global"]["n"]
    )
    if not guard_vs_global_comparable:
        reasons.append("guard-vs-global pooled pinball criterion unevaluable on all test rows")
    elif (metrics["paired_guard_available"]["methods"]["guard92"]["pinball_seconds"]
          > metrics["paired_guard_available"]["methods"]["global"]["pinball_seconds"]):
        reasons.append("guarded pooled pinball worse than global residual")
    return {
        "passes": not reasons,
        "reasons": reasons,
        "model_better_pooled": model_better_pooled,
        "model_better_blocks": better_blocks,
        "model_better_blocks_required": needed,
        "failing_coverage_blocks": failing_coverage_blocks,
        "failing_coverage_block_models": failing_coverage_models,
        "guard_vs_global_comparable": guard_vs_global_comparable,
    }


def evaluate_variant(trace: Trace, timezone_offset_hours: int,
                     output: Path | None = None, *, exploratory: bool = False,
                     feedback_delays_hours: tuple[int, ...] = FEEDBACK_DELAYS_HOURS,
                     exploratory_selection: dict[str, int] | None = None,
                     save_test_predictions_file: bool = False) -> dict[str, Any]:
    p = prepare(trace, timezone_offset_hours)
    quality = quality_gate(p, trace)
    result: dict[str, Any] = {
        "timezone_naive_interpretation": f"UTC+{timezone_offset_hours}",
        "timezone_offset_hours": timezone_offset_hours,
        "quality_gate": quality,
        "scoring_performed": False,
        "primary_validation_valid": False,
        "exploratory_after_quality_gate_failure": bool(exploratory and not quality["passes"]),
    }
    if not quality["passes"] and not exploratory:
        return result
    if exploratory:
        if exploratory_selection is None:
            result["reason"] = "arrival-count-only exploratory start was unavailable"
            return result
        result["exploratory_protocol"] = str(EXPLORATORY_PROTOCOL.relative_to(ROOT))
        result["exploratory_protocol_sha256"] = sha256(EXPLORATORY_PROTOCOL)
        result["exploratory_start_selection"] = exploratory_selection
        p = reanchor(p, exploratory_selection["selected_day0_utc_us"])
        result["exploratory_split_quality"] = quality_gate(p, trace)
    scoring_quality = result.get("exploratory_split_quality", quality)
    eligible = eligible_training_indices(p)
    train = training_indices(p)
    result["training"] = {
        "eligible_labels": int(len(eligible)),
        "sampled_labels": int(len(train)),
        "sample_rng": "numpy.random.default_rng(42).choice(eligible_sorted_indices, 500000, replace=False), then sorted",
        "train_row_index_sha256": hashlib.sha256(train.tobytes()).hexdigest(),
    }
    if len(train) < MIN_TRAIN:
        result["reason"] = "fewer than 10000 available training labels"
        return result
    mapping, model_categories = category_mapping(p.model_code, eligible, trace.model_names)
    result["training"]["categorical_model_names"] = model_categories
    result["training"]["other_model_feature_code"] = MAX_MODEL_CATEGORIES
    features = build_features(p, mapping)
    model = fit_base(features, p, train)
    replay_indices = np.flatnonzero(p.positive & (p.day >= FIT_END_DAY))
    base = predict_in_chunks(model, features, replay_indices)
    del features
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        suffix = "exploratory" if exploratory else "primary"
        model_path = output / f"base_model_naive_utc_plus_{timezone_offset_hours}_{suffix}.joblib"
        joblib.dump({"model": model, "model_categories": model_categories,
                     "feature_columns": ["model", "UTC_hour", "UTC_weekday",
                                         "log1p_global_60m_arrivals", "log1p_model_60m_arrivals"]},
                    model_path)
        result["training"]["model_artifact"] = str(model_path)
        result["training"]["model_artifact_sha256"] = sha256(model_path)
    created = p.created_us[replay_indices]
    finished = p.finished_us[replay_indices]
    labels = p.duration_seconds[replay_indices]
    raw_models = p.model_code[replay_indices]
    variants: dict[str, Any] = {}
    for delay_hours in feedback_delays_hours:
        global_pred, model_pred, support = causal_rolling_predictions(
            created, finished, labels, base, raw_models, delay_hours=delay_hours,
        )
        guarded, policies, unavailable = block_policies(
            p, replay_indices, model_pred, delay_hours=delay_hours,
            n_models=len(trace.model_names),
        )
        metrics = summarize_scores(
            p, trace, replay_indices, base, global_pred, model_pred, guarded, support,
        )
        success = success_check(metrics, scoring_quality, unavailable, trace)
        if exploratory:
            success["passes"] = False
            success["reasons"].append("explicit exploratory mode cannot satisfy primary validation")
        variants[str(delay_hours)] = {
            "delay_hours": delay_hours,
            "base_model": "fixed at the one-hour feedback assumption",
            "block_guard_policies": policies,
            "unavailable_guard_blocks": unavailable,
            "scores": metrics,
            "pre_registered_success": success,
            "primary_validation_valid": bool(
                scoring_quality["passes"] and not exploratory and not unavailable
                and len([record for record in metrics["by_block"].values()
                         if record["positive_labels"]]) >= 7
            ),
        }
        if (save_test_predictions_file and exploratory
                and timezone_offset_hours == 0 and delay_hours == 1):
            if output is None:
                raise ValueError("Prediction export requires an output directory")
            variants[str(delay_hours)]["test_prediction_file"] = save_test_predictions(
                output, p, replay_indices, base, global_pred, model_pred, guarded,
            )
            variants[str(delay_hours)]["model_code_names"] = safe_model_names(trace.model_names)
            variants[str(delay_hours)]["status_code_names"] = trace.status_names
    result["scoring_performed"] = True
    result["training"]["replay_positive_labels_day30_onward"] = int(len(replay_indices))
    result["feedback_delays"] = variants
    result["primary_validation_valid"] = bool(
        variants.get("1", {}).get("primary_validation_valid", False)
    )
    return result


def evaluate(source_dir: Path = SOURCE_DIR, output: Path = OUTPUT, *,
             exploratory: bool = False,
             timezone_offsets_hours: tuple[int, ...] = TIMEZONE_OFFSETS_HOURS,
             feedback_delays_hours: tuple[int, ...] = FEEDBACK_DELAYS_HOURS,
             save_test_predictions_file: bool = False) -> dict[str, Any]:
    if save_test_predictions_file and (
        not exploratory or 0 not in timezone_offsets_hours or 1 not in feedback_delays_hours
    ):
        raise ValueError("Prediction export requires --exploratory, UTC+0 and one-hour feedback")
    trace = load_trace(source_dir)
    output.mkdir(parents=True, exist_ok=True)
    if exploratory:
        original_day0 = int(trace.created_us.min() // DAY_US * DAY_US)
        exploratory_selection = select_exploratory_start(
            trace.created_us, original_day0_utc_us=original_day0,
        )
    else:
        exploratory_selection = None
    if exploratory_selection is not None and (
        exploratory_selection["selected_offset_days"] != 183
        or exploratory_selection["fit_window_all_created_requests"] != 569_748
        or exploratory_selection["initialization_window_all_created_requests"] != 1_182_441
    ):
        raise ValueError("Pinned SwissAI exploratory date/count selection differs from the pre-score protocol")
    result: dict[str, Any] = {
        "method": ("exploratory SwissAI request termination-time p90 replay"
                   if exploratory else "pre-registered SwissAI chronological request termination-time p90 replay"),
        "interpretation_label": (
            "exploratory result; original pre-registered validation failed; VM action effects unevaluated"
            if exploratory else "pre-registered protocol subject to its data and sample gates"
        ),
        "protocol": "reports/swissai_external_validation_protocol.md",
        "source_revision": trace.audit["source"]["revision"],
        "source_sha256": trace.audit["source"]["sha256"],
        "compact_sha256": trace.compact_sha256,
        "raw_audit_sha256": trace.audit_sha256,
        "evaluation_script_sha256": sha256(Path(__file__)),
        "protocol_sha256": sha256(ROOT / "reports/swissai_external_validation_protocol.md"),
        "versions": {
            "python": platform.python_version(), "numpy": np.__version__,
            "pyarrow": pa.__version__, "scikit_learn": sklearn.__version__,
        },
        "target": "recorded positive request termination time, including error-status records",
        "not_action_effect_or_success_only_slo": True,
        "exploratory_mode": exploratory,
        "exploratory_protocol": str(EXPLORATORY_PROTOCOL.relative_to(ROOT)) if exploratory else None,
        "exploratory_protocol_sha256": sha256(EXPLORATORY_PROTOCOL) if exploratory else None,
        "exploratory_start_selection": exploratory_selection,
        "selected_timezone_offsets_hours": timezone_offsets_hours,
        "selected_feedback_delays_hours": feedback_delays_hours,
        "save_test_predictions": save_test_predictions_file,
        "variants": {},
    }
    for offset in timezone_offsets_hours:
        variant = evaluate_variant(
            trace, offset, output, exploratory=exploratory,
            feedback_delays_hours=feedback_delays_hours,
            exploratory_selection=exploratory_selection,
            save_test_predictions_file=save_test_predictions_file,
        )
        result["variants"][str(offset)] = variant
        # Save each variant immediately: the full replay can take time and may be interrupted.
        filename = "metrics_exploratory.json" if exploratory else "metrics.json"
        (output / filename).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--exploratory", action="store_true",
                        help="Explicitly score the unchanged split after a failed quality gate; never count as primary validation")
    parser.add_argument("--timezone-offset-hours", type=int, nargs="+", choices=(0, 1, 2),
                        default=list(TIMEZONE_OFFSETS_HOURS))
    parser.add_argument("--feedback-delay-hours", type=int, nargs="+", choices=(0, 1, 6),
                        default=list(FEEDBACK_DELAYS_HOURS))
    parser.add_argument("--save-test-predictions", action="store_true",
                        help="Save numeric UTC+0/1h exploratory predictions for later baseline analysis")
    args = parser.parse_args()
    result = evaluate(
        args.source_dir, args.output, exploratory=args.exploratory,
        timezone_offsets_hours=tuple(args.timezone_offset_hours),
        feedback_delays_hours=tuple(args.feedback_delay_hours),
        save_test_predictions_file=args.save_test_predictions,
    )
    print(json.dumps({
        offset: {"quality_passes": variant["quality_gate"]["passes"],
                 "scoring_performed": variant["scoring_performed"]}
        for offset, variant in result["variants"].items()
    }, indent=2))
