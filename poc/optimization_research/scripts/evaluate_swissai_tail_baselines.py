"""Causal direct-duration p90 baseline for the exploratory SwissAI replay.

The procedure was fixed in reports/swissai_baseline_protocol.md before this
script read the SwissAI test scores. It intentionally does not fit ACI because
the saved test-only predictions have no day-30--40 base-model residuals.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from rolling_quantile_buffer import RollingQuantileBuffer


ROOT = Path(__file__).resolve().parents[1]
COMPACT = ROOT / "data/external/swissai_serving_trace/requests_sorted.parquet"
TEST_PREDICTIONS = (
    ROOT / "artifacts/swissai_request_duration_v1/"
    "exploratory_utc0_feedback1h_test_predictions.parquet"
)
OUTPUT = ROOT / "artifacts/swissai_tail_baselines_v1"
PROTOCOL = ROOT / "reports/swissai_baseline_protocol.md"
PINNED_COMPACT_SHA256 = "d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b"
US = 1_000_000
HOUR_US = 3_600 * US
DAY_US = 24 * HOUR_US
DELAY_US = HOUR_US
DAY0_US = int(datetime(2025, 1, 16, tzinfo=timezone.utc).timestamp() * US)
FIT_END_US = DAY0_US + 30 * DAY_US
TEST_START_US = DAY0_US + 40 * DAY_US
CAPACITY = 2_000
TARGET = 0.9
INPUT_COLUMNS = (
    "created_utc_us", "duration_seconds", "model_code", "status_code", "day",
    "base_p90_seconds", "global_p90_seconds", "model_p90_seconds",
    "guard92_p90_seconds",
)
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_prior_and_warmup(
    compact_path: Path, *, verify_pinned_sha: bool = True,
) -> tuple[float, list[tuple[int, int, float]], dict[str, int]]:
    """Read only requests before test day 40; never inspect test outcomes."""
    if verify_pinned_sha and sha256(compact_path) != PINNED_COMPACT_SHA256:
        raise ValueError("SwissAI compact SHA256 differs from the audited source")
    parquet = pq.ParquetFile(compact_path)
    if not {"created_utc_us", "finished_utc_us"}.issubset(parquet.schema_arrow.names):
        raise ValueError("Compact source lacks required timestamps")
    training: list[np.ndarray] = []
    warmup: list[tuple[int, int, float]] = []
    source_offset = 0
    last_created: int | None = None
    for batch in parquet.iter_batches(batch_size=200_000,
                                      columns=["created_utc_us", "finished_utc_us"]):
        full_created = batch.column(0).to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
        if len(full_created) == 0:
            continue
        if last_created is not None and int(full_created[0]) < last_created:
            raise ValueError("Compact requests are not sorted by creation time")
        if np.any(np.diff(full_created) < 0):
            raise ValueError("Compact requests are not sorted by creation time")
        last_created = int(full_created[-1])
        prefix = int(np.searchsorted(full_created, TEST_START_US, side="left"))
        created = full_created[:prefix]
        if len(created) == 0:
            break
        finished_array = batch.column(1).slice(0, prefix)
        present = pc.is_valid(finished_array).to_numpy(zero_copy_only=False)
        finished = pc.fill_null(finished_array, 0).to_numpy(
            zero_copy_only=False).astype(np.int64, copy=False)
        positive = present & (finished > created)
        eligible = (
            positive & (created >= DAY0_US) & (created < FIT_END_US)
            & (finished + DELAY_US <= FIT_END_US)
        )
        if np.any(eligible):
            training.append((finished[eligible] - created[eligible]) / US)
        initialize = positive & (created >= FIT_END_US) & (created < TEST_START_US)
        for local_index in np.flatnonzero(initialize):
            j = int(local_index)
            warmup.append((int(finished[j]) + DELAY_US, source_offset + j,
                           float((finished[j] - created[j]) / US)))
        source_offset += len(created)
        if prefix < len(full_created):
            break
    if not training:
        raise ValueError("No positive training labels available at day 30")
    labels = np.concatenate(training)
    fallback = float(np.quantile(labels, TARGET, method="linear"))
    if not math.isfinite(fallback) or fallback <= 0:
        raise ValueError("Frozen training p90 is invalid")
    return fallback, warmup, {
        "eligible_training_labels": int(len(labels)),
        "day30_to_day40_positive_warmup_labels": len(warmup),
        "compact_rows_scanned": source_offset,
    }


class EmpiricalDurationP90:
    """Predict from feedback received by each request's creation time."""

    def __init__(self, fallback: float, warmup: list[tuple[int, int, float]],
                 *, capacity: int = CAPACITY, delay_us: int = DELAY_US) -> None:
        if not math.isfinite(fallback) or fallback <= 0 or delay_us < 0:
            raise ValueError("Invalid frozen fallback or feedback delay")
        self.fallback = fallback
        self.delay_us = delay_us
        self.buffer = RollingQuantileBuffer(capacity, TARGET)
        self.pending = list(warmup)
        for _, _, duration in self.pending:
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Warmup labels must be positive and finite")
        heapq.heapify(self.pending)
        self.next_sequence = max((seq for _, seq, _ in self.pending), default=-1) + 1
        self.last_creation: int | None = None

    def predict(self, created_us: int) -> tuple[float, int]:
        now = int(created_us)
        if self.last_creation is not None and now < self.last_creation:
            raise ValueError("Test requests must be sorted by creation time")
        self.last_creation = now
        while self.pending and self.pending[0][0] <= now:
            _, _, duration = heapq.heappop(self.pending)
            self.buffer.add(duration)
        value = self.buffer.value()
        return (self.fallback if value is None else value), len(self.buffer)

    def schedule(self, created_us: int, duration: float) -> None:
        if self.last_creation != int(created_us):
            raise ValueError("A test label can be scheduled only after its prediction")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Test durations must be positive and finite")
        elapsed_us = int(round(float(duration) * US))
        if elapsed_us <= 0:
            raise ValueError("Test duration rounds to zero microseconds")
        available = int(created_us) + elapsed_us + self.delay_us
        heapq.heappush(self.pending, (available, self.next_sequence, float(duration)))
        self.next_sequence += 1


