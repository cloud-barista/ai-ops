"""Pre-frozen transfer of an online-calibrated cell-a arrival model to cell-b."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import evaluate_google_2019_arrival_model as old
from scripts import prepare_google_2019_cell_b as source


SOURCE_DIR = ROOT / "data/external/google_cluster_2019_b"
OBJECT_LIST = SOURCE_DIR / "collection_events_object_manifest.json"
SCHEMA = SOURCE_DIR / "collection_events.schema.json"
OUTPUT = ROOT / "artifacts/google_2019_cell_b_adaptive_v1"
FREEZE = OUTPUT / "freeze.json"
SERIES = OUTPUT / "series.json"
RESULTS = OUTPUT / "results.json"
PREDICTIONS = OUTPUT / "predictions.csv"
PROTOCOL = ROOT / "reports/google_2019_cell_b_adaptive_protocol_v1.md"
EXPECTED_OBJECT_SHA = "6204a1942f388a5c182da7945ed981673b74b4d6e9e58191ea572372e5e6e773"
EXPECTED_SCHEMA_SHA = "63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3"
EXPECTED_OLD_FREEZE_SHA = "5b260980c471e2282856e6ef8d5a675e8f62fd17c044816f45b5e1bc4143fe07"
EXPECTED_OLD_SERIES_SHA = "bff66403914c23e7086055ee415fd638bd9fca4025eb977c210b6506dc5b993a"
ALPHA = 0.25
BURN_IN = 300
MIN_FULL_BINS = 1164
METHODS = ("persistence", "mean_12", "mean_288", "daily_lag", "fixed_hgb", "adaptive_hgb")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def verify_metadata() -> None:
    if sha256(OBJECT_LIST) != EXPECTED_OBJECT_SHA or sha256(SCHEMA) != EXPECTED_SCHEMA_SHA:
        raise ValueError("Official cell-b object metadata or common schema changed")
    manifest = json.loads(OBJECT_LIST.read_text())
    expected = [f"collection_events-{index:012d}.json.gz" for index in range(4)]
    if manifest.get("bucket") != "clusterdata_2019_b" or [
            entry.get("name") for entry in manifest.get("objects", [])] != expected:
        raise ValueError("cell-b metadata does not list the exact four JSON objects")
    if sum(entry["bytes"] for entry in manifest["objects"]) != 508_666_112:
        raise ValueError("cell-b compressed byte total changed")
    if any(not all(entry.get(key) for key in ("name", "bytes", "generation", "md5_base64", "crc32c_base64"))
           for entry in manifest["objects"]):
        raise ValueError("cell-b GCS metadata field missing")


def verify_old_study() -> dict:
    if sha256(old.FREEZE) != EXPECTED_OLD_FREEZE_SHA or sha256(old.SERIES) != EXPECTED_OLD_SERIES_SHA:
        raise ValueError("original cell-a freeze or series changed")
    paths, manifest_sha = old.verify_inputs()
    if len(paths) != 10:
        raise ValueError("original cell-a source incomplete")
    old.read_freeze(manifest_sha)
    series = json.loads(old.SERIES.read_text())
    if series.get("source_manifest_sha256") != manifest_sha:
        raise ValueError("original cell-a series source mismatch")
    return series


def freeze_payload() -> dict:
    verify_metadata()
    verify_old_study()
    if sklearn.__version__ != "1.5.2":
        raise ValueError("scikit-learn version differs from fixed cell-a model")
    return {
        "cell_b_object_manifest_sha256": sha256(OBJECT_LIST),
        "cell_b_schema_sha256": sha256(SCHEMA),
        "cell_b_extractor_sha256": sha256(Path(source.__file__)),
        "source_metadata_sha256": sha256(OBJECT_LIST),
        "prepare_script_sha256": sha256(Path(source.__file__)),
        "cell_a_freeze_sha256": sha256(old.FREEZE),
        "cell_a_series_sha256": sha256(old.SERIES),
        "cell_a_script_sha256": sha256(Path(old.__file__)),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "alpha": ALPHA,
        "burn_in_bins": BURN_IN,
        "expected_json_objects": 4,
    }


def freeze() -> None:
    contents = freeze_payload()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if FREEZE.exists():
        if json.loads(FREEZE.read_text()) != contents:
            raise ValueError("Existing pre-score freeze differs from current source/code/protocol")
        print(f"Verified existing freeze: {FREEZE}")
        return
    write_json(FREEZE, contents)
    print(f"Wrote pre-content freeze {FREEZE}, sha256={sha256(FREEZE)}")


def verify_freeze() -> dict:
    if not FREEZE.exists():
        raise ValueError("cell-b pre-content freeze missing")
    frozen = json.loads(FREEZE.read_text())
    if frozen != freeze_payload():
        raise ValueError("cell-b pre-content freeze no longer matches data, code, or protocol")
    return frozen


def fit_cell_a(series: dict) -> HistGradientBoostingRegressor:
    train, validation, test = old.split_offsets(series)
    if len(train) != 5184 or len(validation) != 1728 or len(test) != 1728:
        raise ValueError("Original cell-a split changed")
    counts = series["counts"]
    first = series["first_full_bin"]
    features = np.asarray([old.features_before(counts, int(offset), first + int(offset))
                           for offset in train], dtype=float)
    actual = np.asarray([counts[int(offset)] for offset in train], dtype=float)
    with threadpool_limits(limits=1):
        model = HistGradientBoostingRegressor(**old.MODEL_KWARGS)
        model.fit(features, actual)
    return model


def check_cell_a_reproduction(model: HistGradientBoostingRegressor, series: dict) -> None:
    counts = series["counts"]
    first = series["first_full_bin"]
    with old.PREDICTIONS.open(newline="") as stream:
        original_predictions = list(csv.DictReader(stream))
    if len(original_predictions) != 3456:
        raise ValueError("Original validation/test predictions missing")
    offsets = [int(row["bin_id"]) - first for row in original_predictions]
    if any(offset < old.MAX_LAG or offset >= len(counts) for offset in offsets):
        raise ValueError("Original prediction bin outside cell-a series")
    features = np.asarray([old.features_before(counts, offset, first + offset)
                           for offset in offsets], dtype=float)
    with threadpool_limits(limits=1):
        reproduced = np.maximum(0, model.predict(features))
    recorded = np.asarray([float(row["model"]) for row in original_predictions])
    if not np.allclose(reproduced, recorded, rtol=1e-10, atol=1e-8):
        raise ValueError("Retrained cell-a model does not reproduce frozen predictions")


def score_series(series: dict, model: HistGradientBoostingRegressor) -> tuple[dict, list[dict]]:
    counts = series["counts"]
    first = series["first_full_bin"]
    if (series["last_full_bin"] - first + 1 != len(counts) or
            len(counts) < MIN_FULL_BINS or
            any(type(value) is not int or value < 0 for value in counts)):
        raise ValueError("cell-b series is incomplete or malformed")
    offsets = np.arange(old.MAX_LAG, len(counts), dtype=int)
    features = np.asarray([old.features_before(counts, int(offset), first + int(offset))
                           for offset in offsets], dtype=float)
    with threadpool_limits(limits=1):
        fixed = np.maximum(0.0, model.predict(features))
    hgb = np.full(len(counts), np.nan, dtype=float)
    hgb[offsets] = fixed
    methods: dict[str, list[float]] = {name: [] for name in METHODS}
    rows = []
    for offset in range(BURN_IN, len(counts)):
        baseline = old.baseline_predictions(counts, offset)
        previous_hgb = hgb[offset - 12:offset]
        if not np.isfinite(previous_hgb).all():
            raise ValueError("Online correction reached future or unavailable HGB prediction")
        correction = ALPHA * (hgb[offset] - float(np.mean(previous_hgb)))
        adaptive = max(0.0, baseline["mean_12"] + correction)
        values = {**baseline, "fixed_hgb": float(hgb[offset]), "adaptive_hgb": adaptive}
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("Invalid cell-b forecast")
        for name in METHODS:
            methods[name].append(values[name])
        bin_id = first + offset
        rows.append({"bin_id": bin_id, "trace_start_us": bin_id * old.FIVE_MIN_US,
                     "actual": int(counts[offset]), **values})
    actual = np.asarray([row["actual"] for row in rows], dtype=float)
    threshold = float(np.quantile(counts[:BURN_IN], .9, method="higher"))
    positives = int(np.sum(actual >= threshold))
    metrics = {}
    for name in METHODS:
        predicted = np.asarray(methods[name], dtype=float)
        metrics[name] = {**old.metrics(actual, predicted, threshold),
                         "mean_prediction": float(np.mean(predicted))}
    baseline = metrics["mean_12"]
    candidate = metrics["adaptive_hgb"]
    gate = {
        "positive_high_load_threshold": threshold > 0,
        "at_least_100_high_load_test_bins": positives >= 100,
        "mae_at_least_2pct_better": candidate["mae"] <= 0.98 * baseline["mae"],
        "p90_absolute_error_not_worse": candidate["p90_absolute_error"] <= baseline["p90_absolute_error"],
        "high_load_recall_within_1pp": (
            baseline["high_load_recall"] is not None and
            candidate["high_load_recall"] is not None and
            candidate["high_load_recall"] >= baseline["high_load_recall"] - 0.01),
    }
    daily = []
    for day in sorted({row["bin_id"] // 288 for row in rows}):
        day_rows = [row for row in rows if row["bin_id"] // 288 == day]
        daily.append({"trace_day_index": day, "bins": len(day_rows),
                      "job_submits": sum(row["actual"] for row in day_rows),
                      "mean_12_mae": float(np.mean([abs(row["actual"] - row["mean_12"])
                                                     for row in day_rows])),
                      "adaptive_hgb_mae": float(np.mean([abs(row["actual"] - row["adaptive_hgb"])
                                                          for row in day_rows]))})
    result = {
        "scope": "independent Borg cell-b JOB arrival pressure; no AI-App action or user SLO labels",
        "target": series["target"],
        "source_audit": series["audit"],
        "full_bins": len(counts),
        "burn_in_bins": BURN_IN,
        "test_bins": len(rows),
        "high_load_threshold_burn_in_p90": threshold,
        "high_load_test_bins": positives,
        "alpha_fixed_on_exposed_cell_a": ALPHA,
        "metrics": metrics,
        "daily": daily,
        "research_gate": {**gate, "passed": all(gate.values())},
    }
    return result, rows


def evaluate() -> None:
    frozen = verify_freeze()
    paths, manifest_sha = source.verify_inputs()
    if len(paths) != 4:
        raise ValueError("cell-b source has fewer than four verified JSON objects")
    cell_a = verify_old_study()
    model = fit_cell_a(cell_a)
    check_cell_a_reproduction(model, cell_a)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_b_arrival_", dir=OUTPUT) as directory:
        series = source.extract_event_rows(paths, Path(directory))
    series["source_manifest_sha256"] = manifest_sha
    result, rows = score_series(series, model)
    write_json(SERIES, series)
    result["freeze_sha256"] = sha256(FREEZE)
    result["frozen_inputs"] = frozen
    result["verified_source_manifest_sha256"] = manifest_sha
    result["series_sha256"] = sha256(SERIES)
    write_json(RESULTS, result)
    with PREDICTIONS.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("bin_id", "trace_start_us", "actual", *METHODS))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"gate": result["research_gate"], "test_bins": len(rows),
                      "mae": {name: result["metrics"][name]["mae"] for name in METHODS},
                      "source_audit": series["audit"]}, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "evaluate"))
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    else:
        evaluate()


if __name__ == "__main__":
    main()
