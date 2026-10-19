"""Frozen development evaluation of Borg's first post-SCHEDULE EVICT/FAIL.

Only shards 00050 and 00200 can be opened by this script. The outcome is a
recorded first event under the original allocation, never an AI-App action
effect or a replacement resource request's safety label.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_google_2011_early_exit as audit  # noqa: E402


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/google_cluster_2011_2"
PROTOCOL = RESEARCH / "reports/google_2011_early_exit_risk_protocol.md"
OUT = RESEARCH / "artifacts/google_2011_early_exit_risk_v1"
SOURCE_MANIFEST_SHA256 = "b68993eefffd34d42f2fc6613187048112602d185ffe61992292bacf2c2e9940"
AUDIT_SHA256 = "46b26798a62ea593fcffafe422c11461efac2fc383863972490c70c3734af723"
DEV_SHARDS = (50, 200)
HORIZON_SECONDS = 1800
PRIOR_STRENGTH = 20.0  # job-equivalent pseudo-observations
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 42
POSITIVE = frozenset({"EVICT", "FAIL"})
NEGATIVE = frozenset({
    "FINISH", "KILL", "UPDATE_PENDING", "UPDATE_RUNNING", "LOST",
    "no_recorded_event_in_window",
})
COMPETING_DIAGNOSTIC_EXCLUDE = frozenset({"KILL", "UPDATE_PENDING", "UPDATE_RUNNING", "LOST"})
MODEL_PARAMS = {
    "loss": "log_loss", "max_iter": 100, "max_leaf_nodes": 15,
    "max_depth": 3, "min_samples_leaf": 50, "learning_rate": 0.05,
    "l2_regularization": 10.0, "early_stopping": False,
    "random_state": 42,
}


@dataclass(frozen=True)
class Row:
    job_id: str
    task_index: str
    submitted_at_us: int
    scheduled_at_us: int
    first_event: str
    label: int | None
    scheduling_class: str
    priority: str
    cpu_request: float
    memory_request: float


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def official_hashes() -> dict[str, str]:
    if sha256(SOURCE / "SHA256SUM") != SOURCE_MANIFEST_SHA256:
        raise ValueError("Google official SHA256SUM manifest changed")
    names = {}
    for line in (SOURCE / "SHA256SUM").read_text().splitlines():
        if " *" in line:
            digest, name = line.split(" *", 1)
            names[name] = digest
    checked = {}
    for shard in DEV_SHARDS:
        name = f"task_events/part-{shard:05d}-of-00500.csv.gz"
        expected = audit.SOURCES[shard]
        if names.get(name) != expected or sha256(SOURCE / name) != expected:
            raise ValueError(f"Google task_events source checksum mismatch: {name}")
        checked[name] = expected
    checked["SHA256SUM"] = SOURCE_MANIFEST_SHA256
    return checked


def freeze() -> dict:
    if sha256(Path(audit.__file__)) != AUDIT_SHA256:
        raise ValueError("Google early-exit audit code changed")
    frozen = {
        "stage": "development_only_00050_00200",
        "protocol_sha256": sha256(PROTOCOL),
        "evaluator_sha256": sha256(Path(__file__)),
        "audit_sha256": AUDIT_SHA256,
        "source_sha256": official_hashes(),
        "holdout_shard_preselected_not_read": "00350",
        "holdout_expected_sha256": "a01ab82043e64376221ebb731fa9415b722be3faeaa3ace2596c9f90867b8b4c",
        "model_parameters": MODEL_PARAMS,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "freeze_manifest.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if existing != frozen:
            raise ValueError("freeze manifest differs from the current protocol, code, or sources")
    else:
        path.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    return frozen


def first_eligible_per_task(attempts: list[audit.Attempt], shard_end: int) -> list[audit.Attempt]:
    last_schedule = shard_end - HORIZON_SECONDS * audit.US
    first = {}
    for attempt in sorted(attempts, key=lambda a: (a.submitted_at, a.scheduled_at)):
        if attempt.scheduled_at <= last_schedule:
            first.setdefault((attempt.job_id, attempt.task_index), attempt)
    return list(first.values())


def first_event_in_window(attempt: audit.Attempt) -> str:
    if (attempt.next_event_at is None
            or attempt.next_event_at > attempt.scheduled_at + HORIZON_SECONDS * audit.US):
        return "no_recorded_event_in_window"
    return attempt.next_event


def label_for_event(first_event: str) -> int | None:
    if first_event in POSITIVE:
        return 1
    if first_event in NEGATIVE:
        return 0
    return None


def validation_without_training_jobs(training: list[Row], validation: list[Row]) -> list[Row]:
    train_jobs = {row.job_id for row in training}
    return [row for row in validation if row.job_id not in train_jobs]


def read_submit_profiles(shard: int, attempts: list[audit.Attempt]) -> dict[tuple[str, str, int], tuple[str, str, float, float]]:
    if shard not in DEV_SHARDS:
        raise ValueError("the reserved test shards must not be opened")
    keys = {(a.job_id, a.task_index, a.submitted_at) for a in attempts}
    found = {}
    path = SOURCE / "task_events" / f"part-{shard:05d}-of-00500.csv.gz"
    with gzip.open(path, "rt", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) < 11 or row[5] != "0":
                continue
            key = (row[2], row[3], int(row[0]))
            if key not in keys:
                continue
            if key in found:
                raise ValueError("ambiguous duplicate SUBMIT at one task timestamp")
            scheduling_class, priority = row[7], row[8]
            cpu_request, memory_request = float(row[9]), float(row[10])
            if not (scheduling_class and priority and math.isfinite(float(scheduling_class))
                    and math.isfinite(float(priority)) and math.isfinite(cpu_request)
                    and math.isfinite(memory_request) and cpu_request > 0 and memory_request > 0):
                raise ValueError("invalid decision-time SUBMIT feature")
            found[key] = (scheduling_class, priority, cpu_request, memory_request)
    if found.keys() != keys:
        raise ValueError(f"could not link {len(keys - found.keys())} attempts to unique SUBMIT features")
    return found


def rows_for_shard(shard: int) -> tuple[list[Row], dict]:
    if shard not in DEV_SHARDS:
        raise ValueError("the reserved test shards must not be opened")
    audit_summary, attempts, shard_end = audit.audit_shard(shard)
    first_attempts = first_eligible_per_task(attempts, shard_end)
    profiles = read_submit_profiles(shard, first_attempts)
    rows = []
    for attempt in first_attempts:
        first_event = first_event_in_window(attempt)
        label = label_for_event(first_event)
        key = (attempt.job_id, attempt.task_index, attempt.submitted_at)
        scheduling_class, priority, cpu, memory = profiles[key]
        rows.append(Row(attempt.job_id, attempt.task_index, attempt.submitted_at,
                        attempt.scheduled_at, first_event, label, scheduling_class,
                        priority, cpu, memory))
    expected = audit_summary["horizons"][str(HORIZON_SECONDS)]["first_eligible_attempt_per_task"]["eligible_attempts"]
    if len(rows) != expected:
        raise AssertionError("the evaluator and event audit have different denominators")
    return rows, {"source_shard": shard, "first_eligible_attempts": len(rows),
                  "first_event_counts": dict(sorted(Counter(r.first_event for r in rows).items()))}


def job_weights(rows: list[Row], *, normalize_mean: bool = False) -> np.ndarray:
    counts = Counter(row.job_id for row in rows)
    weights = np.asarray([1.0 / counts[row.job_id] for row in rows], dtype=float)
    return weights / weights.mean() if normalize_mean else weights


def features(rows: list[Row]) -> np.ndarray:
    return np.asarray([
        [math.log1p(row.cpu_request), math.log1p(row.memory_request),
         float(row.scheduling_class), float(row.priority)]
        for row in rows
    ], dtype=float)


def fit_baselines(rows: list[Row]) -> tuple[float, dict[tuple[str, str], float]]:
    weights = job_weights(rows)
    labels = np.asarray([row.label for row in rows], dtype=float)
    global_rate = float(np.average(labels, weights=weights))
    cell_count = defaultdict(float)
    cell_positive = defaultdict(float)
    for row, weight in zip(rows, weights):
        cell = (row.scheduling_class, row.priority)
        cell_count[cell] += weight
        cell_positive[cell] += weight * int(row.label)
    cells = {
        cell: (cell_positive[cell] + PRIOR_STRENGTH * global_rate)
        / (cell_count[cell] + PRIOR_STRENGTH)
        for cell in cell_count
    }
    return global_rate, cells


def predict_baselines(rows: list[Row], global_rate: float,
                      cells: dict[tuple[str, str], float]) -> dict[str, np.ndarray]:
    return {
        "global": np.full(len(rows), global_rate, dtype=float),
        "class_priority_shrunk": np.asarray([
            cells.get((row.scheduling_class, row.priority), global_rate) for row in rows
        ], dtype=float),
    }


def loss_arrays(rows: list[Row], predictions: dict[str, np.ndarray]) -> dict[str, dict[str, np.ndarray]]:
    labels = np.asarray([row.label for row in rows], dtype=float)
    result = {}
    for name, probabilities in predictions.items():
        probs = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        if probs.shape != labels.shape or not np.isfinite(probs).all():
            raise ValueError(f"invalid probability vector for {name}")
        result[name] = {
            "brier": (probs - labels) ** 2,
            "log_loss": -labels * np.log(probs) - (1 - labels) * np.log1p(-probs),
        }
    return result


def score(rows: list[Row], predictions: dict[str, np.ndarray]) -> tuple[dict, list[dict]]:
    if not rows:
        raise ValueError("cannot score an empty cohort")
    weights = job_weights(rows)
    labels = np.asarray([row.label for row in rows], dtype=float)
    losses = loss_arrays(rows, predictions)
    by_job: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_job[row.job_id].append(index)
    job_records = []
    for job, indices in sorted(by_job.items()):
        item = {"job_id": job, "rows": len(indices), "positives": int(labels[indices].sum())}
        for name in predictions:
            item[f"{name}_brier"] = float(losses[name]["brier"][indices].mean())
            item[f"{name}_log_loss"] = float(losses[name]["log_loss"][indices].mean())
        job_records.append(item)
    metrics = {
        "rows": len(rows), "jobs": len(by_job), "positives": int(labels.sum()),
        "positive_jobs": sum(item["positives"] > 0 for item in job_records),
        "job_macro_observed_rate": float(np.average(labels, weights=weights)),
        "methods": {},
    }
    for name, probabilities in predictions.items():
        prob = np.asarray(probabilities, dtype=float)
        job_brier = [item[f"{name}_brier"] for item in job_records]
        job_log = [item[f"{name}_log_loss"] for item in job_records]
        calibration = []
        for bucket in range(10):
            mask = (prob >= bucket / 10) & (prob < (bucket + 1) / 10 if bucket < 9 else prob <= 1)
            if mask.any():
                calibration.append({"probability_bin": bucket,
                                    "rows": int(mask.sum()),
                                    "job_equivalent_weight": float(weights[mask].sum()),
                                    "predicted": float(np.average(prob[mask], weights=weights[mask])),
                                    "observed": float(np.average(labels[mask], weights=weights[mask]))})
        metrics["methods"][name] = {
            "job_macro_brier": float(np.mean(job_brier)),
            "job_macro_log_loss": float(np.mean(job_log)),
            "row_micro_brier": float(losses[name]["brier"].mean()),
            "job_macro_mean_probability": float(np.average(prob, weights=weights)),
            "job_macro_absolute_calibration_gap": float(abs(np.average(prob - labels, weights=weights))),
            "max_occupied_bin_calibration_gap": max(abs(b["predicted"] - b["observed"]) for b in calibration),
            "calibration_bins": calibration,
        }
    return metrics, job_records


def positive_concentration(rows: list[Row]) -> dict:
    counts = Counter(row.job_id for row in rows if row.label == 1)
    values = sorted(counts.values(), reverse=True)
    total = sum(values)
    return {"positive_rows": total, "positive_jobs": len(values),
            "largest_positive_job_rows": values[0] if values else 0,
            "top_five_positive_job_rows": sum(values[:5]),
            "top_five_positive_row_fraction": sum(values[:5]) / total if total else None,
            "positive_row_effective_jobs": total**2 / sum(n*n for n in values) if total else 0.0}


def bootstrap_job_difference(job_records: list[dict]) -> dict:
    differences = np.asarray([
        item["hgb_brier"] - item["class_priority_shrunk_brier"]
        for item in job_records
    ], dtype=float)
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    samples = generator.integers(0, len(differences), size=(BOOTSTRAP_DRAWS, len(differences)))
    means = differences[samples].mean(axis=1)
    return {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            "mean_difference": float(differences.mean()),
            "percentile_95_interval": [float(v) for v in np.quantile(means, [0.025, 0.975])]}


def write_csv(path: Path, records: list[dict]) -> None:
    if not records:
        raise ValueError("no records to write")
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def evaluate() -> dict:
    if not (OUT / "freeze_manifest.json").exists():
        raise ValueError("run --stage freeze before scoring")
    if (OUT / "metrics.json").exists():
        raise ValueError("development scores already exist; repeated selection is forbidden")
    frozen = freeze()  # verifies code, protocol, source before opening labels
    training, train_audit = rows_for_shard(50)
    validation, val_audit = rows_for_shard(200)
    train_jobs = {row.job_id for row in training}
    validation_new = validation_without_training_jobs(training, validation)
    train_primary = [row for row in training if row.label is not None]
    val_primary = [row for row in validation_new if row.label is not None]
    support = positive_concentration(val_primary)
    denominators = {
        "training": train_audit,
        "validation_before_job_disjoint": val_audit,
        "validation_removed_shared_job_rows": len(validation) - len(validation_new),
        "validation_shared_jobs": len(train_jobs & {row.job_id for row in validation}),
        "training_primary_rows": len(train_primary),
        "training_primary_event_counts": dict(sorted(Counter(row.first_event for row in train_primary).items())),
        "validation_new_job_first_attempts": len(validation_new),
        "validation_new_job_first_event_counts": dict(sorted(Counter(row.first_event for row in validation_new).items())),
        "validation_primary_rows": len(val_primary),
        "validation_primary_support": support,
    }
    if len(train_primary) < 1000 or support["positive_jobs"] < 30:
        output = {"decision": "NO_GO_INSUFFICIENT_SUPPORT", "denominators": denominators,
                  "freeze_manifest_sha256": sha256(OUT / "freeze_manifest.json"),
                  "holdout_shard_00350_read": False}
        (OUT / "metrics.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
        return output

    global_rate, cells = fit_baselines(train_primary)
    model = HistGradientBoostingClassifier(**MODEL_PARAMS)
    model.fit(features(train_primary), np.asarray([row.label for row in train_primary], dtype=int),
              sample_weight=job_weights(train_primary, normalize_mean=True))
    def predict(rows: list[Row]) -> dict[str, np.ndarray]:
        p = predict_baselines(rows, global_rate, cells)
        p["hgb"] = model.predict_proba(features(rows))[:, 1]
        return p

    train_pred = predict(train_primary)
    val_pred = predict(val_primary)
    train_metrics, _ = score(train_primary, train_pred)
    val_metrics, job_records = score(val_primary, val_pred)
    interval = bootstrap_job_difference(job_records)
    model_brier = val_metrics["methods"]["hgb"]["job_macro_brier"]
    baseline_brier = val_metrics["methods"]["class_priority_shrunk"]["job_macro_brier"]
    relative_improvement = (baseline_brier - model_brier) / baseline_brier
    gates = {
        "job_macro_brier_relative_improvement_at_least_5pct": relative_improvement >= 0.05,
        "paired_job_bootstrap_upper_difference_below_zero": interval["percentile_95_interval"][1] < 0,
        "job_macro_log_loss_no_worse": val_metrics["methods"]["hgb"]["job_macro_log_loss"] <= val_metrics["methods"]["class_priority_shrunk"]["job_macro_log_loss"],
    }

    selected_rows = [row for row in val_primary if row.first_event not in COMPETING_DIAGNOSTIC_EXCLUDE]
    selected_metrics, _ = score(selected_rows, predict(selected_rows))
    positive_job_counts = Counter(row.job_id for row in val_primary if row.label == 1)
    largest_positive_job = positive_job_counts.most_common(1)[0][0]
    minus_largest = [row for row in val_primary if row.job_id != largest_positive_job]
    minus_largest_metrics, _ = score(minus_largest, predict(minus_largest))
    sensitivity = {
        "competing_KILL_UPDATE_LOST_conditionally_excluded_rows": len(val_primary) - len(selected_rows),
        "competing_events_excluded_not_online_target": {name: m["job_macro_brier"] for name, m in selected_metrics["methods"].items()},
        "exclude_largest_positive_job_removed_positive_rows": positive_job_counts[largest_positive_job],
        "exclude_largest_positive_job": {name: m["job_macro_brier"] for name, m in minus_largest_metrics["methods"].items()},
        "row_micro_brier": {name: m["row_micro_brier"] for name, m in val_metrics["methods"].items()},
    }
    joblib.dump(model, OUT / "hgb_model.joblib")
    write_csv(OUT / "validation_job_scores.csv", job_records)
    write_csv(OUT / "validation_predictions.csv", [
        {"job_id": row.job_id, "task_index": row.task_index,
         "submitted_at_us": row.submitted_at_us, "scheduled_at_us": row.scheduled_at_us,
         "first_event": row.first_event, "label": row.label,
         "global_probability": float(val_pred["global"][i]),
         "class_priority_shrunk_probability": float(val_pred["class_priority_shrunk"][i]),
         "hgb_probability": float(val_pred["hgb"][i])}
        for i, row in enumerate(val_primary)
    ])
    output = {
        "decision": "TEST_PENDING" if all(gates.values()) else "NO_GO_VALIDATION",
        "target": "first_recorded_EVICT_or_FAIL_within_30min_after_SCHEDULE",
        "interpretation": "generic Borg task observational risk, not AI-App deployment/scale action effect",
        "denominators": denominators,
        "training_in_sample_diagnostic": train_metrics,
        "validation_new_jobs": val_metrics,
        "comparison": {"relative_job_macro_brier_improvement": relative_improvement,
                       "paired_job_bootstrap": interval, "gates": gates},
        "sensitivity": sensitivity,
        "baseline": {"global_job_macro_rate": global_rate,
                     "class_priority_shrinkage_job_equivalent": PRIOR_STRENGTH,
                     "observed_training_cells": len(cells)},
        "freeze_manifest_sha256": sha256(OUT / "freeze_manifest.json"),
        "model_sha256": sha256(OUT / "hgb_model.joblib"),
        "python_numpy_sklearn": {"numpy": np.__version__, "sklearn": sklearn_version},
        "holdout_shard_00350_read": False,
    }
    (OUT / "metrics.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("freeze", "score"), required=True)
    args = parser.parse_args()
    if args.stage == "freeze":
        frozen = freeze()
        print(json.dumps({"stage": "freeze", "freeze_manifest_sha256": sha256(OUT / "freeze_manifest.json"),
                          "reserved_test_shard": frozen["holdout_shard_preselected_not_read"]}, sort_keys=True))
    else:
        output = evaluate()
        print(json.dumps({"decision": output["decision"],
                          "validation_primary_rows": output["denominators"]["validation_primary_rows"],
                          "holdout_shard_00350_read": False}, sort_keys=True))


if __name__ == "__main__":
    main()
