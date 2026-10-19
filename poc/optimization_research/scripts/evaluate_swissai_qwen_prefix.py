"""Frozen train/validation/test runner for SwissAI Qwen prefix reuse.

``prepare`` opens only training and validation reuse labels to check support,
then fits one fixed classifier on a label-independent daily hash sample.
``validate`` scores only validation rows and chooses one cache admission
threshold. ``test`` cannot open held-out rows until the validation gate and
all artifact/code hashes have been checked. Do not call test during method
development; it is the one-shot final score path.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Iterable, Iterator

import joblib
import numpy as np
import pyarrow.parquet as pq
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss

from swissai_prefix_cache_core import (
    CachePolicySpec, CacheRequest, default_policy_specs, replay_requests,
)
from swissai_qwen_reuse_core import (
    DAY_US, HORIZON_US, Request, future_reuse_labels, historical_features,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/swissai_serving_trace/qwen_buckets_sorted.parquet"
QUALITY = ROOT / "data/external/swissai_serving_trace/qwen_buckets_quality.json"
PROTOCOL = ROOT / "reports/swissai_qwen_prefix_cache_protocol.md"
ADDENDUM = ROOT / "reports/swissai_qwen_prefix_cache_implementation_frozen.md"
REUSE_CORE = ROOT / "scripts/swissai_qwen_reuse_core.py"
CACHE_CORE = ROOT / "scripts/swissai_prefix_cache_core.py"
SCRIPT = Path(__file__).resolve()
ARTIFACTS = ROOT / "artifacts/swissai_qwen_prefix_v1"
MODEL = ARTIFACTS / "model.joblib"
MANIFEST = ARTIFACTS / "manifest.json"
SUPPORT = ARTIFACTS / "support.json"
VALIDATION = ARTIFACTS / "validation.json"
VALIDATION_LOCK = ARTIFACTS / "validation.lock.json"
TEST = ARTIFACTS / "test.json"

PROTOCOL_SHA = "399dabeca72b196e2195953fe25a531fe71477dfa7ab8759a4bd8a1fd00dbbfb"
ADDENDUM_SHA = "caf5fcdcbae75a9df7377555ab9b1286018d1a337125b434db2adf6c8598cdd5"
REUSE_CORE_SHA = "7254144d75a513b3f0c31ee079bb671d1b08bf70ccf2117cd365c783be229986"
CACHE_CORE_SHA = "471f3c725b561212b942d6310489d8a9b35bd69cdd16d0486057fe3c1fffe228"
RAW_SHA = "58b2265b98a10ec3bb2a0b455c520731c1296967906c7adc7b6d3710a013e402"
RAW_ROWS = 3_994_435
RAW_BYTES = 4_617_817_342
QUALITY_SHA = "e530b1b74cfb4076e34962dbfd81414fb8d0a76de0a73f1c39a8b341c7d727dc"
COMPACT_SHA = "b50c10e3034c288332762d67c8fde09989d31ba10678dd2be97fd6deb20f0d7b"
FEATURES = (
    "complete_blocks", "token_remainder", "utc_hour", "utc_weekday",
    "arrivals_5m", "exact_5m", "exact_60m", "seconds_since_exact",
    "prefix_1_60m", "prefix_2_60m", "prefix_4_60m",
)
PARAMETERS = {
    "max_iter": 100, "max_leaf_nodes": 15, "max_depth": 3,
    "min_samples_leaf": 100, "learning_rate": 0.05,
    "l2_regularization": 10, "early_stopping": False, "random_state": 42,
}
THRESHOLDS = (0.0, 0.1, 0.2, 0.4, 0.6, 0.8)
SPLITS = {
    "train": ("2025-05-14T00:00:00+00:00", "2025-08-01T00:00:00+00:00"),
    "validation": ("2025-08-01T00:00:00+00:00", "2025-08-08T00:00:00+00:00"),
    "locked_test": ("2025-08-08T00:00:00+00:00", "2025-08-23T00:00:00+00:00"),
}
SPLIT_RECORD = {name: list(times) for name, times in SPLITS.items()}
EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_us(value: str) -> int:
    delta = datetime.fromisoformat(value).astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def bounds(split: str) -> tuple[int, int]:
    if split not in SPLITS:
        raise ValueError("unknown_frozen_split")
    return tuple(_utc_us(value) for value in SPLITS[split])  # type: ignore[return-value]


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact_json_must_be_object")
    return value


def verify_fixed_code() -> dict[str, str]:
    hashes = {
        "protocol_sha256": sha256(PROTOCOL),
        "addendum_sha256": sha256(ADDENDUM),
        "reuse_core_sha256": sha256(REUSE_CORE),
        "cache_core_sha256": sha256(CACHE_CORE),
        "evaluator_sha256": sha256(SCRIPT),
    }
    for key, expected in (
        ("protocol_sha256", PROTOCOL_SHA), ("addendum_sha256", ADDENDUM_SHA),
        ("reuse_core_sha256", REUSE_CORE_SHA), ("cache_core_sha256", CACHE_CORE_SHA),
    ):
        if hashes[key] != expected:
            raise ValueError(f"frozen_code_or_protocol_changed:{key}")
    return hashes


def verify_source() -> dict:
    """Verify provenance before reading any label; hash the exact compact."""
    code = verify_fixed_code()
    quality_sha = sha256(QUALITY)
    compact_sha = sha256(SOURCE)
    if quality_sha != QUALITY_SHA or compact_sha != COMPACT_SHA:
        raise ValueError("frozen_qwen_quality_or_compact_hash_changed")
    quality = read_json(QUALITY)
    source = quality.get("source", {})
    compact = quality.get("compact", {})
    if (source.get("decompressed_sha256") != RAW_SHA
            or source.get("decompressed_bytes") != RAW_BYTES
            or source.get("raw_rows") != RAW_ROWS
            or source.get("pinned_source_verified") is not True):
        raise ValueError("pinned_qwen_raw_source_not_verified")
    if compact.get("sha256") != compact_sha:
        raise ValueError("qwen_compact_hash_mismatch")
    if compact.get("rows") != pq.ParquetFile(SOURCE).metadata.num_rows:
        raise ValueError("qwen_compact_row_count_mismatch")
    if quality.get("interpretation", {}).get("future_reuse_labels_or_cache_outcomes_computed") is not False:
        raise ValueError("input_quality_contains_outcome_computation")
    return {
        "quality_sha256": quality_sha,
        "compact_sha256": compact["sha256"],
        "compact_rows": int(compact["rows"]),
        "source_sha256": RAW_SHA,
        "code": code,
        "support_by_fixed_split": quality.get("support_by_fixed_split", {}),
    }


def stream_rows(split: str) -> Iterator[tuple[int, bytes, int, tuple[int, ...]]]:
    """Stream only row groups intersecting this split, without materializing later splits."""
    begin, end = bounds(split)
    parquet = pq.ParquetFile(SOURCE)
    names = ["created_utc_us", "id_sha256", "token_count", "bucket_ids"]
    created_column = parquet.schema_arrow.get_field_index("created_utc_us")
    previous: tuple[int, bytes] | None = None
    for rg in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(rg).column(created_column).statistics
        if statistics is None or not statistics.has_min_max:
            raise ValueError("sorted_compact_requires_time_row_group_statistics")
        if statistics.max < begin:
            continue
        if statistics.min >= end:
            break
        for batch in parquet.iter_batches(row_groups=[rg], columns=names, batch_size=8192):
            times = batch.column(0).to_numpy(zero_copy_only=False)
            left = int(np.searchsorted(times, begin, side="left"))
            right = int(np.searchsorted(times, end, side="left"))
            if right <= left:
                continue
            for row in batch.slice(left, right - left).to_pylist():
                created = row["created_utc_us"]
                id_hash = row["id_sha256"]
                token_count = row["token_count"]
                prefix = tuple(row["bucket_ids"])
                key = (created, id_hash)
                if previous is not None and key <= previous:
                    raise ValueError("qwen_compact_not_strictly_sorted")
                previous = key
                if (len(id_hash) != 32 or len(prefix) == 0
                        or len(prefix) != token_count // 16):
                    raise ValueError("qwen_compact_structural_mismatch")
                yield created, id_hash, token_count, prefix


def request_stream(split: str) -> Iterator[Request]:
    for created, _, token_count, prefix in stream_rows(split):
        yield Request(created, prefix, token_count)


def sample_training_indices(rows: Iterable[tuple[int, bytes, int, tuple[int, ...]]],
                            begin_us: int, end_us: int,
                            *, per_day: int = 5000) -> list[int]:
    """Choose daily smallest SHA256(ID) values without reading any labels."""
    if per_day < 1:
        raise ValueError("invalid_daily_sample_cap")
    heaps: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for index, (created, digest, _, _) in enumerate(rows):
        if not (begin_us + HORIZON_US <= created < end_us - HORIZON_US):
            continue
        day = created // DAY_US
        key = int.from_bytes(digest, "big")
        heap = heaps[day]
        item = (-key, -index, index)
        if len(heap) < per_day:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    return sorted(item[2] for heap in heaps.values() for item in heap)


def selection_digest(indices: list[int]) -> str:
    digest = hashlib.sha256()
    for index in indices:
        digest.update(index.to_bytes(8, "big"))
    return digest.hexdigest()


def summarize_labels(split: str, labels: list[int]) -> dict:
    counts = {"positive": 0, "negative": 0, "censored_or_warmup": 0, "rows": 0}
    by_day: dict[str, dict[str, int]] = {}
    for index, (created, _, _, _) in enumerate(stream_rows(split)):
        if index >= len(labels):
            raise ValueError("label_count_shorter_than_split")
        label = labels[index]
        counts["rows"] += 1
        if label == -1:
            counts["censored_or_warmup"] += 1
            continue
        if label not in (0, 1):
            raise ValueError("invalid_reuse_label")
        name = "positive" if label == 1 else "negative"
        counts[name] += 1
        day = date.fromordinal(EPOCH_ORDINAL + created // DAY_US).isoformat()
        by_day.setdefault(day, {"positive": 0, "negative": 0})[name] += 1
    if counts["rows"] != len(labels):
        raise ValueError("label_count_longer_than_split")
    counts["scorable"] = counts["positive"] + counts["negative"]
    return {"counts": counts, "days": dict(sorted(by_day.items()))}


def support_gate(train: dict, validation: dict, *, minimum_each: int = 10_000,
                 minimum_days: int = 3, minimum_daily_each: int = 100) -> dict:
    reasons = []
    for name, summary in (("train", train), ("validation", validation)):
        counts = summary["counts"]
        if counts["positive"] < minimum_each or counts["negative"] < minimum_each:
            reasons.append(f"{name}_class_support_below_{minimum_each}")
    supported_days = sum(
        row["positive"] >= minimum_daily_each and row["negative"] >= minimum_daily_each
        for row in validation["days"].values()
    )
    if supported_days < minimum_days:
        reasons.append(f"validation_supported_days_below_{minimum_days}")
    return {"passed": not reasons, "reasons": reasons,
            "validation_supported_days": supported_days,
            "minimum_each": minimum_each, "minimum_days": minimum_days,
            "minimum_daily_each": minimum_daily_each}


def matrix(features: dict[int, dict[str, float]], indices: Iterable[int]) -> np.ndarray:
    return np.asarray([[features[index][name] for name in FEATURES] for index in indices], dtype=np.float64)


def _check_support_against_quality(split: str, summary: dict, source: dict) -> None:
    expected = source["support_by_fixed_split"].get(split, {})
    if (summary["counts"]["rows"] != expected.get("eligible_rows")
            or summary["counts"]["scorable"] != expected.get("eligible_scoring_window")):
        raise ValueError(f"{split}_support_differs_from_prefrozen_structure_audit")


def _write_model_once(model: HistGradientBoostingClassifier) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if MODEL.exists() or MODEL.is_symlink():
        raise FileExistsError("refusing_to_replace_model")
    fd, name = tempfile.mkstemp(prefix=".qwen_model_", suffix=".joblib", dir=ARTIFACTS)
    os.close(fd)
    temp = Path(name)
    try:
        joblib.dump(model, temp, compress=3)
        os.link(temp, MODEL)
    finally:
        temp.unlink(missing_ok=True)


def prepare() -> dict:
    if MODEL.exists() or MANIFEST.exists() or SUPPORT.exists():
        raise FileExistsError("qwen_preparation_artifact_already_exists")
    source = verify_source()
    train_begin, train_end = bounds("train")
    sampled = sample_training_indices(stream_rows("train"), train_begin, train_end)
    train_labels = future_reuse_labels(request_stream("train"), train_begin, train_end)
    val_begin, val_end = bounds("validation")
    validation_labels = future_reuse_labels(request_stream("validation"), val_begin, val_end)
    train_support = summarize_labels("train", train_labels)
    validation_support = summarize_labels("validation", validation_labels)
    _check_support_against_quality("train", train_support, source)
    _check_support_against_quality("validation", validation_support, source)
    gate = support_gate(train_support, validation_support)
    support_report = {
        "kind": "swissai_qwen_prefix_support_v1",
        "source": {key: source[key] for key in ("quality_sha256", "compact_sha256", "compact_rows", "source_sha256")},
        "train": train_support, "validation": validation_support, "gate": gate,
        "sample_rows": len(sampled), "sample_index_sha256": selection_digest(sampled),
        "test_labels_or_cache_scores_opened": False,
    }
    write_once(SUPPORT, support_report)
    if not gate["passed"]:
        raise ValueError("qwen_training_support_gate_failed")
    if not sampled or any(train_labels[index] not in (0, 1) for index in sampled):
        raise ValueError("invalid_frozen_training_sample")
    selected_features = historical_features(request_stream("train"), train_begin, train_end,
                                            set(sampled))
    x = matrix(selected_features, sampled)
    y = np.asarray([train_labels[index] for index in sampled], dtype=np.int8)
    if len(np.unique(y)) != 2:
        raise ValueError("hash_sample_has_only_one_class")
    model = HistGradientBoostingClassifier(**PARAMETERS)
    model.fit(x, y)
    _write_model_once(model)
    p_train = train_support["counts"]["positive"] / train_support["counts"]["scorable"]
    manifest = {
        "kind": "swissai_qwen_prefix_model_v1",
        "source": {key: source[key] for key in ("quality_sha256", "compact_sha256", "compact_rows", "source_sha256")},
        "code": source["code"],
        "sklearn_version": sklearn.__version__,
        "features": list(FEATURES), "parameters": PARAMETERS,
        "splits": SPLIT_RECORD,
        "support_sha256": sha256(SUPPORT),
        "training_sample_rows": len(sampled),
        "training_sample_index_sha256": selection_digest(sampled),
        "training_global_positive_rate": p_train,
        "model_sha256": sha256(MODEL),
        "model_path": str(MODEL),
        "test_labels_or_cache_scores_opened": False,
    }
    write_once(MANIFEST, manifest)
    return {"support_gate": gate, "training_sample_rows": len(sampled),
            "manifest_path": str(MANIFEST), "model_sha256": manifest["model_sha256"]}


def load_manifest(*, verify_compact: bool = True) -> tuple[dict, dict | None]:
    manifest = read_json(MANIFEST)
    code = verify_fixed_code()
    if (manifest.get("kind") != "swissai_qwen_prefix_model_v1"
            or manifest.get("code") != code
            or manifest.get("features") != list(FEATURES)
            or manifest.get("parameters") != PARAMETERS
            or manifest.get("splits") != SPLIT_RECORD
            or manifest.get("sklearn_version") != sklearn.__version__
            or manifest.get("model_sha256") != sha256(MODEL)
            or manifest.get("support_sha256") != sha256(SUPPORT)):
        raise ValueError("qwen_model_manifest_or_code_mismatch")
    if manifest.get("model_path") != str(MODEL):
        raise ValueError("qwen_model_path_changed")
    source = verify_source() if verify_compact else None
    if source is not None and manifest.get("source") != {key: source[key] for key in
                                                      ("quality_sha256", "compact_sha256", "compact_rows", "source_sha256")}:
        raise ValueError("qwen_model_source_changed")
    return manifest, source


def _model() -> tuple[dict, HistGradientBoostingClassifier]:
    manifest, _ = load_manifest()
    model = joblib.load(MODEL)
    if not isinstance(model, HistGradientBoostingClassifier):
        raise ValueError("qwen_model_class_changed")
    return manifest, model


def _predict_split(split: str, model: HistGradientBoostingClassifier) -> tuple[np.ndarray, dict[int, dict[str, float]]]:
    begin, end = bounds(split)
    features = historical_features(request_stream(split), begin, end)
    x = matrix(features, range(len(features)))
    probabilities = model.predict_proba(x)[:, 1].astype(np.float64)
    if not np.all(np.isfinite(probabilities)) or np.any((probabilities < 0) | (probabilities > 1)):
        raise ValueError("invalid_frozen_model_probability")
    return probabilities, features


def _classification_scores(split: str, labels: list[int], probabilities: np.ndarray,
                           features: dict[int, dict[str, float]], p_train: float) -> dict:
    if len(labels) != len(probabilities):
        raise ValueError("prediction_label_count_mismatch")
    methods = ("global_train_rate", "recent_exact_60m_shrunk", "hgb")
    full_y: list[int] = []
    full_p: dict[str, list[float]] = {name: [] for name in methods}
    daily_y: dict[str, list[int]] = defaultdict(list)
    daily_p: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for index, (created, _, _, _) in enumerate(stream_rows(split)):
        label = labels[index]
        if label == -1:
            continue
        exact = features[index]["exact_60m"]
        scores = {
            "global_train_rate": p_train,
            "recent_exact_60m_shrunk": (exact + 20 * p_train) / (exact + 20),
            "hgb": float(probabilities[index]),
        }
        day = date.fromordinal(EPOCH_ORDINAL + created // DAY_US).isoformat()
        full_y.append(label)
        daily_y[day].append(label)
        for name, probability in scores.items():
            full_p[name].append(probability)
            daily_p[day][name].append(probability)
    if not full_y:
        raise ValueError("empty_scorable_split")

    def score(y: list[int], p: list[float]) -> dict:
        result = {"brier": float(brier_score_loss(y, p)),
                  "average_precision": float(average_precision_score(y, p)) if 0 < sum(y) < len(y) else None}
        return result

    return {
        "total": {"rows": len(full_y), "positive": int(sum(full_y)),
                  "positive_rate": float(np.mean(full_y)),
                  "methods": {name: score(full_y, full_p[name]) for name in methods}},
        "days": {
            day: {"rows": len(y), "positive": int(sum(y)), "positive_rate": float(np.mean(y)),
                  "methods": {name: score(y, daily_p[day][name]) for name in methods}}
            for day, y in sorted(daily_y.items())
        },
    }


def _cache_input(split: str, probabilities: np.ndarray) -> Iterator[CacheRequest]:
    count = 0
    for count, (created, digest, _, prefix) in enumerate(stream_rows(split), start=1):
        if count > len(probabilities):
            raise ValueError("cache_probability_shorter_than_split")
        yield CacheRequest(created, prefix, digest, float(probabilities[count - 1]))
    if count != len(probabilities):
        raise ValueError("cache_probability_longer_than_split")


def _admission_name(threshold: float, capacity: int) -> str:
    return f"admit_t{str(threshold).replace('.', 'p')}_c{capacity}"


def check_tau_zero(cache: dict, capacity: int) -> None:
    lru = cache[f"lru_c{capacity}"]
    tau_zero = cache[_admission_name(0.0, capacity)]
    if lru["total"] != tau_zero["total"] or lru["days"] != tau_zero["days"]:
        raise ValueError("tau_zero_does_not_equal_lru")


def choose_validation_policy(cache: dict, capacity: int = 4096) -> dict:
    check_tau_zero(cache, capacity)
    selected = min(THRESHOLDS, key=lambda tau: (-cache[_admission_name(tau, capacity)]["total"]["hit_ratio"], tau))
    candidate = cache[_admission_name(selected, capacity)]["total"]["hit_ratio"]
    lru = cache[f"lru_c{capacity}"]["total"]["hit_ratio"]
    lfu = cache[f"lfu_c{capacity}"]["total"]["hit_ratio"]
    gate = bool(max(lru, lfu) > 0 and candidate >= 1.05 * lru and candidate >= 1.05 * lfu)
    return {"selected_tau": selected, "candidate_hit_ratio": candidate,
            "lru_hit_ratio": lru, "lfu_hit_ratio": lfu,
            "relative_gain_over_stronger_baseline": candidate / max(lru, lfu) - 1 if max(lru, lfu) > 0 else None,
            "gate_passed": gate,
            "gate_rule": "candidate >= 1.05 * LRU and candidate >= 1.05 * LFU; stronger baseline positive"}


def validate() -> dict:
    if VALIDATION.exists() or VALIDATION_LOCK.exists():
        raise FileExistsError("qwen_validation_artifact_already_exists")
    manifest, model = _model()
    begin, end = bounds("validation")
    labels = future_reuse_labels(request_stream("validation"), begin, end)
    support = summarize_labels("validation", labels)
    prior = read_json(SUPPORT)
    if support != prior["validation"]:
        raise ValueError("validation_support_changed_since_prepare")
    probabilities, features = _predict_split("validation", model)
    classification = _classification_scores("validation", labels, probabilities,
                                            features, manifest["training_global_positive_rate"])
    cache = replay_requests(_cache_input("validation", probabilities),
                            default_policy_specs((4096,), THRESHOLDS),
                            split_start_us=begin, split_end_us=end)
    selected = choose_validation_policy(cache)
    result = {
        "kind": "swissai_qwen_prefix_validation_v1",
        "manifest_sha256": sha256(MANIFEST),
        "model_sha256": manifest["model_sha256"],
        "source": manifest["source"], "code": manifest["code"],
        "support": support, "classification": classification,
        "cache": cache, "selection": selected,
        "test_labels_or_cache_scores_opened": False,
    }
    write_once(VALIDATION, result)
    lock = {"kind": "swissai_qwen_prefix_validation_lock_v1",
            "validation_sha256": sha256(VALIDATION),
            "manifest_sha256": sha256(MANIFEST),
            "code": manifest["code"],
            "selected_tau": selected["selected_tau"],
            "gate_passed": selected["gate_passed"]}
    write_once(VALIDATION_LOCK, lock)
    return {"selection": selected, "validation_path": str(VALIDATION),
            "lock_sha256": sha256(VALIDATION_LOCK)}


def preflight_test() -> tuple[dict, dict]:
    """Reject a closed/tampered gate before opening the compact or test rows."""
    manifest, _ = load_manifest(verify_compact=False)
    validation = read_json(VALIDATION)
    lock = read_json(VALIDATION_LOCK)
    if (lock.get("kind") != "swissai_qwen_prefix_validation_lock_v1"
            or lock.get("validation_sha256") != sha256(VALIDATION)
            or lock.get("manifest_sha256") != sha256(MANIFEST)
            or lock.get("code") != manifest["code"]
            or validation.get("manifest_sha256") != sha256(MANIFEST)
            or validation.get("model_sha256") != manifest["model_sha256"]
            or validation.get("source") != manifest["source"]
            or validation.get("code") != manifest["code"]):
        raise ValueError("qwen_validation_lock_or_manifest_mismatch")
    selected = choose_validation_policy(validation["cache"])
    if (validation.get("selection") != selected
            or lock.get("selected_tau") != selected["selected_tau"]
            or lock.get("gate_passed") != selected["gate_passed"]
            or not selected["gate_passed"]):
        raise ValueError("frozen_validation_gate_failed_test_remains_closed")
    return manifest, validation


def _test_specs(tau: float) -> list[CachePolicySpec]:
    specs = []
    for capacity in (4096, 1024, 16384):
        specs += [CachePolicySpec(f"lru_c{capacity}", capacity, "lru"),
                  CachePolicySpec(f"lfu_c{capacity}", capacity, "lfu"),
                  CachePolicySpec(_admission_name(tau, capacity), capacity, "lru", tau)]
    if tau != 0.0:
        specs.append(CachePolicySpec(_admission_name(0.0, 4096), 4096, "lru", 0.0))
    return specs


def test_gate(cache: dict, tau: float) -> dict:
    capacity = 4096
    name = _admission_name(tau, capacity)
    candidate = cache[name]["total"]["hit_ratio"]
    lru = cache[f"lru_c{capacity}"]["total"]["hit_ratio"]
    lfu = cache[f"lfu_c{capacity}"]["total"]["hit_ratio"]
    days = []
    for day, row in cache[name]["days"].items():
        if row["requests"] > 0:
            base = max(cache[f"lru_c{capacity}"]["days"][day]["hit_ratio"],
                       cache[f"lfu_c{capacity}"]["days"][day]["hit_ratio"])
            days.append(row["hit_ratio"] > base)
    relative = bool(max(lru, lfu) > 0 and candidate >= 1.05 * lru and candidate >= 1.05 * lfu)
    majority = bool(days and sum(days) > len(days) / 2)
    return {"passed": relative and majority, "relative_5pct_passed": relative,
            "majority_days_passed": majority, "days_better": int(sum(days)),
            "days_eligible": len(days), "candidate_hit_ratio": candidate,
            "lru_hit_ratio": lru, "lfu_hit_ratio": lfu}


def test() -> dict:
    if TEST.exists():
        raise FileExistsError("qwen_test_artifact_already_exists")
    manifest, validation = preflight_test()
    # Only now may a held-out row or label be read.
    source = verify_source()
    if manifest["source"] != {key: source[key] for key in
                              ("quality_sha256", "compact_sha256", "compact_rows", "source_sha256")}:
        raise ValueError("qwen_test_source_changed")
    model = joblib.load(MODEL)
    begin, end = bounds("locked_test")
    labels = future_reuse_labels(request_stream("locked_test"), begin, end)
    support = summarize_labels("locked_test", labels)
    probabilities, features = _predict_split("locked_test", model)
    classification = _classification_scores("locked_test", labels, probabilities,
                                            features, manifest["training_global_positive_rate"])
    tau = validation["selection"]["selected_tau"]
    cache = replay_requests(_cache_input("locked_test", probabilities), _test_specs(tau),
                            split_start_us=begin, split_end_us=end)
    check_tau_zero(cache, 4096)
    gate = test_gate(cache, tau)
    result = {"kind": "swissai_qwen_prefix_locked_test_v1",
              "manifest_sha256": sha256(MANIFEST),
              "validation_lock_sha256": sha256(VALIDATION_LOCK),
              "selected_tau": tau, "support": support,
              "classification": classification, "cache": cache, "gate": gate,
              "scope": "offline fixed-block prefix-trie replay; not observed GPU KV, TTFT, or VM action effect"}
    write_once(TEST, result)
    return {"gate": gate, "test_path": str(TEST)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "validate", "test"))
    args = parser.parse_args()
    output = {"prepare": prepare, "validate": validate, "test": test}[args.stage]()
    print(json.dumps(output, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
