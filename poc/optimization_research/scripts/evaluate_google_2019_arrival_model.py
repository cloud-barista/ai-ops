"""Frozen exploratory forecast of next-bin Google 2019 cell-a JOB arrivals.

Extracting requires all ten official collection-event objects. No model score is
computed until a separate freeze file pins the source, script, protocol, and
extracted series. Borg arrivals are not AI-MCMP scale-action outcomes.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import math
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data" / "external" / "google_cluster_2019_a"
ARTIFACT = RESEARCH / "artifacts" / "google_2019_arrival_model"
PROTOCOL = RESEARCH / "reports" / "google_2019_arrival_model_protocol_v1.md"
OBJECT_LIST = SOURCE / "collection_events_object_manifest.json"
VERIFIED = SOURCE / "verified_collection_events_manifest.json"
SCHEMA = SOURCE / "collection_events.schema.json"
SERIES = ARTIFACT / "series.json"
FREEZE = ARTIFACT / "freeze.json"
RESULTS = ARTIFACT / "results.json"
PREDICTIONS = ARTIFACT / "predictions.csv"
EXPECTED_OBJECT_LIST_SHA256 = "452c60360ff98db27d10417494361a664c7ef34da229ef3093e9df9149f12f89"
EXPECTED_SCHEMA_SHA256 = "63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3"
EXPECTED_FIRST_SHA256 = "2288aa6bb4ef5157a68ca73719685b5bf438edcc85ef5ea5eb472c294cb271d4"
FIVE_MIN_US = 300_000_000
MAX_LAG = 288
BASELINE_NAMES = ("persistence", "mean_12", "mean_288", "daily_lag")
MODEL_KWARGS = dict(loss="poisson", max_iter=120, learning_rate=0.05,
                    max_leaf_nodes=15, min_samples_leaf=50,
                    l2_regularization=1.0, early_stopping=False,
                    random_state=42)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> tuple[list[Path], str]:
    """Fail closed until exactly ten pinned, verified, locally intact shards exist."""
    if sha256(OBJECT_LIST) != EXPECTED_OBJECT_LIST_SHA256:
        raise ValueError("official object metadata manifest changed")
    if sha256(SCHEMA) != EXPECTED_SCHEMA_SHA256:
        raise ValueError("official schema checksum mismatch")
    if not VERIFIED.exists():
        raise ValueError("all ten shards have not been verified")
    expected = json.loads(OBJECT_LIST.read_text())["objects"]
    observed = json.loads(VERIFIED.read_text())["objects"]
    names = [f"collection_events-{index:012d}.json.gz" for index in range(10)]
    if [entry.get("name") for entry in expected] != names:
        raise ValueError("official object list is not the required ten shards")
    if [entry.get("name") for entry in observed] != names:
        raise ValueError("verified manifest does not contain exactly ten shards")
    files = []
    for index, (meta, local) in enumerate(zip(expected, observed, strict=True)):
        for key in ("name", "bytes", "generation", "md5_base64", "crc32c_base64"):
            if local.get(key) != meta[key]:
                raise ValueError(f"verified manifest {key} differs from GCS metadata: {meta['name']}")
        path = SOURCE / meta["name"]
        if path.stat().st_size != meta["bytes"]:
            raise ValueError(f"compressed size mismatch: {path.name}")
        if not isinstance(local.get("sha256"), str) or len(local["sha256"]) != 64:
            raise ValueError(f"missing local SHA-256: {path.name}")
        if index == 0 and local["sha256"] != EXPECTED_FIRST_SHA256:
            raise ValueError("first-shard SHA-256 changed")
        md5 = hashlib.md5()
        shasum = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                md5.update(block)
                shasum.update(block)
        if base64.b64encode(md5.digest()).decode() != meta["md5_base64"]:
            raise ValueError(f"GCS MD5 mismatch: {path.name}")
        if shasum.hexdigest() != local["sha256"]:
            raise ValueError(f"local SHA-256 mismatch: {path.name}")
        files.append(path)
    return files, sha256(VERIFIED)


def valid_int(value: object, field: str) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"missing, negative, or non-integer {field}")
    return value


def extract_event_rows(paths: list[Path], scratch: Path) -> dict:
    """Stream unordered source events and deduplicate by minimum SUBMIT time."""
    if len(paths) != 10:
        raise ValueError("exactly ten verified shards are required")
    db_path = scratch / "first_submits.sqlite3"
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE first_submit (collection_id INTEGER PRIMARY KEY, "
                    "first_time_us INTEGER NOT NULL, event_rows INTEGER NOT NULL, "
                    "shard_mask INTEGER NOT NULL)")
        upsert = ("INSERT INTO first_submit VALUES (?, ?, 1, ?) "
                  "ON CONFLICT(collection_id) DO UPDATE SET "
                  "first_time_us=MIN(first_time_us, excluded.first_time_us), "
                  "event_rows=event_rows+1, shard_mask=shard_mask|excluded.shard_mask")
        audit = Counter()
        min_time, max_time = None, None
        for shard, path in enumerate(paths):
            batch = []
            with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
                for line in stream:
                    row = json.loads(line)
                    audit["all_collection_event_rows"] += 1
                    timestamp = valid_int(row.get("time"), "time")
                    event_type = valid_int(row.get("type"), "type")
                    collection_type = valid_int(row.get("collection_type"), "collection_type")
                    collection_id = valid_int(row.get("collection_id"), "collection_id")
                    if collection_id >= 2**63:
                        raise ValueError("collection_id exceeds SQLite signed INTEGER range")
                    min_time = timestamp if min_time is None else min(min_time, timestamp)
                    max_time = timestamp if max_time is None else max(max_time, timestamp)
                    if collection_type != 0 or event_type != 0:
                        continue
                    audit["raw_job_submit_rows"] += 1
                    if row.get("missing_type") is not None:
                        audit["excluded_job_submit_missing_type_rows"] += 1
                        continue
                    audit["valid_job_submit_rows"] += 1
                    batch.append((collection_id, timestamp, 1 << shard))
                    if len(batch) >= 10000:
                        con.executemany(upsert, batch)
                        con.commit()
                        batch.clear()
            if batch:
                con.executemany(upsert, batch)
                con.commit()
        if min_time is None or max_time is None:
            raise ValueError("no collection events")
        first_bin = min_time // FIVE_MIN_US + 1
        last_bin = max_time // FIVE_MIN_US - 1
        if last_bin < first_bin:
            raise ValueError("no complete 5-minute bins")
        counts = [0] * (last_bin - first_bin + 1)
        for bin_id, count in con.execute(
            "SELECT first_time_us / ?, COUNT(*) FROM first_submit GROUP BY first_time_us / ?",
            (FIVE_MIN_US, FIVE_MIN_US),
        ):
            if first_bin <= bin_id <= last_bin:
                counts[bin_id - first_bin] = count
        unique, duplicate_ids, extra_submit_rows, cross_shard_ids = con.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN event_rows>1 THEN 1 ELSE 0 END), "
            "SUM(event_rows-1), "
            "SUM(CASE WHEN (shard_mask & (shard_mask-1)) != 0 THEN 1 ELSE 0 END) "
            "FROM first_submit"
        ).fetchone()
        if audit["valid_job_submit_rows"] != unique + extra_submit_rows:
            raise AssertionError("SUBMIT deduplication total differs from source rows")
        return {
            "version": 1,
            "target": "first_valid_JOB_collection_SUBMIT_count_per_complete_5m_bin",
            "time_min_us": min_time,
            "time_max_us": max_time,
            "first_full_bin": first_bin,
            "last_full_bin": last_bin,
            "counts": counts,
            "audit": {**dict(sorted(audit.items())),
                      "unique_job_submit_collection_ids": unique,
                      "duplicate_job_submit_collection_ids": duplicate_ids,
                      "extra_valid_submit_rows_after_dedup": extra_submit_rows,
                      "collection_ids_with_submit_in_multiple_shards": cross_shard_ids,
                      "complete_5m_bins": len(counts),
                      "zero_job_submit_bins": sum(value == 0 for value in counts)},
        }
    finally:
        con.close()


def split_offsets(series: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    counts = series["counts"]
    if series["last_full_bin"] - series["first_full_bin"] + 1 != len(counts):
        raise ValueError("series bin boundaries do not match count length")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise ValueError("series contains invalid counts")
    eligible = len(counts) - MAX_LAG
    n_train = math.floor(eligible * 0.6)
    n_validation = math.floor(eligible * 0.2)
    n_test = eligible - n_train - n_validation
    if min(n_train, n_validation, n_test) < MAX_LAG:
        raise ValueError("insufficient complete bins for frozen temporal blocks")
    train = np.arange(MAX_LAG, MAX_LAG + n_train)
    validation = np.arange(MAX_LAG + n_train, MAX_LAG + n_train + n_validation)
    test = np.arange(MAX_LAG + n_train + n_validation, len(counts))
    return train, validation, test


def features_before(counts: list[int], offset: int, bin_id: int) -> list[float]:
    if offset < MAX_LAG or offset >= len(counts):
        raise ValueError("feature offset has no full history or target")
    prior = np.asarray(counts[offset - MAX_LAG:offset], dtype=float)
    if prior.size != MAX_LAG:
        raise AssertionError("future or incomplete history reached feature builder")
    lags = [float(prior[-lag]) for lag in (1, 2, 3, 6, 12, 24, 288)]
    means = [float(np.mean(prior[-width:])) for width in (3, 12, 24, 288)]
    stds = [float(np.std(prior[-width:], ddof=0)) for width in (12, 288)]
    phase = 2 * math.pi * (bin_id % 288) / 288
    return lags + means + stds + [math.sin(phase), math.cos(phase)]


def baseline_predictions(counts: list[int], offset: int) -> dict[str, float]:
    if offset < MAX_LAG or offset >= len(counts):
        raise ValueError("baseline offset has no full history or target")
    return {
        "persistence": float(counts[offset - 1]),
        "mean_12": float(sum(counts[offset - 12:offset]) / 12),
        "mean_288": float(sum(counts[offset - 288:offset]) / 288),
        "daily_lag": float(counts[offset - 288]),
    }


def metrics(actual: np.ndarray, predicted: np.ndarray, high_threshold: float) -> dict:
    errors = np.abs(actual - predicted)
    positives = actual >= high_threshold
    return {
        "bins": int(actual.size),
        "mae": float(np.mean(errors)),
        "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
        "wape": float(np.sum(errors) / np.sum(actual)) if np.sum(actual) > 0 else None,
        "p90_absolute_error": float(np.quantile(errors, .9, method="higher")),
        "high_load_true_bins": int(np.sum(positives)),
        "high_load_alert_bins": int(np.sum(predicted >= high_threshold)),
        "high_load_recall": float(np.mean(predicted[positives] >= high_threshold))
        if np.any(positives) else None,
    }


def read_freeze(manifest_sha: str) -> dict:
    frozen = json.loads(FREEZE.read_text())
    expected = {
        "source_manifest_sha256": manifest_sha,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "series_sha256": sha256(SERIES),
    }
    if frozen != expected:
        raise ValueError("freeze file differs from source, protocol, script, or extracted series")
    return frozen


def evaluate(series: dict, frozen: dict) -> tuple[dict, list[dict]]:
    counts = series["counts"]
    first_bin = series["first_full_bin"]
    train, validation, test = split_offsets(series)
    offsets = np.concatenate((train, validation, test))
    x = np.asarray([features_before(counts, int(offset), first_bin + int(offset))
                    for offset in offsets], dtype=float)
    actual = np.asarray([counts[int(offset)] for offset in offsets], dtype=float)
    baseline = {name: np.asarray([baseline_predictions(counts, int(offset))[name]
                                  for offset in offsets], dtype=float)
                for name in BASELINE_NAMES}
    n_train, n_val = len(train), len(validation)
    train_slice = slice(0, n_train)
    val_slice = slice(n_train, n_train + n_val)
    test_slice = slice(n_train + n_val, len(offsets))
    with threadpool_limits(limits=1):
        model = HistGradientBoostingRegressor(**MODEL_KWARGS)
        model.fit(x[train_slice], actual[train_slice])
        model_pred = np.maximum(0.0, model.predict(x))
    predictions = {**baseline, "model": model_pred}
    high_threshold = float(np.quantile(actual[train_slice], .9, method="higher"))
    validation_metrics = {name: metrics(actual[val_slice], pred[val_slice], high_threshold)
                          for name, pred in predictions.items()}
    selected = min(BASELINE_NAMES, key=lambda name: validation_metrics[name]["mae"])
    test_metrics = {name: metrics(actual[test_slice], pred[test_slice], high_threshold)
                    for name, pred in predictions.items()}
    candidate = test_metrics["model"]
    champion = test_metrics[selected]
    gate = {
        "mae_at_least_5_percent_better": candidate["mae"] <= .95 * champion["mae"],
        "p90_absolute_error_not_worse": candidate["p90_absolute_error"] <= champion["p90_absolute_error"],
        "high_load_recall_not_worse": (
            candidate["high_load_recall"] is not None
            and champion["high_load_recall"] is not None
            and candidate["high_load_recall"] >= champion["high_load_recall"]),
    }
    daily = []
    for day in sorted({(first_bin + int(offset)) // 288 for offset in test}):
        positions = [position for position, offset in enumerate(offsets)
                     if position >= n_train + n_val and (first_bin + int(offset)) // 288 == day]
        daily.append({
            "trace_day_index": day,
            "bins": len(positions),
            "job_submits": int(sum(actual[positions])),
            "model_mae": float(np.mean(np.abs(actual[positions] - model_pred[positions]))),
            "selected_baseline_mae": float(np.mean(np.abs(actual[positions] - baseline[selected][positions]))),
        })
    rows = []
    for position in range(n_train, len(offsets)):
        offset = int(offsets[position])
        bin_id = first_bin + offset
        rows.append({
            "split": "validation" if position < n_train + n_val else "test",
            "bin_id": bin_id,
            "trace_start_us": bin_id * FIVE_MIN_US,
            "actual": int(actual[position]),
            **{name: float(pred[position]) for name, pred in predictions.items()},
        })
    result = {
        "status": "exploratory_external_workload_only",
        "target": series["target"],
        "frozen_sha256": frozen,
        "software": {"python": sys.version.split()[0], "numpy": np.__version__,
                     "sklearn": sklearn.__version__},
        "model": {"name": "HistGradientBoostingRegressor", "parameters": MODEL_KWARGS},
        "splits": {name: {"bins": int(len(values)), "first_bin": first_bin + int(values[0]),
                           "last_bin": first_bin + int(values[-1])}
                   for name, values in (("train", train), ("validation", validation), ("test", test))},
        "high_load_threshold_train_p90": high_threshold,
        "validation_selected_baseline": selected,
        "validation": validation_metrics,
        "test": test_metrics,
        "test_daily": daily,
        "research_gate": {**gate, "passed": all(gate.values())},
        "source_audit": series["audit"],
    }
    return result, rows


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "evaluate"))
    args = parser.parse_args()
    paths, manifest_sha = verify_inputs()
    if args.command == "extract":
        ARTIFACT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="google19_arrival_", dir=ARTIFACT) as directory:
            series = extract_event_rows(paths, Path(directory))
        series["source_manifest_sha256"] = manifest_sha
        write_json(SERIES, series)
        print(json.dumps({"series": str(SERIES), "sha256": sha256(SERIES),
                          "audit": series["audit"]}, sort_keys=True))
        return
    series = json.loads(SERIES.read_text())
    if series.get("source_manifest_sha256") != manifest_sha:
        raise ValueError("series was not extracted from the current verified manifest")
    frozen = read_freeze(manifest_sha)
    result, rows = evaluate(series, frozen)
    write_json(RESULTS, result)
    with PREDICTIONS.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("split", "bin_id", "trace_start_us", "actual",
                                                     *BASELINE_NAMES, "model"))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"results": str(RESULTS), "predictions": str(PREDICTIONS),
                      "selected_baseline": result["validation_selected_baseline"],
                      "research_gate": result["research_gate"]}, sort_keys=True))


if __name__ == "__main__":
    main()
