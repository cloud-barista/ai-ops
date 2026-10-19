"""Frozen early-telemetry prediction of later observed Borg CPU/RAM peaks.

Only ``validate`` may read the already available development labels. ``fetch-test``
requires a passing validation gate; no command reads shard 00475 before that.
The target is observed use under the original allocation, never safe VM sizing.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import platform
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "data/external/google_cluster_2011_2"
OUT = BASE / "artifacts/google_2011_early_feedback_v1"
PROTOCOL = BASE / "reports/google_2011_early_feedback_protocol_v1.md"
TESTS = BASE / "tests/test_google_2011_early_feedback.py"
MANIFEST = SOURCE / "SHA256SUM"
MANIFEST_SHA256 = "b68993eefffd34d42f2fc6613187048112602d185ffe61992292bacf2c2e9940"
TRAIN, VALIDATION, TEST = 50, 200, 475
Q = 0.9
US = 1_000_000
HORIZON = 1800 * US
METHODS = ("request_ratio", "recent_peak_margin", "submit_hgb", "feedback_hgb")
BASELINES = METHODS[:3]
AXES = ("cpu", "memory")
MODEL_KWARGS = dict(loss="quantile", quantile=Q, max_iter=150,
                    max_leaf_nodes=15, max_depth=3, min_samples_leaf=30,
                    learning_rate=0.05, l2_regularization=1.0, random_state=42)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def official_hashes() -> dict[str, str]:
    if sha256(MANIFEST) != MANIFEST_SHA256:
        raise ValueError("Official SHA256SUM manifest changed")
    result = {}
    for line in MANIFEST.read_text().splitlines():
        if " *" in line:
            digest, name = line.split(" *", 1)
            result[name] = digest
    return result


def source_name(kind: str, shard: int) -> str:
    return f"{kind}/part-{shard:05d}-of-00500.csv.gz"


def source_checks(shards: tuple[int, ...]) -> dict[str, str]:
    expected = official_hashes()
    checks = {}
    for shard in shards:
        for kind in ("task_events", "task_usage"):
            name = source_name(kind, shard)
            if sha256(SOURCE / name) != expected[name]:
                raise ValueError(f"Official SHA-256 mismatch: {name}")
            checks[name] = expected[name]
    return checks


@dataclass(frozen=True)
class Event:
    time: int
    kind: str
    machine: str
    missing: str
    cpu_request: str
    memory_request: str
    scheduling_class: str
    priority: str


@dataclass
class Episode:
    schedule: int
    end: int
    machine: str
    cpu_request: float
    memory_request: float
    scheduling_class: float
    priority: float
    usage: list[tuple[int, int, float, float]] = field(default_factory=list)


def valid_episode(events: list[Event], shard_end: int) -> Episode | None:
    submits = [event for event in events if event.kind == "0"]
    if len(submits) != 1:
        return None
    submit = submits[0]
    if submit.missing or not submit.cpu_request or not submit.memory_request:
        return None
    try:
        cpu = float(submit.cpu_request)
        memory = float(submit.memory_request)
    except ValueError:
        return None
    if not all(math.isfinite(x) and x > 0 for x in (cpu, memory)):
        return None
    schedules = [event for event in events if event.kind == "1"
                 and event.time >= submit.time and event.machine and not event.missing]
    if len(schedules) != 1:
        return None
    schedule = schedules[0]
    end = schedule.time + HORIZON
    if (schedule.time > submit.time + 600 * US or end > shard_end
            or any(event.time < submit.time for event in events)
            or (schedule.cpu_request and float(schedule.cpu_request) != cpu)
            or (schedule.memory_request and float(schedule.memory_request) != memory)
            or any(event is not schedule and submit.time <= event.time < end
                   and event.kind in "12345678" for event in events)):
        return None

    def number(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            return math.nan

    return Episode(schedule.time, end, schedule.machine, cpu, memory,
                   number(submit.scheduling_class), number(submit.priority))


def append_valid_usage(episode: Episode, row: list[str]) -> bool:
    if len(row) < 19 or row[4] != episode.machine or row[18] == "1":
        return False
    try:
        start, stop = int(row[0]), int(row[1])
        cpu, memory = float(row[13]), float(row[10])
    except ValueError:
        return False
    if (stop <= start or stop <= episode.schedule or start >= episode.end
            or not all(math.isfinite(x) and x >= 0 for x in (cpu, memory))):
        return False
    episode.usage.append((start, stop, cpu, memory))
    return True


def covered_fraction(rows: list[tuple[int, int, float, float]],
                     left: int, right: int) -> float:
    intervals = sorted((max(left, start), min(right, stop))
                       for start, stop, _, _ in rows if stop > left and start < right)
    covered, edge = 0, left
    for start, stop in intervals:
        if stop > edge:
            covered += stop - max(edge, start)
            edge = stop
    return covered / (right - left)


def episode_row(job: str, task: str, episode: Episode) -> dict | None:
    """No usage report may contribute information on both sides of decision."""
    # An interval maximum cannot be time-clipped. A report that began before
    # SCHEDULE cannot supply a post-schedule feature or decision timestamp.
    possible = [stop for start, stop, _, _ in episode.usage
                if start >= episode.schedule
                and episode.schedule + 300 * US <= stop <= episode.schedule + 600 * US]
    if not possible:
        return None
    decision = min(possible)
    prefix = [row for row in episode.usage
              if row[0] >= episode.schedule and row[1] <= decision]
    later = [row for row in episode.usage
             if row[0] >= decision and row[1] <= episode.end]
    if not prefix or not later:
        return None
    if max(row[1] for row in prefix) > decision or min(row[0] for row in later) < decision:
        raise AssertionError("A usage interval crossed the prediction boundary")
    pre_coverage = covered_fraction(prefix, episode.schedule, decision)
    later_coverage = covered_fraction(later, decision, episode.end)
    return dict(job=job, task=task, cpu_request=episode.cpu_request,
                memory_request=episode.memory_request,
                scheduling_class=episode.scheduling_class, priority=episode.priority,
                decision_offset_s=(decision - episode.schedule) / US,
                pre_coverage=pre_coverage, later_coverage=later_coverage,
                pre_rows=len(prefix),
                pre_schedule_report_excluded=sum(x[0] < episode.schedule
                                                 for x in episode.usage),
                pre_cpu_peak=max(x[2] for x in prefix),
                pre_memory_peak=max(x[3] for x in prefix),
                later_cpu_peak=max(x[2] for x in later),
                later_memory_peak=max(x[3] for x in later))


def extract_shard(shard: int) -> tuple[pd.DataFrame, dict]:
    name = f"part-{shard:05d}-of-00500.csv.gz"
    events: dict[tuple[str, str], list[Event]] = defaultdict(list)
    event_end, event_rows = 0, 0
    with gzip.open(SOURCE / "task_events" / name, "rt", newline="") as stream:
        for row in csv.reader(stream):
            event_rows += 1
            event_end = max(event_end, int(row[0]))
            events[(row[2], row[3])].append(Event(int(row[0]), row[5], row[4],
                                                  row[1], row[9], row[10],
                                                  row[7], row[8]))
    episodes = {key: episode for key, ev in events.items()
                if (episode := valid_episode(ev, event_end)) is not None}
    usage_rows, kept_usage = 0, 0
    with gzip.open(SOURCE / "task_usage" / name, "rt", newline="") as stream:
        for row in csv.reader(stream):
            usage_rows += 1
            episode = episodes.get((row[2], row[3]))
            if episode is not None and append_valid_usage(episode, row):
                kept_usage += 1
    rows = [result for (job, task), episode in episodes.items()
            if (result := episode_row(job, task, episode)) is not None]
    frame = pd.DataFrame.from_records(rows)
    if frame.empty:
        raise ValueError(f"No feedback episodes in shard {shard}")
    strict = frame[(frame.pre_coverage >= 0.9) & (frame.later_coverage >= 0.9)]
    eligible = frame[(frame.pre_coverage >= 0.8) & (frame.later_coverage >= 0.8)].copy()
    info = dict(shard=shard, event_rows=event_rows, usage_rows=usage_rows,
                event_candidate_tasks=len(episodes), valid_usage_rows=kept_usage,
                decision_candidate_tasks=len(frame), eligible_tasks=len(eligible),
                eligible_jobs=int(eligible.job.nunique()),
                coverage90_tasks=len(strict), coverage90_jobs=int(strict.job.nunique()),
                pre_schedule_report_tasks=int((eligible.pre_schedule_report_excluded > 0).sum()),
                largest_job_tasks=int(eligible.job.value_counts().iloc[0]) if len(eligible) else 0,
                decision_offset_median_s=float(eligible.decision_offset_s.median())
                if len(eligible) else None)
    return eligible.reset_index(drop=True), info


def job_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("job", sort=False).job.transform("size").to_numpy(float)
    return 1.0 / counts


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float = Q) -> float:
    if (len(values) == 0 or len(values) != len(weights)
            or not np.isfinite(values).all() or not np.isfinite(weights).all()
            or (weights < 0).any() or weights.sum() <= 0):
        raise ValueError("Invalid weighted quantile inputs")
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    index = min(int(np.searchsorted(cumulative, q * cumulative[-1], side="left")),
                len(values) - 1)
    return float(values[order[index]])


def split_fit_calibration(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    jobs = sorted(frame.job.unique(), key=lambda j: hashlib.sha256(j.encode()).digest())
    n_cal = math.ceil(len(jobs) / 4)
    cal_jobs = set(jobs[:n_cal])
    return (frame[~frame.job.isin(cal_jobs)].copy(),
            frame[frame.job.isin(cal_jobs)].copy())


def features(frame: pd.DataFrame, feedback: bool) -> np.ndarray:
    columns = [np.log1p(frame.cpu_request.to_numpy(float)),
               np.log1p(frame.memory_request.to_numpy(float)),
               frame.scheduling_class.to_numpy(float),
               frame.priority.to_numpy(float)]
    if feedback:
        columns += [np.log1p(frame.pre_cpu_peak.to_numpy(float)),
                    np.log1p(frame.pre_memory_peak.to_numpy(float)),
                    np.log1p(frame.pre_cpu_peak.to_numpy(float) /
                             frame.cpu_request.to_numpy(float)),
                    np.log1p(frame.pre_memory_peak.to_numpy(float) /
                             frame.memory_request.to_numpy(float)),
                    frame.pre_rows.to_numpy(float),
                    frame.decision_offset_s.to_numpy(float),
                    frame.pre_coverage.to_numpy(float)]
    return np.column_stack(columns)


def target(frame: pd.DataFrame, axis: str) -> np.ndarray:
    return frame[f"later_{axis}_peak"].to_numpy(float)


def prepeak(frame: pd.DataFrame, axis: str) -> np.ndarray:
    return frame[f"pre_{axis}_peak"].to_numpy(float)


def fit_bundle(fit: pd.DataFrame, calibration: pd.DataFrame) -> dict:
    weights = job_weights(fit)
    model_weights = weights * len(fit) / weights.sum()
    bundle: dict = {"scales": {}, "ratio": {}, "margin": {},
                    "models": {"submit_hgb": {}, "feedback_hgb": {}},
                    "joint_margins": {}}
    for axis in AXES:
        y = target(fit, axis)
        req = fit[f"{axis}_request"].to_numpy(float)
        old = prepeak(fit, axis)
        bundle["scales"][axis] = max(weighted_quantile(y, weights, 0.5), 1e-6)
        bundle["ratio"][axis] = weighted_quantile(y / req, weights)
        bundle["margin"][axis] = weighted_quantile(np.maximum(y - old, 0), weights)
        for method in ("submit_hgb", "feedback_hgb"):
            model = HistGradientBoostingRegressor(**MODEL_KWARGS)
            model.fit(features(fit, method == "feedback_hgb"), y,
                      sample_weight=model_weights)
            bundle["models"][method][axis] = model
    raw = predict_raw(calibration, bundle)
    cpu = target(calibration, "cpu")
    memory = target(calibration, "memory")
    cal_weights = job_weights(calibration)
    for method in METHODS:
        residual = np.maximum((cpu - raw[method]["cpu"]) / bundle["scales"]["cpu"],
                              (memory - raw[method]["memory"]) /
                              bundle["scales"]["memory"])
        bundle["joint_margins"][method] = max(0.0, weighted_quantile(residual, cal_weights))
    return bundle


def predict_raw(frame: pd.DataFrame, bundle: dict) -> dict[str, dict[str, np.ndarray]]:
    outputs: dict[str, dict[str, np.ndarray]] = {method: {} for method in METHODS}
    for axis in AXES:
        req = frame[f"{axis}_request"].to_numpy(float)
        outputs["request_ratio"][axis] = req * bundle["ratio"][axis]
        outputs["recent_peak_margin"][axis] = prepeak(frame, axis) + bundle["margin"][axis]
        for method in ("submit_hgb", "feedback_hgb"):
            values = bundle["models"][method][axis].predict(features(frame, method == "feedback_hgb"))
            outputs[method][axis] = np.maximum(values, 0)
    return outputs


def predict(frame: pd.DataFrame, bundle: dict) -> dict[str, dict[str, np.ndarray]]:
    outputs = predict_raw(frame, bundle)
    for method in METHODS:
        for axis in AXES:
            outputs[method][axis] = np.maximum(
                0, outputs[method][axis] + bundle["joint_margins"][method]
                * bundle["scales"][axis])
    return outputs


def pinball(actual: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    return np.where(actual >= predicted, Q * (actual - predicted),
                    (1 - Q) * (predicted - actual))


def score(frame: pd.DataFrame, bundle: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    predictions = predict(frame, bundle)
    details = pd.DataFrame({"job": frame.job.to_numpy(),
                            "count": np.ones(len(frame), dtype=int)})
    saved = pd.DataFrame({"job_hash": [hashlib.sha256(j.encode()).hexdigest()[:20]
                                         for j in frame.job],
                          "task_hash": [hashlib.sha256((j + ":" + t).encode()).hexdigest()[:20]
                                        for j, t in zip(frame.job, frame.task)],
                          "coverage90": ((frame.pre_coverage >= 0.9)
                                         & (frame.later_coverage >= 0.9)).to_numpy(),
                          "actual_cpu": target(frame, "cpu"),
                          "actual_memory": target(frame, "memory")})
    for method in METHODS:
        joint = np.ones(len(frame), dtype=bool)
        for axis in AXES:
            y = target(frame, axis)
            p = predictions[method][axis]
            details[f"{method}_{axis}_loss"] = pinball(y, p)
            details[f"{method}_{axis}_coverage"] = (y <= p).astype(float)
            saved[f"{method}_{axis}"] = p
            joint &= y <= p
        details[f"{method}_joint"] = joint.astype(float)
        details[f"{method}_composite"] = (
            details[f"{method}_cpu_loss"] / bundle["scales"]["cpu"]
            + details[f"{method}_memory_loss"] / bundle["scales"]["memory"]) / 2
    job = details.groupby("job", sort=True).mean(numeric_only=True)
    job["count"] = details.groupby("job", sort=True)["count"].sum()
    metrics = {}
    for method in METHODS:
        metrics[method] = dict(
            composite=float(job[f"{method}_composite"].mean()),
            cpu_pinball=float(job[f"{method}_cpu_loss"].mean()),
            memory_pinball=float(job[f"{method}_memory_loss"].mean()),
            joint_coverage=float(job[f"{method}_joint"].mean()),
            row_composite=float(details[f"{method}_composite"].mean()),
            row_joint_coverage=float(details[f"{method}_joint"].mean()))
    return metrics, job, saved


def gate(metrics: dict, job: pd.DataFrame, compare: str,
         fit_jobs: int, cal_jobs: int, validation_jobs: int,
         validation_tasks: int,
         axis_comparisons: dict[str, str]) -> dict:
    candidate = metrics["feedback_hgb"]
    baseline = metrics[compare]
    strongest_cpu = metrics[axis_comparisons["cpu"]]["cpu_pinball"]
    strongest_memory = metrics[axis_comparisons["memory"]]["memory_pinball"]
    wins = float((job["feedback_hgb_composite"] < job[f"{compare}_composite"]).mean())
    largest = job["count"].idxmax()
    remainder = job.drop(index=largest)
    without_largest = float(remainder["feedback_hgb_composite"].mean()
                            - remainder[f"{compare}_composite"].mean())
    checks = dict(support=(fit_jobs >= 40 and cal_jobs >= 15
                           and validation_jobs >= 30 and validation_tasks >= 1000),
                  composite_gain=candidate["composite"] <= 0.95 * baseline["composite"],
                  cpu_noninferior=candidate["cpu_pinball"] <= 1.05 * strongest_cpu,
                  memory_noninferior=candidate["memory_pinball"] <= 1.05 * strongest_memory,
                  joint_coverage=candidate["joint_coverage"] >= 0.85,
                  job_wins=wins >= 0.60,
                  largest_job_robust=without_largest < 0)
    return dict(passed=all(checks.values()), checks=checks,
                comparison=compare, axis_comparisons=axis_comparisons,
                job_win_fraction=wins,
                without_largest_composite_difference=without_largest)


def write_json(path: Path, value: dict) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "freeze.json").exists():
        raise FileExistsError("Freeze already exists; use a new experiment version")
    checks = source_checks((TRAIN, VALIDATION))  # Hash only; no labels opened.
    expected = official_hashes()
    test_hashes = {source_name(kind, TEST): expected[source_name(kind, TEST)]
                   for kind in ("task_events", "task_usage")}
    if any((SOURCE / name).exists() for name in test_hashes):
        raise ValueError("Test shard already exists before freeze")
    manifest = dict(protocol_sha256=sha256(PROTOCOL), code_sha256=sha256(Path(__file__)),
                    tests_sha256=sha256(TESTS), source_sha256=checks,
                    expected_test_sha256=test_hashes, test_unopened=True,
                    methods=list(METHODS), train=TRAIN, validation=VALIDATION, test=TEST,
                    python=platform.python_version(), numpy=np.__version__,
                    pandas=pd.__version__, sklearn=sklearn.__version__,
                    joblib=joblib.__version__)
    write_json(OUT / "freeze.json", manifest)
    print(json.dumps(dict(freeze_sha256=sha256(OUT / "freeze.json"),
                          expected_test_sha256=test_hashes), indent=2))


def check_freeze() -> dict:
    manifest = json.loads((OUT / "freeze.json").read_text())
    if (sha256(PROTOCOL) != manifest["protocol_sha256"]
            or sha256(Path(__file__)) != manifest["code_sha256"]
            or sha256(TESTS) != manifest["tests_sha256"]
            or source_checks((TRAIN, VALIDATION)) != manifest["source_sha256"]):
        raise ValueError("Frozen code, protocol, tests, or development source changed")
    if (platform.python_version() != manifest["python"]
            or np.__version__ != manifest["numpy"]
            or pd.__version__ != manifest["pandas"]
            or sklearn.__version__ != manifest["sklearn"]
            or joblib.__version__ != manifest["joblib"]):
        raise ValueError("Frozen numerical library versions changed")
    return manifest


def validate() -> None:
    frozen = check_freeze()
    if (OUT / "validation.json").exists():
        raise FileExistsError("Validation already scored")
    if any((SOURCE / name).exists() for name in frozen["expected_test_sha256"]):
        raise ValueError("Test shard appeared before validation gate")
    train, train_info = extract_shard(TRAIN)
    validation, validation_info = extract_shard(VALIDATION)
    fit, calibration = split_fit_calibration(train)
    overlap = set(train.job) & set(validation.job)
    if overlap:
        raise ValueError("Train/validation job overlap")
    bundle = fit_bundle(fit, calibration)
    metrics, jobs, predictions = score(validation, bundle)
    compare = min(BASELINES, key=lambda method: (metrics[method]["composite"],
                                                  BASELINES.index(method)))
    axis_comparisons = {axis: min(BASELINES, key=lambda method: (
        metrics[method][f"{axis}_pinball"], BASELINES.index(method))) for axis in AXES}
    decision = gate(metrics, jobs, compare, fit.job.nunique(), calibration.job.nunique(),
                    validation.job.nunique(), len(validation), axis_comparisons)
    strict = validation[(validation.pre_coverage >= 0.9)
                        & (validation.later_coverage >= 0.9)]
    strict_metrics = score(strict, bundle)[0] if len(strict) else {}
    model_path = OUT / "model.joblib"
    joblib.dump(bundle, model_path, compress=3)
    predictions.to_parquet(OUT / "validation_predictions.parquet", index=False)
    result = dict(train=train_info, validation=validation_info,
                  fit_jobs=int(fit.job.nunique()), calibration_jobs=int(calibration.job.nunique()),
                  metrics=metrics, decision=decision,
                  coverage90_metrics=strict_metrics,
                  model_sha256=sha256(model_path),
                  validation_predictions_sha256=sha256(OUT / "validation_predictions.parquet"),
                  test_labels_read=False,
                  coverage90_tasks=validation_info["coverage90_tasks"])
    write_json(OUT / "validation.json", result)
    print(json.dumps(result, indent=2))


def fetch_test() -> None:
    manifest = check_freeze()
    result = json.loads((OUT / "validation.json").read_text())
    if not result["decision"]["passed"]:
        raise ValueError("Validation gate failed; test shard remains sealed")
    for name, expected in manifest["expected_test_sha256"].items():
        path = SOURCE / name
        if path.exists():
            if sha256(path) != expected:
                raise ValueError(f"Existing test source hash mismatch: {name}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".partial")
        if temporary.exists():
            raise FileExistsError(f"Partial download exists: {temporary}")
        try:
            with urllib.request.urlopen("https://storage.googleapis.com/clusterdata-2011-2/" + name,
                                        timeout=60) as source, temporary.open("xb") as sink:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    sink.write(block)
            if sha256(temporary) != expected:
                raise ValueError(f"Official test source SHA-256 mismatch: {name}")
            temporary.rename(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    print(json.dumps(dict(test_source_sha256=source_checks((TEST,))), indent=2))


def test() -> None:
    manifest = check_freeze()
    validation_result = json.loads((OUT / "validation.json").read_text())
    if not validation_result["decision"]["passed"]:
        raise ValueError("Validation gate failed; test cannot be scored")
    if (OUT / "test.json").exists():
        raise FileExistsError("Test already scored")
    checks = source_checks((TEST,))
    if checks != manifest["expected_test_sha256"]:
        raise ValueError("Official test source mismatch")
    frame, info = extract_shard(TEST)
    train, _ = extract_shard(TRAIN)
    validation, _ = extract_shard(VALIDATION)
    if set(frame.job) & (set(train.job) | set(validation.job)):
        raise ValueError("Test job overlaps development")
    if info["eligible_jobs"] < 30 or info["eligible_tasks"] < 1000:
        result = dict(test=info, score_unavailable="insufficient_test_support",
                      test_source_sha256=checks)
        write_json(OUT / "test.json", result)
        print(json.dumps(result, indent=2))
        return
    model_path = OUT / "model.joblib"
    if sha256(model_path) != validation_result["model_sha256"]:
        raise ValueError("Fitted model changed after validation")
    bundle = joblib.load(model_path)
    metrics, jobs, predictions = score(frame, bundle)
    compare = validation_result["decision"]["comparison"]
    decision = gate(metrics, jobs, compare,
                    validation_result["fit_jobs"], validation_result["calibration_jobs"],
                    info["eligible_jobs"], info["eligible_tasks"],
                    validation_result["decision"]["axis_comparisons"])
    strict = frame[(frame.pre_coverage >= 0.9) & (frame.later_coverage >= 0.9)]
    strict_metrics = score(strict, bundle)[0] if len(strict) else {}
    predictions.to_parquet(OUT / "test_predictions.parquet", index=False)
    result = dict(test=info, metrics=metrics, decision=decision,
                  coverage90_metrics=strict_metrics,
                  test_source_sha256=checks, model_sha256=sha256(model_path),
                  test_predictions_sha256=sha256(OUT / "test_predictions.parquet"))
    write_json(OUT / "test.json", result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "validate", "fetch-test", "test"))
    args = parser.parse_args()
    {"freeze": freeze, "validate": validate, "fetch-test": fetch_test,
     "test": test}[args.stage]()


if __name__ == "__main__":
    main()
