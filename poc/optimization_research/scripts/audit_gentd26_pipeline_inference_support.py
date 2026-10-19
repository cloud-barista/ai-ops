"""Read-only support audit for fixed-time GenTD26 pipeline-latency forecasts.

This counts observable labels only. It never fits or scores a predictor, and
the public trace contains no AI-App deployment or scaling action outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


DATA = Path(__file__).resolve().parents[1] / "data/external/gentd26"
EXPECTED_SHA256 = {
    "README.md": "389a0cb2da5212bdccbf3fc00bffd9850b6c0142a5855167dcef350083e526fe",
    "pipeline_inference_data_anon.csv": "6d34855c401258ed435cb8213e70d386b9945d052e665638adc3752cc61a6fd4",
    "model_predict_data_anon.csv": "002bf8800ac7aa0cfc8abf06af73f475ae3f91ff107b62834bf93fe1e1d110a5",
}
TRAIN_HOURS = 8
VALID_HOURS = 4
HISTORY_SECONDS = 3600
AVAILABILITY_LAG_SECONDS = 300
MIN_HISTORY_SAMPLES = 5
FUTURE_SAMPLE_THRESHOLDS = (1, 3, 5, 10)
HORIZONS_SECONDS = (600, 1800)
SPLITS = ("train", "validation", "test")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metric(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"timestamp_anon", "value", "container_ip"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{path.name}: missing columns {sorted(required - set(frame))}")
    frame = frame[["timestamp_anon", "value", "container_ip"]]
    if (
        frame.empty
        or frame.isna().any().any()
        or not np.isfinite(frame.timestamp_anon.to_numpy(float)).all()
        or not np.isfinite(frame.value.to_numpy(float)).all()
        or frame.value.lt(0).any()
        or frame.container_ip.eq("").any()
        or frame.duplicated(["container_ip", "timestamp_anon"]).any()
    ):
        raise ValueError(f"{path.name}: invalid, empty, or duplicated metric records")
    return frame


def split_of(decision: float, future_end: float, first_time: float) -> str | None:
    """A label belongs to a split only when its entire future window does."""
    train_end = first_time + TRAIN_HOURS * 3600
    valid_end = train_end + VALID_HOURS * 3600
    if decision < train_end:
        return "train" if future_end <= train_end else None
    if decision < valid_end:
        return "validation" if future_end <= valid_end else None
    return "test"


def window_counts(sorted_times: np.ndarray, decision: float, horizon: int) -> tuple[int, int]:
    """Count [t-60m,t-5m] history and (t,t+horizon] outcome samples."""
    history_n = int(
        np.searchsorted(sorted_times, decision - AVAILABILITY_LAG_SECONDS, side="right")
        - np.searchsorted(sorted_times, decision - HISTORY_SECONDS, side="left")
    )
    future_n = int(
        np.searchsorted(sorted_times, decision + horizon, side="right")
        - np.searchsorted(sorted_times, decision, side="right")
    )
    return history_n, future_n


def support_by_split(pipeline: pd.DataFrame, horizon: int) -> dict[str, dict]:
    if horizon <= 0:
        raise ValueError("Horizon must be positive")
    first_time = float(pipeline.timestamp_anon.min())
    last_time = float(pipeline.timestamp_anon.max())
    times_by_container = {
        str(container): np.sort(group.timestamp_anon.to_numpy(float))
        for container, group in pipeline.groupby("container_ip")
    }
    rows = []
    for decision in np.arange(
        first_time + HISTORY_SECONDS, last_time - horizon + 1, horizon, dtype=float
    ):
        split = split_of(decision, decision + horizon, first_time)
        if split is None:
            continue
        for container, times in times_by_container.items():
            history_n, future_n = window_counts(times, decision, horizon)
            if history_n >= MIN_HISTORY_SAMPLES:
                rows.append((split, container, decision, future_n))
    eligible = pd.DataFrame(rows, columns=["split", "container", "decision", "future_n"])
    result = {}
    for split in SPLITS:
        part = eligible.loc[eligible.split.eq(split)]
        summary = {"history_supported_windows": int(len(part))}
        for minimum in FUTURE_SAMPLE_THRESHOLDS:
            observed = part.loc[part.future_n.ge(minimum)]
            summary[f"future_n_ge_{minimum}"] = {
                "windows": int(len(observed)),
                "containers": int(observed.container.nunique()),
                "nonoverlapping_time_blocks": int(observed.decision.nunique()),
            }
        result[split] = summary
    return result


def join_diagnostic(pipeline: pd.DataFrame, pure: pd.DataFrame) -> dict[str, int]:
    paired = pipeline.merge(
        pure,
        on=["container_ip", "timestamp_anon"],
        suffixes=("_pipeline", "_pure"),
        validate="one_to_one",
    )
    a = paired.value_pipeline.to_numpy(float)
    b = paired.value_pure.to_numpy(float)
    return {
        "matched_keys": int(len(paired)),
        "pipeline_less_than_pure": int(np.count_nonzero(a < b)),
        "pipeline_equal_pure": int(np.count_nonzero(a == b)),
        "pipeline_greater_than_pure": int(np.count_nonzero(a > b)),
    }


def global_future_blocks(pipeline: pd.DataFrame, horizon: int, min_future: int = 10) -> dict[str, int]:
    """Count nonoverlapping pooled outcome blocks; no per-container input gate."""
    times = np.sort(pipeline.timestamp_anon.to_numpy(float))
    first_time = float(times[0])
    last_time = float(times[-1])
    counts = {split: 0 for split in SPLITS}
    for decision in np.arange(
        first_time + HISTORY_SECONDS, last_time - horizon + 1, horizon, dtype=float
    ):
        split = split_of(decision, decision + horizon, first_time)
        if split is None:
            continue
        future_n = int(
            np.searchsorted(times, decision + horizon, side="right")
            - np.searchsorted(times, decision, side="right")
        )
        if future_n >= min_future:
            counts[split] += 1
    return counts


def audit(data_dir: Path = DATA, verify_hashes: bool = True) -> dict:
    source_hashes = {name: sha256(data_dir / name) for name in EXPECTED_SHA256}
    if verify_hashes:
        for name, expected in EXPECTED_SHA256.items():
            if source_hashes[name] != expected:
                raise ValueError(f"{name}: SHA-256 mismatch")
    pipeline = load_metric(data_dir / "pipeline_inference_data_anon.csv")
    pure = load_metric(data_dir / "model_predict_data_anon.csv")
    first_time = float(pipeline.timestamp_anon.min())
    last_time = float(pipeline.timestamp_anon.max())
    hour_counts = (
        ((pipeline.timestamp_anon - first_time) // 3600)
        .astype(int).value_counts().sort_index()
    )
    return {
        "source_sha256": source_hashes,
        "trace": {
            "pipeline_rows": int(len(pipeline)),
            "pipeline_containers": int(pipeline.container_ip.nunique()),
            "first_timestamp_anon": first_time,
            "last_timestamp_anon": last_time,
            "hours": (last_time - first_time) / 3600,
            "pipeline_rows_by_hour": {str(k): int(v) for k, v in hour_counts.items()},
        },
        "same_key_join": join_diagnostic(pipeline, pure),
        "protocol": {
            "train_hours": TRAIN_HOURS,
            "validation_hours": VALID_HOURS,
            "history_seconds": HISTORY_SECONDS,
            "availability_lag_seconds": AVAILABILITY_LAG_SECONDS,
            "min_history_samples": MIN_HISTORY_SAMPLES,
            "future_sample_thresholds": FUTURE_SAMPLE_THRESHOLDS,
            "nonoverlapping_horizons_seconds": HORIZONS_SECONDS,
        },
        "support": {
            str(horizon): support_by_split(pipeline, horizon)
            for horizon in HORIZONS_SECONDS
        },
        "global_future_blocks_ge_10": {
            str(horizon): global_future_blocks(pipeline, horizon)
            for horizon in HORIZONS_SECONDS
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    print(json.dumps(audit(args.data_dir), ensure_ascii=False, indent=2, sort_keys=True))
