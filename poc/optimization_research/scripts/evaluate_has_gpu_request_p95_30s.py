#!/usr/bin/env python3
"""Frozen, request-only HAS-GPU 30-second HTTP latency evaluation.

Workflow: freeze -> count_gate -> score. Do not run any step before protocol
review. The count gate does not calculate latency percentiles or model scores.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
PROTOCOL = ROOT / "reports" / "has_gpu_request_p95_30s_protocol.md"
TESTS = ROOT / "tests" / "test_has_gpu_request_p95_30s.py"
AUDIT = ROOT / "artifacts" / "has_gpu_clock_ranges_v1.json"
OUTPUT = ROOT / "artifacts" / "has_gpu_request_p95_30s_v1"
MANIFEST = OUTPUT / "frozen_manifest.json"
GATE = OUTPUT / "count_gate.json"
RESULTS = OUTPUT / "scores.json"
CACHE = OUTPUT / "source_csv"

GITHUB_COMMIT = "31f8ea29ca184193dbc7523736d77c817ddce980"
SOURCE_PREFIX = "HAS-GPU/validation/result/HAS/"
FAMILIES = ("bert", "resnet", "convnext", "mobilenet")
EXPECTED_RUNS_BY_FAMILY = {"bert": 8, "resnet": 4, "convnext": 3, "mobilenet": 3}
EXPECTED_ZIP_SIZE = 62_262_205
EXPECTED_ZIP_MD5 = "md5:b8da5cea33e59b9b21dab8163633118a"
EXPECTED_REQUEST_COLUMNS = ["duration", "mode", "status_code", "success", "timestamp"]
SECOND_US = 1_000_000
WINDOW_US = 30 * SECOND_US
RIGHT_MARGIN_US = 30 * SECOND_US
MIN_REQUESTS = 100
MIN_RUNS_PER_FAMILY = 3
MIN_WINDOWS_PER_FAMILY = 15
RIDGE_ALPHA = 10.0
RATE_PSEUDOCOUNT_PER_10S = 1.0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_new_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def selected_sources(audit: dict) -> list[dict]:
    if (audit.get("selected_runs") != 18
            or audit.get("zip_size") != EXPECTED_ZIP_SIZE
            or audit.get("zip_published_md5_unverified") != EXPECTED_ZIP_MD5):
        raise ValueError("HAS-GPU audit or Zenodo ZIP identity changed")
    sources = []
    for item in audit["runs"]:
        # Deliberately whitelist request fields; the audit's resource records
        # are irrelevant to this request-only experiment.
        source = {key: item[key] for key in
                  ("run", "model_family", "request_member", "request_sha256", "request_rows")}
        if (not re.fullmatch(r"[a-z0-9-]+", source["run"])
                or not source["request_member"].startswith(SOURCE_PREFIX)
                or not re.fullmatch(r"[0-9a-f]{64}", source["request_sha256"])
                or not isinstance(source["request_rows"], int)
                or source["request_rows"] < MIN_REQUESTS):
            raise ValueError(f"invalid request source entry: {source}")
        sources.append(source)
    if (len(sources) != 18
            or len({s["run"] for s in sources}) != 18
            or Counter(s["model_family"] for s in sources) != EXPECTED_RUNS_BY_FAMILY
            or sum(s["request_rows"] for s in sources) != audit.get("selected_request_rows")):
        raise ValueError("unexpected request run inventory")
    return sorted(sources, key=lambda s: s["run"])


def freeze() -> None:
    """Create the reviewed source/code/protocol manifest; never overwrite it."""
    audit = read_json(AUDIT)
    manifest = {
        "protocol_version": "has_gpu_request_p95_30s_v1",
        "source_record": audit["record_url"],
        "source_record_api": audit["record_api"],
        "source_zip_size": EXPECTED_ZIP_SIZE,
        "source_zip_published_md5_unverified": EXPECTED_ZIP_MD5,
        "github_commit": GITHUB_COMMIT,
        "audit_sha256": sha256_file(AUDIT),
        "protocol_sha256": sha256_file(PROTOCOL),
        "script_sha256": sha256_file(SCRIPT),
        "tests_sha256": sha256_file(TESTS),
        "sources": selected_sources(audit),
    }
    write_new_json(MANIFEST, manifest)
    print(MANIFEST)


def checked_manifest() -> dict:
    manifest = read_json(MANIFEST)
    expected = {
        "audit_sha256": sha256_file(AUDIT),
        "protocol_sha256": sha256_file(PROTOCOL),
        "script_sha256": sha256_file(SCRIPT),
        "tests_sha256": sha256_file(TESTS),
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("frozen audit, protocol, script, or tests changed")
    if manifest.get("github_commit") != GITHUB_COMMIT:
        raise ValueError("pinned GitHub commit changed")
    if manifest.get("sources") != selected_sources(read_json(AUDIT)):
        raise ValueError("frozen request sources changed")
    return manifest


def fetch_source(source: dict) -> bytes:
    member = source["request_member"]
    path = member.removeprefix("HAS-GPU/")
    url = f"https://raw.githubusercontent.com/KontonGu/HAS-GPU/{GITHUB_COMMIT}/{quote(path, safe='/')}"
    with urlopen(Request(url, headers={"User-Agent": "KHU-AI-MCMP-research"}), timeout=60) as response:
        data = response.read()
    if sha256_bytes(data) != source["request_sha256"]:
        raise ValueError(f"source SHA-256 mismatch: {member}")
    return data


def cached_source(source: dict, *, download: bool) -> bytes:
    path = CACHE / f"{source['run']}.csv"
    if path.exists():
        data = path.read_bytes()
    elif download:
        data = fetch_source(source)
        CACHE.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
    else:
        raise FileNotFoundError(path)
    if sha256_bytes(data) != source["request_sha256"]:
        raise ValueError(f"cached source SHA-256 mismatch: {path}")
    return data


def naive_epoch_microseconds(value: str) -> int:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is not None:
        raise ValueError(f"expected timezone-naive request timestamp: {value}")
    delta = instant - datetime(1970, 1, 1)
    return ((delta.days * 86_400 + delta.seconds) * SECOND_US
            + delta.microseconds)


@dataclass(frozen=True)
class RequestTrace:
    run: str
    family: str
    completion_us: np.ndarray
    completion_duration_s: np.ndarray
    start_us: np.ndarray
    start_duration_s: np.ndarray


def parse_request_csv(data: bytes, source: dict) -> RequestTrace:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if reader.fieldnames != EXPECTED_REQUEST_COLUMNS:
        raise ValueError(f"unexpected request CSV columns: {reader.fieldnames}")
    completions, durations = [], []
    expected_mode = "bert" if source["model_family"] == "bert" else "image"
    for row in reader:
        if (row["status_code"] != "200" or row["success"] != "True"
                or row["mode"] != expected_mode):
            raise ValueError(f"unexpected request outcome or mode: {source['run']}")
        duration = float(row["duration"])
        if not 0 < duration <= 30 or not math.isfinite(duration):
            raise ValueError("duration must be finite, positive, and within right margin")
        completions.append(naive_epoch_microseconds(row["timestamp"]))
        durations.append(duration)
    if len(completions) != source["request_rows"]:
        raise ValueError(f"request row count mismatch: {source['run']}")
    completion_us = np.asarray(completions, dtype=np.int64)
    duration_s = np.asarray(durations, dtype=np.float64)
    if np.any(np.diff(completion_us) < 0):
        raise ValueError(f"request completion row order regressed: {source['run']}")
    start_us = completion_us - np.rint(duration_s * SECOND_US).astype(np.int64)
    order = np.argsort(start_us, kind="stable")
    return RequestTrace(source["run"], source["model_family"], completion_us,
                        duration_s, start_us[order], duration_s[order])


@dataclass(frozen=True)
class Anchor:
    t_us: int
    past_lo: int
    past_hi: int
    future_lo: int
    future_hi: int
    recent_count: int

    @property
    def past_count(self) -> int:
        return self.past_hi - self.past_lo

    @property
    def future_count(self) -> int:
        return self.future_hi - self.future_lo


def count_anchors(trace: RequestTrace) -> tuple[dict, list[Anchor]]:
    """Select windows using counts only; do not compute any latency quantile."""
    ends, starts = trace.completion_us, trace.start_us
    t_us = int(ends[0]) + WINDOW_US
    last_us = int(ends[-1])
    counts = {"candidate_anchors": 0, "past_count_pass": 0,
              "future_count_pass": 0, "eligible_anchors": 0}
    eligible = []
    while t_us + WINDOW_US + RIGHT_MARGIN_US <= last_us:
        past_lo = int(np.searchsorted(ends, t_us - WINDOW_US, side="right"))
        past_hi = int(np.searchsorted(ends, t_us, side="right"))
        future_lo = int(np.searchsorted(starts, t_us, side="right"))
        future_hi = int(np.searchsorted(starts, t_us + WINDOW_US, side="right"))
        recent_lo = int(np.searchsorted(ends, t_us - 10 * SECOND_US, side="right"))
        anchor = Anchor(t_us, past_lo, past_hi, future_lo, future_hi,
                        past_hi - recent_lo)
        counts["candidate_anchors"] += 1
        counts["past_count_pass"] += int(anchor.past_count >= MIN_REQUESTS)
        counts["future_count_pass"] += int(anchor.future_count >= MIN_REQUESTS)
        if anchor.past_count >= MIN_REQUESTS and anchor.future_count >= MIN_REQUESTS:
            eligible.append(anchor)
            counts["eligible_anchors"] += 1
        t_us += WINDOW_US
    return counts, eligible


def coverage_report(run_counts: list[dict]) -> dict:
    families = []
    for family in FAMILIES:
        rows = [r for r in run_counts if r["family"] == family]
        usable_runs = sum(r["eligible_anchors"] > 0 for r in rows)
        eligible = sum(r["eligible_anchors"] for r in rows)
        families.append({"family": family, "runs": len(rows),
                         "runs_with_eligible_windows": usable_runs,
                         "candidate_anchors": sum(r["candidate_anchors"] for r in rows),
                         "eligible_anchors": eligible,
                         "passed": usable_runs >= MIN_RUNS_PER_FAMILY
                                   and eligible >= MIN_WINDOWS_PER_FAMILY})
    return {"runs": run_counts, "families": families,
            "passed": all(item["passed"] for item in families)}


def count_gate() -> None:
    manifest = checked_manifest()
    run_counts = []
    for source in manifest["sources"]:
        trace = parse_request_csv(cached_source(source, download=True), source)
        counts, _ = count_anchors(trace)
        run_counts.append({"run": source["run"], "family": source["model_family"],
                           "request_rows": source["request_rows"], **counts})
    report = coverage_report(run_counts)
    report["manifest_sha256"] = sha256_file(MANIFEST)
    report["selection"] = "past completions and future reconstructed starts >=100"
    write_new_json(GATE, report)
    print(GATE)
    if not report["passed"]:
        raise RuntimeError("count-only eligibility gate failed; do not score this protocol")


@dataclass(frozen=True)
class Example:
    run: str
    family: str
    t_us: int
    features: np.ndarray
    y_log_ratio: float
    past_p95_s: float
    future_p95_s: float


def make_examples(trace: RequestTrace, anchors: list[Anchor]) -> list[Example]:
    examples = []
    for anchor in anchors:
        past = trace.completion_duration_s[anchor.past_lo:anchor.past_hi]
        future = trace.start_duration_s[anchor.future_lo:anchor.future_hi]
        past_p95 = float(np.percentile(past, 95, method="linear"))
        past_p50 = float(np.percentile(past, 50, method="linear"))
        future_p95 = float(np.percentile(future, 95, method="linear"))
        preceding20_count = anchor.past_count - anchor.recent_count
        recent_rate = (anchor.recent_count + RATE_PSEUDOCOUNT_PER_10S) / 10
        preceding_rate = (preceding20_count + 2 * RATE_PSEUDOCOUNT_PER_10S) / 20
        features = np.log(np.array([past_p95, past_p50, anchor.past_count,
                                    recent_rate / preceding_rate], dtype=np.float64))
        examples.append(Example(trace.run, trace.family, anchor.t_us, features,
                                math.log(future_p95 / past_p95), past_p95, future_p95))
    return examples


@dataclass(frozen=True)
class FittedRidge:
    x_mean: np.ndarray
    x_scale: np.ndarray
    y_mean: float
    coefficients: np.ndarray

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.y_mean + ((x - self.x_mean) / self.x_scale) @ self.coefficients


def fit_ridge_train_only(x: np.ndarray, y: np.ndarray) -> FittedRidge:
    if x.ndim != 2 or x.shape[1] != 4 or len(x) != len(y) or len(y) == 0:
        raise ValueError("expected nonempty four-feature training matrix")
    x_mean = np.mean(x, axis=0)
    x_scale = np.std(x, axis=0, ddof=0)
    x_scale = np.where(x_scale == 0, 1.0, x_scale)
    z = (x - x_mean) / x_scale
    y_mean = float(np.mean(y))
    coefficients = np.linalg.solve(z.T @ z + RIDGE_ALPHA * np.eye(4),
                                   z.T @ (y - y_mean))
    return FittedRidge(x_mean, x_scale, y_mean, coefficients)


def prediction_metrics(true_log_ratio: np.ndarray, predicted_log_ratio: np.ndarray) -> dict:
    error = predicted_log_ratio - true_log_ratio
    return {"male": float(np.mean(np.abs(error))),
            "severe_underprediction_rate": float(np.mean(error < math.log(0.8)))}


def evaluate(examples: list[Example]) -> dict:
    per_run = []
    fold_training_windows = {}
    for heldout in FAMILIES:
        train = [ex for ex in examples if ex.family != heldout]
        test = [ex for ex in examples if ex.family == heldout]
        if not train or not test:
            raise ValueError(f"empty leave-family-out fold: {heldout}")
        x_train = np.stack([ex.features for ex in train])
        y_train = np.array([ex.y_log_ratio for ex in train])
        model = fit_ridge_train_only(x_train, y_train)
        mean_log_ratio = float(np.mean(y_train))
        fold_training_windows[heldout] = len(train)
        for run in sorted({ex.run for ex in test}):
            run_examples = [ex for ex in test if ex.run == run]
            x_test = np.stack([ex.features for ex in run_examples])
            y_test = np.array([ex.y_log_ratio for ex in run_examples])
            predictions = {
                "persistence": np.zeros(len(run_examples)),
                "training_mean_log_ratio": np.full(len(run_examples), mean_log_ratio),
                "ridge_alpha_10": model.predict(x_test),
            }
            per_run.append({"run": run, "family": heldout, "windows": len(run_examples),
                            "methods": {name: prediction_metrics(y_test, pred)
                                        for name, pred in predictions.items()}})
    method_names = ("persistence", "training_mean_log_ratio", "ridge_alpha_10")
    metric_names = ("male", "severe_underprediction_rate")
    per_family = []
    for family in FAMILIES:
        rows = [row for row in per_run if row["family"] == family]
        per_family.append({"family": family, "runs": len(rows),
                           "windows": sum(row["windows"] for row in rows),
                           "methods": {method: {metric: float(np.mean([
                               row["methods"][method][metric] for row in rows]))
                               for metric in metric_names} for method in method_names}})
    macro = {method: {metric: float(np.mean([
        row["methods"][method][metric] for row in per_family]))
        for metric in metric_names} for method in method_names}
    return {"fold_training_windows": fold_training_windows,
            "per_run": per_run, "per_family_equal_run": per_family,
            "equal_family_macro": macro}


def score() -> None:
    manifest = checked_manifest()
    gate = read_json(GATE)
    if not gate.get("passed") or gate.get("manifest_sha256") != sha256_file(MANIFEST):
        raise ValueError("passing count gate for this exact manifest is required")
    run_counts = []
    examples = []
    for source in manifest["sources"]:
        trace = parse_request_csv(cached_source(source, download=False), source)
        counts, anchors = count_anchors(trace)
        run_counts.append({"run": source["run"], "family": source["model_family"],
                           "request_rows": source["request_rows"], **counts})
        examples.extend(make_examples(trace, anchors))
    if coverage_report(run_counts) != {key: gate[key] for key in ("runs", "families", "passed")}:
        raise ValueError("count gate and scoring window inventory differ")
    report = {
        "manifest_sha256": sha256_file(MANIFEST),
        "count_gate_sha256": sha256_file(GATE),
        "task": "30-second future-start HTTP duration p95 on active-load windows",
        "metric_definition": "MALE=mean absolute log predicted/observed p95; severe=predicted<0.8 observed",
        **evaluate(examples),
    }
    write_new_json(RESULTS, report)
    print(RESULTS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "count_gate", "score"))
    command = parser.parse_args().command
    {"freeze": freeze, "count_gate": count_gate, "score": score}[command]()


if __name__ == "__main__":
    main()