@dataclass
class Score:
    n: int = 0
    covered: int = 0
    loss_sum: float = 0.0
    prediction_sum: float = 0.0

    def add(self, labels: np.ndarray, predictions: np.ndarray) -> None:
        valid = np.isfinite(predictions)
        y = labels[valid]
        q = predictions[valid]
        error = y - q
        self.n += len(y)
        self.covered += int(np.count_nonzero(y <= q))
        self.loss_sum += float(np.sum(np.maximum(TARGET * error, (TARGET - 1) * error)))
        self.prediction_sum += float(np.sum(q))

    def summary(self) -> dict[str, float | int | None]:
        return {
            "n": self.n,
            "coverage": self.covered / self.n if self.n else None,
            "pinball_seconds": self.loss_sum / self.n if self.n else None,
            "mean_prediction_seconds": self.prediction_sum / self.n if self.n else None,
        }


def _scores_for(methods: dict[str, np.ndarray], labels: np.ndarray,
                into: dict[str, Score]) -> None:
    for name, values in methods.items():
        into.setdefault(name, Score()).add(labels, values)


def evaluate(predictions_path: Path = TEST_PREDICTIONS,
             compact_path: Path = COMPACT, output: Path = OUTPUT,
             *, verify_pinned_sha: bool = True) -> dict:
    fallback, warmup, initial = frozen_prior_and_warmup(
        compact_path, verify_pinned_sha=verify_pinned_sha)
    replay = EmpiricalDurationP90(fallback, warmup)
    source = pq.ParquetFile(predictions_path)
    if not set(INPUT_COLUMNS).issubset(source.schema_arrow.names):
        raise ValueError("Saved SwissAI test predictions lack required columns")
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "direct_recent_p90_test_predictions.parquet"
    temporary = result_path.with_suffix(".parquet.tmp")
    schema = pa.schema([
        pa.field("test_row_ordinal", pa.int64(), nullable=False),
        pa.field("created_utc_us", pa.int64(), nullable=False),
        pa.field("duration_seconds", pa.float64(), nullable=False),
        pa.field("model_code", pa.int32(), nullable=False),
        pa.field("status_code", pa.int32(), nullable=False),
        pa.field("day", pa.int32(), nullable=False),
        pa.field("direct_recent_p90_seconds", pa.float64(), nullable=False),
        pa.field("completed_feedback_support", pa.int16(), nullable=False),
    ])
    all_scores: dict[str, Score] = {}
    block_scores: dict[int, dict[str, Score]] = {}
    rows = 0
    fallback_uses = 0
    min_support: int | None = None
    max_support = 0
    last_created: int | None = None
    with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
        for batch in source.iter_batches(batch_size=100_000, columns=list(INPUT_COLUMNS)):
            values = {name: batch.column(i).to_numpy(zero_copy_only=False)
                      for i, name in enumerate(INPUT_COLUMNS)}
            created = np.asarray(values["created_utc_us"], dtype=np.int64)
            labels = np.asarray(values["duration_seconds"], dtype=np.float64)
            day = np.asarray(values["day"], dtype=np.int32)
            if len(created) == 0:
                continue
            if (created[0] < TEST_START_US or (last_created is not None and created[0] < last_created)
                    or np.any(np.diff(created) < 0)):
                raise ValueError("Test predictions are not chronological day-40-onward requests")
            last_created = int(created[-1])
            if (np.any(~np.isfinite(labels)) or np.any(labels <= 0)
                    or np.any(day < 40)
                    or np.any(day != (created - DAY0_US) // DAY_US)):
                raise ValueError("Test prediction labels or day numbers violate the protocol")
            direct = np.empty(len(created), dtype=np.float64)
            support = np.empty(len(created), dtype=np.int16)
            for j in range(len(created)):
                q, n = replay.predict(int(created[j]))
                direct[j] = q
                support[j] = n
                replay.schedule(int(created[j]), float(labels[j]))
            fallback_uses += int(np.count_nonzero(support == 0))
            min_support = int(support.min()) if min_support is None else min(min_support, int(support.min()))
            max_support = max(max_support, int(support.max()))
            methods = {
                "direct_recent_duration": direct,
                "base": np.asarray(values["base_p90_seconds"], dtype=np.float64),
                "global_residual": np.asarray(values["global_p90_seconds"], dtype=np.float64),
                "model_residual": np.asarray(values["model_p90_seconds"], dtype=np.float64),
                "guard92": np.asarray(values["guard92_p90_seconds"], dtype=np.float64),
            }
            for name in ("base", "global_residual", "model_residual"):
                if np.any(~np.isfinite(methods[name])) or np.any(methods[name] < 0):
                    raise ValueError(f"Saved {name} predictions are invalid")
            guard_valid = np.isfinite(methods["guard92"])
            for name in ("direct_recent_duration", "global_residual", "model_residual"):
                methods[f"{name}_on_guard_valid"] = np.where(
                    guard_valid, methods[name], np.nan)
            _scores_for(methods, labels, all_scores)
            block = 40 + 10 * ((day - 40) // 10)
            for block_id in np.unique(block):
                mask = block == block_id
                _scores_for({name: q[mask] for name, q in methods.items()},
                            labels[mask], block_scores.setdefault(int(block_id), {}))
            writer.write_table(pa.Table.from_arrays([
                pa.array(np.arange(rows, rows + len(created), dtype=np.int64)),
                pa.array(created, type=pa.int64()),
                pa.array(labels, type=pa.float64()),
                pa.array(values["model_code"], type=pa.int32()),
                pa.array(values["status_code"], type=pa.int32()),
                pa.array(day, type=pa.int32()),
                pa.array(direct, type=pa.float64()),
                pa.array(support, type=pa.int16()),
            ], schema=schema))
            rows += len(created)
    if rows != source.metadata.num_rows:
        raise ValueError("Saved prediction row count changed during replay")
    os.replace(temporary, result_path)
    if pq.ParquetFile(result_path).metadata.num_rows != rows:
        raise ValueError("Direct baseline output has an incomplete row count")
    metrics = {
        "scope": "exploratory after original independent-validation quality-gate failure",
        "primary_validation_valid": False,
        "method": "direct_recent_2000_completed_durations_empirical_p90",
        "day0_utc": "2025-01-16T00:00:00Z",
        "feedback_delay_hours": 1,
        "training_fallback_p90_seconds": fallback,
        "initialization": initial,
        "test_rows": rows,
        "fallback_uses": fallback_uses,
        "test_support_min": min_support,
        "test_support_max": max_support,
        "scores_all": {name: value.summary() for name, value in all_scores.items()},
        "scores_by_10day_block": {
            str(block): {name: value.summary() for name, value in scores.items()}
            for block, scores in sorted(block_scores.items())
        },
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "compact_sha256": sha256(compact_path),
        "source_predictions_sha256": sha256(predictions_path),
        "direct_predictions_file": str(result_path),
        "direct_predictions_sha256": sha256(result_path),
    }
    metrics_path = output / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=TEST_PREDICTIONS)
    parser.add_argument("--compact", type=Path, default=COMPACT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = evaluate(args.predictions, args.compact, args.output)
    print(json.dumps({
        "test_rows": result["test_rows"],
        "scores_all": result["scores_all"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
