"""Frozen three-shard stress test of *observed* CPU/RAM peaks under original allocation.

`prepare` reads training and validation labels only. `test` requires a matching
frozen manifest before reading the held-out shard. Neither stage estimates a
minimum safe resource request or an AI application/VM sizing policy.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/google_cluster_2011_2"
OUT = RESEARCH / "data/derived/google_2011_observed_resource"
PROTOCOL = RESEARCH / "reports/google_2011_observed_resource_protocol.md"
SHARDS = (50, 200, 400)
METHODS = ("request", "global_ratio", "conditional_ratio", "hgb")
TARGETS = {"cpu": ("cpu_request", "max_cpu"), "memory": ("memory_request", "max_memory")}
Q = 0.90
SPAN = 30 * 60 * 1_000_000
MODEL_KWARGS = dict(loss="quantile", quantile=Q, max_iter=150,
                    max_leaf_nodes=15, max_depth=4, min_samples_leaf=20,
                    learning_rate=0.05, l2_regularization=1.0, random_state=42)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes() -> dict[str, str]:
    source_manifest_digest = sha256(SOURCE / "SHA256SUM")
    if source_manifest_digest != "b68993eefffd34d42f2fc6613187048112602d185ffe61992292bacf2c2e9940":
        raise ValueError("Official SHA256SUM manifest changed")
    manifest = {}
    for line in (SOURCE / "SHA256SUM").read_text().splitlines():
        if " *" in line:
            digest, name = line.split(" *", 1)
            manifest[name] = digest
    checks = {}
    for shard in SHARDS:
        for kind in ("task_events", "task_usage"):
            name = f"{kind}/part-{shard:05d}-of-00500.csv.gz"
            actual = sha256(SOURCE / name)
            if actual != manifest[name]:
                raise ValueError(f"Official SHA-256 mismatch: {name}")
            checks[name] = actual
    checks["SHA256SUM"] = source_manifest_digest
    return checks


@dataclass(frozen=True, slots=True)
class Event:
    time: int
    kind: str
    machine: str
    missing: str
    cpu_request: str
    memory_request: str
    scheduling_class: str
    priority: str


@dataclass(slots=True)
class Episode:
    start: int
    end: int
    machine: str
    cpu_request: float
    memory_request: float
    scheduling_class: float
    priority: float
    intervals: list[tuple[int, int]] = field(default_factory=list)
    valid_rows: int = 0
    max_cpu: float = 0.0
    max_memory: float = 0.0
    agg1_max_cpu: float | None = None
    agg1_max_memory: float | None = None


def interval_coverage(intervals: list[tuple[int, int]]) -> int:
    covered, right = 0, 0
    for left, stop in sorted(intervals):
        if stop <= right:
            continue
        covered += stop - max(left, right)
        right = stop
    return covered


def _num(value: str) -> float:
    return float(value) if value else float("nan")


def extract_shard(shard: int) -> tuple[pd.DataFrame, dict]:
    """Mirror the prior strict audit; only call for 400 in the frozen test stage."""
    prefix = f"part-{shard:05d}-of-00500.csv.gz"
    events: dict[tuple[str, str], list[Event]] = defaultdict(list)
    event_max = 0
    with gzip.open(SOURCE / "task_events" / prefix, "rt", newline="") as source:
        for row in csv.reader(source):
            event_max = max(event_max, int(row[0]))
            events[(row[2], row[3])].append(Event(
                int(row[0]), row[5], row[4], row[1], row[9], row[10], row[7], row[8]
            ))
    episodes: dict[tuple[str, str], Episode] = {}
    for key, task_events in events.items():
        submits = [event for event in task_events if event.kind == "0"]
        if len(submits) != 1:
            continue
        submit = submits[0]
        if submit.missing or not submit.cpu_request or not submit.memory_request:
            continue
        cpu_request, memory_request = float(submit.cpu_request), float(submit.memory_request)
        if not all(math.isfinite(x) and x > 0 for x in (cpu_request, memory_request)):
            continue
        schedules = [event for event in task_events if event.kind == "1"
                     and event.time >= submit.time and event.machine and not event.missing]
        if len(schedules) != 1:
            continue
        schedule = schedules[0]
        end = schedule.time + SPAN
        if (schedule.time > submit.time + 600 * 1_000_000
                or any(event.time < submit.time for event in task_events)
                or (schedule.cpu_request and float(schedule.cpu_request) != cpu_request)
                or (schedule.memory_request and float(schedule.memory_request) != memory_request)
                or end > event_max):
            continue
        if any(event is not schedule and submit.time <= event.time < end
               and event.kind in "12345678" for event in task_events):
            continue
        episodes[key] = Episode(schedule.time, end, schedule.machine,
                                cpu_request, memory_request,
                                _num(submit.scheduling_class), _num(submit.priority))

    with gzip.open(SOURCE / "task_usage" / prefix, "rt", newline="") as source:
        for row in csv.reader(source):
            episode = episodes.get((row[2], row[3]))
            if episode is None:
                continue
            start, end = int(row[0]), int(row[1])
            if (row[4] != episode.machine or end <= episode.start or start >= episode.end
                    or not row[10] or not row[13]):
                continue
            memory, cpu = float(row[10]), float(row[13])
            if not all(math.isfinite(x) and x >= 0 for x in (memory, cpu)):
                continue
            episode.intervals.append((max(start, episode.start), min(end, episode.end)))
            episode.valid_rows += 1
            episode.max_cpu = max(episode.max_cpu, cpu)
            episode.max_memory = max(episode.max_memory, memory)
            # Google v2.1 task_usage field 19 (zero-based 18) is aggregation type.
            if row[18] == "1":
                episode.agg1_max_cpu = max(episode.agg1_max_cpu or 0.0, cpu)
                episode.agg1_max_memory = max(episode.agg1_max_memory or 0.0, memory)

    rows = []
    covered_count, contaminated_count = 0, 0
    for (job, task), episode in episodes.items():
        if episode.valid_rows < 5 or interval_coverage(episode.intervals) < 0.95 * SPAN:
            continue
        covered_count += 1
        if ((episode.agg1_max_cpu is not None and episode.agg1_max_cpu >= episode.max_cpu)
                or (episode.agg1_max_memory is not None
                    and episode.agg1_max_memory >= episode.max_memory)):
            contaminated_count += 1
            continue
        rows.append(dict(job=job, task=task, cpu_request=episode.cpu_request,
                         memory_request=episode.memory_request,
                         scheduling_class=episode.scheduling_class,
                         priority=episode.priority,
                         max_cpu=episode.max_cpu, max_memory=episode.max_memory))
    frame = pd.DataFrame.from_records(rows)
    if frame.empty:
        raise ValueError(f"No eligible episodes in shard {shard}")
    return frame, dict(shard=shard, event_eligible=len(episodes),
                       valid_and_covered=covered_count,
                       aggregation_peak_excluded=contaminated_count,
                       used_rows=len(frame), jobs=int(frame.job.nunique()),
                       missing_request_time_features={
                           col: int(frame[col].isna().sum())
                           for col in ("scheduling_class", "priority")})


def job_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("job", sort=False).job.transform("size").to_numpy(float)
    return 1.0 / counts


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float = Q) -> float:
    if len(values) == 0 or np.any(weights < 0) or not np.isfinite(values).all():
        raise ValueError("Invalid weighted quantile inputs")
    order = np.argsort(values, kind="stable")
    sorted_values, sorted_weights = values[order], weights[order]
    total = sorted_weights.sum()
    if total <= 0:
        raise ValueError("All quantile weights are zero")
    index = min(np.searchsorted(np.cumsum(sorted_weights), q * total, side="left"), len(values) - 1)
    return float(sorted_values[index])


def _bins(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    return np.searchsorted(edges, values, side="right").astype(int)


def fit_ratios(frame: pd.DataFrame) -> dict:
    edges = {col: np.quantile(frame[col].to_numpy(float), [0.25, 0.5, 0.75])
             for col in ("cpu_request", "memory_request")}
    cpu_bin = _bins(frame.cpu_request.to_numpy(float), edges["cpu_request"])
    mem_bin = _bins(frame.memory_request.to_numpy(float), edges["memory_request"])
    weights = job_weights(frame)
    fitted: dict = {"edges": edges, "targets": {}}
    for target, (request, observed) in TARGETS.items():
        ratio = frame[observed].to_numpy(float) / frame[request].to_numpy(float)
        global_ratio = weighted_quantile(ratio, weights)
        axis = cpu_bin if target == "cpu" else mem_bin
        axis_ratios, cell_ratios = {}, {}
        for b in range(4):
            mask = axis == b
            if mask.sum() >= 20 and frame.loc[mask, "job"].nunique() >= 3:
                axis_ratios[b] = weighted_quantile(ratio[mask], job_weights(frame.loc[mask]))
        for c in range(4):
            for m in range(4):
                mask = (cpu_bin == c) & (mem_bin == m)
                if mask.sum() >= 20 and frame.loc[mask, "job"].nunique() >= 3:
                    cell_ratios[(c, m)] = weighted_quantile(ratio[mask], job_weights(frame.loc[mask]))
        fitted["targets"][target] = dict(global_ratio=global_ratio,
                                         axis_ratios=axis_ratios,
                                         cell_ratios=cell_ratios)
    return fitted


def feature_matrix(frame: pd.DataFrame) -> np.ndarray:
    return np.column_stack((np.log1p(frame.cpu_request.to_numpy(float)),
                            np.log1p(frame.memory_request.to_numpy(float)),
                            frame.scheduling_class.to_numpy(float),
                            frame.priority.to_numpy(float)))


def fit_bundle(frame: pd.DataFrame) -> dict:
    bundle = {"ratio": fit_ratios(frame), "hgb": {}}
    features, weights = feature_matrix(frame), job_weights(frame)
    for target, (_, observed) in TARGETS.items():
        model = HistGradientBoostingRegressor(**MODEL_KWARGS)
        model.fit(features, np.log1p(frame[observed].to_numpy(float)), sample_weight=weights)
        bundle["hgb"][target] = model
    return bundle


def predict(frame: pd.DataFrame, bundle: dict, target: str) -> dict[str, np.ndarray]:
    request, _ = TARGETS[target]
    source = frame[request].to_numpy(float)
    ratio = bundle["ratio"]["targets"][target]
    edges = bundle["ratio"]["edges"]
    cpu_bin = _bins(frame.cpu_request.to_numpy(float), edges["cpu_request"])
    mem_bin = _bins(frame.memory_request.to_numpy(float), edges["memory_request"])
    axis = cpu_bin if target == "cpu" else mem_bin
    conditional = np.asarray([
        ratio["cell_ratios"].get((int(c), int(m)),
                                 ratio["axis_ratios"].get(int(a), ratio["global_ratio"]))
        for c, m, a in zip(cpu_bin, mem_bin, axis)
    ])
    return {"request": source,
            "global_ratio": source * ratio["global_ratio"],
            "conditional_ratio": source * conditional,
            "hgb": np.maximum(0.0, np.expm1(bundle["hgb"][target].predict(feature_matrix(frame))))}


def pinball(y: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    return np.where(y >= prediction, Q * (y - prediction), (1 - Q) * (prediction - y))


def job_table(frame: pd.DataFrame, target: str, predictions: dict[str, np.ndarray]) -> pd.DataFrame:
    y = frame[TARGETS[target][1]].to_numpy(float)
    data = pd.DataFrame({"job": frame.job.to_numpy(), "n": np.ones(len(frame), dtype=int)})
    for method, prediction in predictions.items():
        data[f"{method}_loss"] = pinball(y, prediction)
        data[f"{method}_coverage"] = (y <= prediction).astype(float)
    agg = {col: "mean" for col in data if col.endswith(("_loss", "_coverage"))}
    agg["n"] = "sum"
    return data.groupby("job", sort=True).agg(agg).reset_index()


def scores(frame: pd.DataFrame, bundle: dict) -> tuple[dict, dict[str, pd.DataFrame]]:
    scored, tables = {}, {}
    for target in TARGETS:
        table = job_table(frame, target, predict(frame, bundle, target))
        tables[target] = table
        scored[target] = {}
        for method in METHODS:
            losses = table[f"{method}_loss"].to_numpy(float)
            coverage = table[f"{method}_coverage"].to_numpy(float)
            counts = table.n.to_numpy(float)
            scored[target][method] = dict(job_equal_pinball=float(losses.mean()),
                                          row_pinball=float(np.average(losses, weights=counts)),
                                          job_equal_coverage=float(coverage.mean()),
                                          row_coverage=float(np.average(coverage, weights=counts)),
                                          jobs=len(table), rows=int(counts.sum()))
    return scored, tables


def select(validation: dict) -> dict[str, str]:
    return {target: min(METHODS, key=lambda method: (
        validation[target][method]["job_equal_pinball"], METHODS.index(method)))
        for target in TARGETS}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "freeze_manifest.json").exists():
        raise FileExistsError("Frozen manifest already exists; use a fresh output directory")
    checks = source_hashes()  # Hash held-out bytes; do not parse held-out labels.
    train, train_info = extract_shard(50)
    validation, validation_info = extract_shard(200)
    if (train_info["valid_and_covered"] != 3209
            or validation_info["valid_and_covered"] != 2770
            or train_info["aggregation_peak_excluded"] != 75
            or validation_info["aggregation_peak_excluded"] != 0):
        raise ValueError("Strict-audit counts changed; inspect extraction before training")
    if set(train.job) & set(validation.job):
        raise ValueError("Training and validation job IDs overlap")
    bundle = fit_bundle(train)
    validation_scores, _ = scores(validation, bundle)
    chosen = select(validation_scores)
    model_path = OUT / "frozen_models.joblib"
    joblib.dump(bundle, model_path, compress=3)
    manifest = dict(protocol_sha256=sha256(PROTOCOL), script_sha256=sha256(Path(__file__)),
                    source_sha256=checks, model_sha256=sha256(model_path),
                    train_info=train_info, validation_info=validation_info,
                    train_job_ids=sorted(train.job.unique().tolist()),
                    validation_job_ids=sorted(validation.job.unique().tolist()),
                    validation_scores=validation_scores, selected=chosen,
                    candidate_order=list(METHODS), quantile=Q,
                    test_labels_read=False)
    _write_json(OUT / "freeze_manifest.json", manifest)
    print(json.dumps(dict(train=train_info, validation=validation_info,
                          selected=chosen, validation_scores=validation_scores,
                          freeze_manifest_sha256=sha256(OUT / "freeze_manifest.json")), indent=2))


def bootstrap_job_diff(table: pd.DataFrame, method: str, baseline: str) -> dict:
    differences = (table[f"{method}_loss"] - table[f"{baseline}_loss"]).to_numpy(float)
    rng = np.random.default_rng(2026)
    draws = rng.integers(0, len(differences), size=(2000, len(differences)))
    boot = differences[draws].mean(axis=1)
    return dict(mean_difference=float(differences.mean()),
                ci95=[float(x) for x in np.quantile(boot, [0.025, 0.975])],
                jobs=len(differences), bootstrap_draws=2000, seed=2026)


def test() -> None:
    manifest_path = OUT / "freeze_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if sha256(PROTOCOL) != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed after freeze")
    if sha256(Path(__file__)) != manifest["script_sha256"]:
        raise ValueError("Script changed after freeze")
    if source_hashes() != manifest["source_sha256"]:
        raise ValueError("Source bytes changed after freeze")
    model_path = OUT / "frozen_models.joblib"
    if sha256(model_path) != manifest["model_sha256"]:
        raise ValueError("Model changed after freeze")
    if (OUT / "test_scores.json").exists():
        raise FileExistsError("Test score already exists")
    bundle = joblib.load(model_path)
    frame, info = extract_shard(400)  # No held-out model loss was seen before the freeze.
    if info["valid_and_covered"] != 6447:
        raise ValueError("Held-out strict-audit count changed")
    if set(frame.job) & set(manifest["train_job_ids"] + manifest["validation_job_ids"]):
        raise ValueError("Test/development job overlap")
    scored, tables = scores(frame, bundle)
    gate, uncertainty, sensitivity = {}, {}, {}
    top_job = frame.job.value_counts().index[0]
    for target in TARGETS:
        baseline = min(METHODS[:-1], key=lambda method: (
            manifest["validation_scores"][target][method]["job_equal_pinball"],
            METHODS.index(method)))
        hgb, base = scored[target]["hgb"], scored[target][baseline]
        gate[target] = dict(validation_selected_baseline=baseline,
                            job_equal_pinball_5pct_better=hgb["job_equal_pinball"] <= .95 * base["job_equal_pinball"],
                            row_pinball_not_5pct_worse=hgb["row_pinball"] <= 1.05 * base["row_pinball"],
                            job_equal_coverage_at_least_085=hgb["job_equal_coverage"] >= .85)
        uncertainty[target] = bootstrap_job_diff(tables[target], "hgb", baseline)
        trimmed = tables[target].loc[tables[target].job != top_job]
        sensitivity[target] = {
            method: dict(job_equal_pinball=float(trimmed[f"{method}_loss"].mean()),
                         row_pinball=float(np.average(trimmed[f"{method}_loss"], weights=trimmed.n)),
                         job_equal_coverage=float(trimmed[f"{method}_coverage"].mean()))
            for method in METHODS}
    result = dict(test_info=info, scores=scored, validation_selected=manifest["selected"],
                  prespecified_gate=gate,
                  learned_improvement_confirmed=all(all(v for k, v in gate[target].items()
                                                         if k != "validation_selected_baseline")
                                                    for target in TARGETS),
                  bootstrap_hgb_minus_validation_best_baseline=uncertainty,
                  posthoc_remove_largest_job=dict(job_id=top_job,
                                                  removed_rows=int((frame.job == top_job).sum()),
                                                  scores=sensitivity),
                  freeze_manifest_sha256=sha256(manifest_path))
    _write_json(OUT / "test_scores.json", result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "test"))
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    else:
        test()


if __name__ == "__main__":
    main()
