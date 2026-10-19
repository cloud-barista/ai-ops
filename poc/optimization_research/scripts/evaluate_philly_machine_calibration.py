"""Pre-December machine calibration of the frozen Philly GPU-risk model.

`develop` may read only the already-studied [5,15) machines and writes the
selected settings. `audit` and `score` use the separately collected [15,25)
machines only after a frozen protocol and selection manifest are verified.
The outcome is a future *observed GPU-utilization event*, not a scale action.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.special import expit, logit
from scipy.optimize import brentq
from sklearn.metrics import average_precision_score

import evaluate_philly_machine_holdout as frozen
import prepare_philly_gpu_risk_candidates as builder
import philly_risk_metrics as risk_metrics
from fetch_philly_gpu_sample import EXPECTED_SHA256, EXPECTED_SIZE


ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/external/msr_philly_2017_holdout/gpu_util_normalized_first_width.parquet"
DEV_AUDIT = ROOT / "data/external/msr_philly_2017_holdout/gpu_util_normalized_first_width_audit.json"
NEW = ROOT / "data/external/msr_philly_2017_calibration_holdout/gpu_util_normalized_first_width.parquet"
NEW_AUDIT = ROOT / "data/external/msr_philly_2017_calibration_holdout/gpu_util_normalized_first_width_audit.json"
NEW_RAW_AUDIT = ROOT / "data/external/msr_philly_2017_calibration_holdout/gpu_util_calibration_holdout_audit.json"
OLD_DRY = ROOT / "artifacts/philly_gpu_risk_candidates_v1/dry_run_audit.json"
MODEL_SPEC = ROOT / "artifacts/philly_machine_holdout_model_v1/model_spec.json"
PROTOCOL = ROOT / "reports/philly_machine_calibration_protocol_frozen.md"
SELECTION = ROOT / "artifacts/philly_machine_calibration_v1/development_selection.json"
TEST_AUDIT = ROOT / "artifacts/philly_machine_calibration_v1/test_dry_run.json"
TEST_RESULT = ROOT / "artifacts/philly_machine_calibration_v1/test_metrics.json"
TEST_PREDICTIONS = ROOT / "artifacts/philly_machine_calibration_v1/test_predictions.parquet"
DEV_SHA = "146fe4318d6634120bdc10b2af9522be6c0a113c3213fb386a007b4d21edbdf8"
MODEL_SHA = "861005a57cbf9f370a8f36682ca9004a3301af01f45e9c797949bf6319ad76a3"
MIN_MACHINE_DAYS = 5
MIN_MACHINE_HOURLY = 30
MIN_GATE_DAYS = 3
MIN_GATE_HOURLY = 15
SAMPLE_PERIOD_S = 3600  # The 35-minute future windows do not overlap hourly.
SIGMAS = (0.5, 1.0, 2.0)
GATE_THRESHOLDS = (0.0, 0.002)
BASE_MODES = ("pooled", "fitted_global")
RATE_PRIOR_N = 50


class CalibrationGates(builder.GateAccumulator):
    """Keep only score-free class counts in the two fixed December halves."""

    def __init__(self, test_span: dict):
        super().__init__()
        start = int(test_span["start_utc_day"])
        end = int(test_span["end_utc_day_exclusive"])
        self.halves = ((start, start+7), (start+7, end))
        self.counts = [Counter(), Counter()]

    def add(self, frame: pd.DataFrame, future_complete: np.ndarray,
            positive_if_observed: np.ndarray) -> None:
        super().add(frame, future_complete, positive_if_observed)
        if frame.empty:
            return
        primary = (frame.split.eq("test").to_numpy()
                   & frame.cohort.eq("complete").to_numpy()
                   & frame.current_state.eq("below_80").to_numpy()
                   & future_complete)
        days = frame.utc_day.to_numpy(int)
        for i, (start, end) in enumerate(self.halves):
            use = primary & (days>=start) & (days<end)
            self.counts[i]["positive"] += int((use & positive_if_observed).sum())
            self.counts[i]["negative"] += int((use & ~positive_if_observed).sum())

    def half_counts(self) -> list[dict]:
        return [{"start_utc_day": a, "end_utc_day_exclusive": b,
                 "positive": self.counts[i]["positive"],
                 "negative": self.counts[i]["negative"]}
                for i, (a,b) in enumerate(self.halves)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def residue(machine_id: str) -> int:
    return int.from_bytes(hashlib.sha256(machine_id.encode("utf-8")).digest(), "big") % 100


def _load_model():
    if sha256(MODEL_SPEC) != MODEL_SHA:
        raise ValueError("Frozen original Philly model spec changed")
    return frozen.load_frozen_model_spec(json.loads(MODEL_SPEC.read_text()))


def _bounds() -> dict:
    return json.loads(OLD_DRY.read_text())["source_raw_utc_extrema_only_split"]


def examples_from_normalized(path: Path, *, low: int, high: int,
                             labels: bool,
                             allowed_machines: set[str] | None = None
                             ) -> tuple[pd.DataFrame, dict]:
    """Replay the original five-minute definition one machine at a time."""
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_parquet(path, columns=list(builder.INPUT_COLUMNS))
    if frame.empty or frame.source_row_index.duplicated().any():
        raise ValueError("Missing source rows or duplicate source indices")
    ids = frame.machine_id.astype(str).unique()
    if any(not low <= residue(mid) < high for mid in ids):
        raise ValueError("Sample contains a machine outside its frozen hash interval")
    source_rows, source_machines = len(frame), len(ids)
    if allowed_machines is not None:
        if not allowed_machines or not allowed_machines.issubset(set(ids)):
            raise ValueError("Quality-retained machine list differs from source")
        frame = frame.loc[frame.machine_id.isin(allowed_machines)]
        ids = frame.machine_id.astype(str).unique()
    bounds = _bounds()
    outputs = []
    counts = Counter()
    gates = CalibrationGates(bounds["spans"]["test"])
    test_quality = Counter()
    for mid, raw in frame.groupby("machine_id", sort=True):
        windows, _, win_summary = builder.minute_to_windows(raw)
        windows["machine_hash"] = builder.machine_hash(str(mid))
        window_quality = builder._window_quality(windows)
        q = builder.split_input_window_counts(window_quality, bounds)["test"]
        test_quality.update(q)
        candidates, futures, targets, summary = builder.construct_candidates(
            windows, bounds, gates, include_scoring_labels=labels)
        counts.update(win_summary)
        counts.update(summary)
        for candidate, future, target in zip(
                candidates, futures, targets if targets is not None else [None] * len(candidates)):
            # Only complete, currently below-threshold telemetry is evaluated.
            mask = (candidate.cohort.eq("complete") & candidate.current_state.eq("below_80"))
            if not mask.any():
                continue
            part = candidate.loc[mask].copy()
            part["future_complete"] = future.loc[mask, "future_complete"].to_numpy(bool)
            if target is not None:
                part["y"] = target.loc[mask, "y"].to_numpy()
            outputs.append(part)
    examples = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
    if examples.empty:
        raise ValueError("No primary-stratum candidate decisions")
    keys = ["machine_hash", "gpu_index", "decision_utc_s"]
    if examples.duplicated(keys).any():
        raise ValueError("Duplicate machine/slot/decision keys")
    if labels:
        observed = examples.loc[examples.future_complete].copy()
        if observed.y.isna().any() or not observed.y.isin((0, 1)).all():
            raise ValueError("Observed candidate lacks a binary event label")
        observed["y"] = observed.y.astype("int8")
        examples = observed
    return examples, {"source_rows": source_rows, "source_machines": source_machines,
                      "retained_rows": len(frame), "retained_machines": len(ids),
                      "candidate_counts": dict(counts), "primary_candidates": len(examples),
                      "gate_counts_by_split_cohort_state": gates.result(),
                      "test_half_gate_counts": gates.half_counts(),
                      "test_input_window_quality": dict(test_quality)}


def _day_balanced_weights(frame: pd.DataFrame) -> np.ndarray:
    sizes = frame.groupby("utc_day").utc_day.transform("size").to_numpy(float)
    return 1.0 / sizes


def offset_fit(frame: pd.DataFrame, base: np.ndarray, sigma: float) -> float:
    """Penalized logistic intercept with each active day weighted once."""
    if sigma <= 0 or frame.empty or len(frame) != len(base):
        raise ValueError("Invalid offset fit input")
    hourly = frame.decision_utc_s.to_numpy(np.int64) % SAMPLE_PERIOD_S == 0
    data, p = frame.loc[hourly], np.clip(base[hourly], 1e-6, 1-1e-6)
    if data.empty:
        return 0.0
    z = logit(p)
    y = data.y.to_numpy(float)
    weights = _day_balanced_weights(data)
    def derivative(delta: float) -> float:
        return float(np.dot(weights, expit(z + delta) - y) + delta / sigma**2)
    if derivative(-4) >= 0:
        return -4.0
    if derivative(4) <= 0:
        return 4.0
    return float(brentq(derivative, -4, 4, xtol=1e-10))


def _support(frame: pd.DataFrame, days: int, rows: int) -> bool:
    hourly = frame.loc[frame.decision_utc_s.mod(SAMPLE_PERIOD_S).eq(0)]
    return len(hourly) >= rows and hourly.utc_day.nunique() >= days


def _brier(frame: pd.DataFrame, p: np.ndarray) -> float:
    return float(np.mean((frame.y.to_numpy(float) - p)**2))


def make_predictions(frame: pd.DataFrame, model, *, sigma: float,
                     gate_threshold: float, global_delta: float,
                     global_prior: float,
                     fitted_global_delta: float | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Fit each machine using pre-December completed outcomes only."""
    all_frame = frame.copy()
    all_frame["pooled"] = model.predict_pooled(all_frame)
    all_frame["beta"] = model.predict_beta(all_frame)
    all_frame["global_offset"] = expit(logit(np.clip(all_frame.pooled, 1e-6, 1-1e-6))
                                         + global_delta)
    all_frame["global_fitted"] = expit(logit(np.clip(all_frame.pooled, 1e-6, 1-1e-6))
                                         + (global_delta if fitted_global_delta is None
                                            else fitted_global_delta))
    pieces = []
    decisions = []
    for machine, rows in all_frame.groupby("machine_hash", sort=True):
        history = rows.loc[rows.split.isin(("train", "validation"))]
        test = rows.loc[rows.split.eq("test")].copy()
        if test.empty:
            continue
        if history.y.isna().any():
            raise ValueError("Pre-test calibration contains unavailable labels")
        history_hourly = history.loc[history.decision_utc_s.mod(SAMPLE_PERIOD_S).eq(0)]
        supported = _support(history, MIN_MACHINE_DAYS, MIN_MACHINE_HOURLY)
        offset = (offset_fit(history, history.global_offset.to_numpy(float), sigma)
                  if supported else 0.0)
        local_rate = (float((history_hourly.y.sum() + RATE_PRIOR_N * global_prior)
                            / (len(history_hourly) + RATE_PRIOR_N))
                      if supported else global_prior)
        early = history.loc[history.split.eq("train")]
        late = history.loc[history.split.eq("validation")]
        gate_supported = (_support(early, MIN_GATE_DAYS, MIN_GATE_HOURLY)
                          and _support(late, MIN_GATE_DAYS, MIN_GATE_HOURLY))
        if gate_supported:
            early_delta = offset_fit(early, early.global_offset.to_numpy(float), sigma)
            late_base = late.global_offset.to_numpy(float)
            late_corrected = expit(logit(np.clip(late_base, 1e-6, 1-1e-6)) + early_delta)
            benefit = _brier(late, late_base) - _brier(late, late_corrected)
        else:
            early_delta = 0.0
            benefit = None
        gate_on = bool(supported and gate_supported and benefit > gate_threshold)
        z = logit(np.clip(test.global_offset.to_numpy(float), 1e-6, 1-1e-6))
        test["machine_offset"] = expit(z + offset)
        test["validated_offset"] = expit(z + (offset if gate_on else 0.0))
        test["machine_rate"] = local_rate
        pieces.append(test)
        decisions.append({"machine_hash": machine, "history_rows": len(history),
                          "history_hourly": len(history_hourly),
                          "history_days": int(history_hourly.utc_day.nunique()),
                          "supported": supported, "gate_supported": gate_supported,
                          "validation_brier_benefit": benefit,
                          "gate_on": gate_on, "offset": offset,
                          "early_offset": early_delta, "test_rows": len(test)})
    if not pieces:
        raise ValueError("No December test examples")
    return pd.concat(pieces, ignore_index=True), decisions


def fit_global(frame: pd.DataFrame, model) -> tuple[float, float, dict]:
    history = frame.loc[frame.split.isin(("train", "validation"))]
    if history.empty or history.y.isna().any():
        raise ValueError("Global calibration history is missing")
    p0 = model.predict_pooled(history)
    # A broad prior prevents one or two anomalous machine-days dominating.
    hourly = history.loc[history.decision_utc_s.mod(SAMPLE_PERIOD_S).eq(0)].copy()
    hourly["pooled"] = p0[history.decision_utc_s.mod(SAMPLE_PERIOD_S).eq(0).to_numpy()]
    day_size = hourly.groupby(["machine_hash", "utc_day"]).utc_day.transform("size").to_numpy(float)
    weights = 1.0 / day_size
    machine_days = hourly.groupby("machine_hash").utc_day.transform("nunique").to_numpy(float)
    weights /= machine_days
    z, y = logit(np.clip(hourly.pooled.to_numpy(float), 1e-6, 1-1e-6)), hourly.y.to_numpy(float)
    def derivative(delta):
        return float(np.dot(weights, expit(z+delta)-y) + delta / 2.0**2)
    delta = float(brentq(derivative, -4, 4)) if derivative(-4)<0<derivative(4) else 0.0
    prior = float(np.dot(weights, y) / weights.sum())
    return delta, prior, {"rows": len(history), "hourly": len(hourly),
                          "machines": int(hourly.machine_hash.nunique()),
                          "machine_days": int(hourly.groupby(["machine_hash", "utc_day"]).ngroups)}


def _machine_equal_brier(frame: pd.DataFrame, col: str) -> float:
    score = frame.assign(loss=(frame.y.to_numpy(float) - frame[col].to_numpy(float))**2)
    return float(score.groupby("machine_hash").loss.mean().mean())


def _score(frame: pd.DataFrame) -> dict:
    methods = ("pooled", "beta", "global_fitted", "machine_rate",
               "machine_offset", "validated_offset")
    actual = frame.y.to_numpy(int)
    output = {}
    for name in methods:
        machine = frame.groupby("machine_hash", sort=True).agg(
            observed=("y", "mean"), predicted=(name, "mean"))
        predicted = frame[name].to_numpy(float)
        output[name] = {
            "brier": _brier(frame, predicted),
            "machine_equal_brier": _machine_equal_brier(frame, name),
            "average_precision": (float(average_precision_score(actual, predicted))
                                  if np.unique(actual).size == 2 else None),
            "observed_rate": float(actual.mean()),
            "mean_probability": float(predicted.mean()),
            "absolute_calibration_gap": float(abs(actual.mean()-predicted.mean())),
            "machine_equal_absolute_calibration_gap": float(
                (machine.observed-machine.predicted).abs().mean()),
        }
    return output


def _candidate_fingerprint(frame: pd.DataFrame) -> str:
    """Stable hash of every decision-time value and its label observability."""
    columns = [*builder.CANDIDATE_SCHEMA.names, "future_complete"]
    ordered = frame.loc[:, columns].sort_values(
        ["machine_hash", "gpu_index", "decision_utc_s"], kind="mergesort")
    digest = hashlib.sha256("\n".join(columns).encode())
    digest.update(pd.util.hash_pandas_object(ordered, index=False).to_numpy("<u8").tobytes())
    return digest.hexdigest()


def choose_development(rows: list[dict]) -> tuple[dict, tuple[str, float | None, str | None]]:
    """Select the proposal and independently tuned strongest simple method."""
    if not rows:
        raise ValueError("Development candidate scores are empty")
    selected = min(rows, key=lambda r: (
        r["metrics"]["validated_offset"]["machine_equal_brier"],
        r["sigma"], -r["gate_threshold"], BASE_MODES.index(r["base_mode"])))
    options = []
    for method in ("pooled", "beta", "global_fitted", "machine_rate", "machine_offset"):
        for row in rows:
            if method != "machine_offset" and (
                    row["sigma"], row["gate_threshold"], row["base_mode"]
                    ) != (SIGMAS[0], GATE_THRESHOLDS[0], BASE_MODES[0]):
                continue
            if method == "machine_offset" and row["gate_threshold"] != GATE_THRESHOLDS[0]:
                continue
            options.append((row["metrics"][method]["machine_equal_brier"],
                            method, row["sigma"] if method == "machine_offset" else None,
                            row["base_mode"] if method == "machine_offset" else None))
    best = min(options, key=lambda x: (x[0], x[1], x[2] or 0,
                                       BASE_MODES.index(x[3]) if x[3] else 0))
    return selected, (best[1], best[2], best[3])


def test_gate_pass(primary: dict, halves: list[dict], *, candidates: int,
                   observed: int, machines: int, valid_windows: int,
                   observed_windows: int, retained_machines: int) -> bool:
    """Decide score eligibility from aggregate counts before fitting probabilities."""
    return bool(candidates and observed >= 2000 and machines >= 10
                and observed / candidates >= 0.5
                and observed_windows and valid_windows / observed_windows >= 0.5
                and primary["positive_gate_count"] >= 100
                and primary["negative_gate_count"] >= 100
                and primary["positive_machine_count"] >= 5
                and primary["negative_machine_count"] >= 5
                and primary["positive_utc_day_count"] >= 5
                and all(h["positive"] >= 1 and h["negative"] >= 1 for h in halves)
                and retained_machines > 0)


def develop(output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if sha256(DEV) != DEV_SHA or sha256(DEV_AUDIT) != "ee870efa49dd556b688cde7ab495edaf01eaaa26a15d9e7196d94d9beb8a00bc":
        raise ValueError("Development Philly sample differs from audited [5,15) material")
    examples, audit = examples_from_normalized(DEV, low=5, high=15, labels=True)
    model = _load_model()
    delta, prior, support = fit_global(examples, model)
    rows = []
    for base_mode in BASE_MODES:
        base_delta = 0.0 if base_mode == "pooled" else delta
        for sigma in SIGMAS:
            scored, decisions = make_predictions(
                examples, model, sigma=sigma, gate_threshold=GATE_THRESHOLDS[0],
                global_delta=base_delta, global_prior=prior,
                fitted_global_delta=delta)
            for threshold in GATE_THRESHOLDS:
                accepted = {d["machine_hash"] for d in decisions
                            if d["gate_supported"] and d["supported"]
                            and d["validation_brier_benefit"] > threshold}
                scored["validated_offset"] = np.where(
                    scored.machine_hash.isin(accepted),
                    scored.machine_offset, scored.global_offset)
                metrics = _score(scored)
                rows.append({"base_mode": base_mode, "sigma": sigma,
                             "gate_threshold": threshold,
                             "metrics": metrics, "gated_machines": len(accepted),
                             "support_machines": sum(x["supported"] for x in decisions)})
    # Machine-equal Brier chooses complexity; exact ties prefer more shrinkage,
    # then the stricter gate. All candidates and the strongest simple baseline
    # remain visible in the manifest.
    selected, best_simple = choose_development(rows)
    result = {"kind": "philly_calibration_development_selection",
              "developed_from": "[5,15) machines; their December scores were previously seen",
              "source_sha256": DEV_SHA, "model_spec_sha256": MODEL_SHA,
              "script_sha256": sha256(Path(__file__)),
              "builder_sha256": sha256(Path(builder.__file__)),
              "bounds_sha256": sha256(OLD_DRY),
              "global_offset": delta, "global_prior": prior,
              "global_support": support, "extract_audit": audit,
              "candidates": rows,
              "selected_base_mode": selected["base_mode"],
              "selected_sigma": selected["sigma"],
              "selected_gate_threshold": selected["gate_threshold"],
              "primary_simple_baseline": best_simple[0],
              "primary_simple_baseline_sigma": best_simple[1],
              "primary_simple_baseline_base_mode": best_simple[2],
              "created_utc": datetime.now(timezone.utc).isoformat()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+"\n")
    return result


def _verify_freeze(expected_protocol: str, expected_selection: str) -> dict:
    if sha256(PROTOCOL) != expected_protocol or sha256(SELECTION) != expected_selection:
        raise ValueError("Pre-collection frozen protocol or selection hash changed")
    protocol = PROTOCOL.read_text()
    selection = json.loads(SELECTION.read_text())
    if "**상태: FROZEN" not in protocol:
        raise ValueError("Protocol is not frozen")
    for path in (Path(__file__), ROOT / "scripts/fetch_philly_machine_calibration.py",
                 ROOT / "scripts/normalize_philly_gpu_sample.py",
                 Path(builder.__file__), MODEL_SPEC, SELECTION):
        if sha256(path) not in protocol:
            raise ValueError(f"Frozen protocol omits current hash of {path.name}")
    if (selection.get("script_sha256") != sha256(Path(__file__))
            or selection.get("model_spec_sha256") != MODEL_SHA
            or selection.get("source_sha256") != DEV_SHA
            or selection.get("bounds_sha256") != sha256(OLD_DRY)
            or selection.get("builder_sha256") != sha256(Path(builder.__file__))):
        raise ValueError("Development selection differs from frozen source/model/code")
    selected, baseline = choose_development(selection["candidates"])
    if (selection.get("selected_base_mode") != selected["base_mode"]
            or selection.get("selected_sigma") != selected["sigma"]
            or selection.get("selected_gate_threshold") != selected["gate_threshold"]
            or selection.get("primary_simple_baseline") != baseline[0]
            or selection.get("primary_simple_baseline_sigma") != baseline[1]
            or selection.get("primary_simple_baseline_base_mode") != baseline[2]):
        raise ValueError("Development selection is inconsistent with candidate scores")
    return selection


def _verify_new_source() -> dict:
    raw = json.loads(NEW_RAW_AUDIT.read_text())
    norm = json.loads(NEW_AUDIT.read_text())
    raw_path = NEW_RAW_AUDIT.parent / "gpu_util_machine_hash15to24pct.parquet"
    collector = ROOT / "scripts/fetch_philly_machine_calibration.py"
    normalizer = ROOT / "scripts/normalize_philly_gpu_sample.py"
    expected_freeze = {
        "frozen_calibration_protocol_sha256": sha256(PROTOCOL),
        "development_selection_sha256": sha256(SELECTION),
        "holdout_collector_script_sha256": sha256(collector),
    }
    if (not raw.get("complete") or not raw.get("integrity_pass")
            or raw.get("compressed_sha256") != EXPECTED_SHA256
            or raw.get("compressed_bytes_read") != EXPECTED_SIZE
            or raw.get("pre_fetch_freeze") != expected_freeze
            or raw.get("collector_script_sha256") != sha256(collector)
            or raw.get("sample_rule")
               != "15 <= int.from_bytes(SHA256(machineId UTF-8), 'big') % 100 < 25"
            or raw.get("sample_parquet") != str(raw_path)
            or raw.get("sample_sha256") != sha256(raw_path)
            or raw.get("sample_bytes") != raw_path.stat().st_size
            or raw.get("gpu_csv", {}).get("selected_rows")
               != pq.ParquetFile(raw_path).metadata.num_rows):
        raise ValueError("New sample lacks full pinned archive integrity")
    if (not norm.get("integrity_pass") or norm.get("source_sha256") != raw.get("sample_sha256")
            or norm.get("output_sha256") != sha256(NEW)
            or norm.get("output_parquet") != str(NEW)
            or norm.get("source_parquet") != str(raw_path)
            or norm.get("source_audit") != str(NEW_RAW_AUDIT)
            or norm.get("normalization_script_sha256") != sha256(normalizer)
            or norm.get("rows") != raw["gpu_csv"]["selected_rows"]
            or norm.get("rows") != pq.ParquetFile(NEW).metadata.num_rows
            or norm.get("source_row_index_sequence_sha256")
               != norm.get("derived_row_index_sequence_sha256")):
        raise ValueError("Normalized new-machine lineage failed")
    ids = set(raw["gpu_csv"]["selected_machine_ids"])
    old = json.loads((ROOT / "data/external/msr_philly_2017_holdout/gpu_util_holdout_audit.json").read_text())
    old_ids = set(old["gpu_csv"]["selected_machine_ids"])
    older = json.loads((ROOT / "data/external/msr_philly_2017/gpu_util_audit.json").read_text())
    old_ids.update(older["gpu_csv"]["selected_machine_ids"])
    if not ids or ids & old_ids or any(not 15 <= residue(x) < 25 for x in ids):
        raise ValueError("New-machine ID selection overlaps development")
    if set(norm["machines"]) != ids:
        raise ValueError("Normalized/new raw machine inventory differs")
    return {"raw": raw, "normalized": norm}


def audit_test(expected_protocol: str, expected_selection: str, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    _verify_freeze(expected_protocol, expected_selection)
    lineage = _verify_new_source()
    quality = lineage["normalized"]["machines"]
    width_pass = {mid: item["invalid_rows"] / item["rows"] <= 0.01
                  for mid, item in quality.items()}
    retained = {mid for mid, passed in width_pass.items() if passed}
    examples, extract = examples_from_normalized(
        NEW, low=15, high=25, labels=False, allowed_machines=retained)
    test = examples.loc[examples.split.eq("test")]
    # Gate is score-free; future-complete flags only, no row-level outcomes.
    observed = test.loc[test.future_complete]
    primary_gate = next((g for g in extract["gate_counts_by_split_cohort_state"]
                         if (g["split"], g["cohort"], g["current_state"])
                         == ("test", "complete", "below_80")), None)
    if primary_gate is None or primary_gate["future_complete"] != len(observed):
        raise ValueError("Test primary gate differs from candidate observability")
    half_gates = extract["test_half_gate_counts"]
    if sum(h["positive"]+h["negative"] for h in half_gates) != len(observed):
        raise ValueError("Test half event counts differ from complete candidates")
    input_quality = extract["test_input_window_quality"]
    observed_windows = input_quality.get("observed_window_slots", 0)
    valid_windows = input_quality.get("valid_window_slots", 0)
    if valid_windows > observed_windows:
        raise ValueError("Test input window quality counts are inconsistent")
    result = {"kind": "test_dry_run_no_row_labels_or_scores",
              "protocol_sha256": expected_protocol,
              "selection_sha256": expected_selection,
              "raw_audit_sha256": sha256(NEW_RAW_AUDIT),
              "normalized_audit_sha256": sha256(NEW_AUDIT),
              "normalized_source_sha256": sha256(NEW),
              "extract": extract,
              "test_candidates": len(test), "test_future_complete": len(observed),
              "test_future_complete_fraction": len(observed)/len(test) if len(test) else 0.0,
              "test_observed_machines": int(observed.machine_hash.nunique()),
              "test_primary_gate": primary_gate,
              "test_half_gate_counts": half_gates,
              "test_valid_window_fraction": valid_windows/observed_windows if observed_windows else 0.0,
              "test_candidate_feature_observability_sha256": _candidate_fingerprint(test),
              "width_valid_machines": sum(width_pass.values()),
              "width_invalid_machines": sum(not x for x in width_pass.values()),
              "excluded_machine_hashes": sorted(builder.machine_hash(mid)
                                                for mid, passed in width_pass.items() if not passed),
              "passes": test_gate_pass(
                  primary_gate, half_gates, candidates=len(test),
                  observed=len(observed), machines=int(observed.machine_hash.nunique()),
                  valid_windows=valid_windows, observed_windows=observed_windows,
                  retained_machines=len(retained))}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    return result


def score_test(expected_protocol: str, expected_selection: str,
               expected_audit: str, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    selection = _verify_freeze(expected_protocol, expected_selection)
    if sha256(TEST_AUDIT) != expected_audit:
        raise ValueError("Test dry-run audit hash changed")
    dry = json.loads(TEST_AUDIT.read_text())
    if not dry["passes"]:
        raise ValueError("Pre-score test quality gate failed")
    if (dry.get("raw_audit_sha256") != sha256(NEW_RAW_AUDIT)
            or dry.get("normalized_audit_sha256") != sha256(NEW_AUDIT)
            or dry.get("normalized_source_sha256") != sha256(NEW)):
        raise ValueError("New source differs from the score-free dry run")
    _verify_new_source()
    quality = json.loads(NEW_AUDIT.read_text())["machines"]
    retained = {mid for mid, item in quality.items()
                if item["invalid_rows"] / item["rows"] <= 0.01}
    before_labels, before_extract = examples_from_normalized(
        NEW, low=15, high=25, labels=False, allowed_machines=retained)
    before_test = before_labels.loc[before_labels.split.eq("test")]
    if (_candidate_fingerprint(before_test)
            != dry.get("test_candidate_feature_observability_sha256")
            or before_extract["gate_counts_by_split_cohort_state"]
               != dry["extract"]["gate_counts_by_split_cohort_state"]):
        raise ValueError("Score-free test candidates or future observation changed")
    examples, extract = examples_from_normalized(
        NEW, low=15, high=25, labels=True, allowed_machines=retained)
    if (extract["gate_counts_by_split_cohort_state"]
            != dry["extract"]["gate_counts_by_split_cohort_state"]):
        raise ValueError("Observed label replay differs from frozen aggregate gate")
    model = _load_model()
    selected_delta = (0.0 if selection["selected_base_mode"] == "pooled"
                      else float(selection["global_offset"]))
    scored, decisions = make_predictions(
        examples, model, sigma=float(selection["selected_sigma"]),
        gate_threshold=float(selection["selected_gate_threshold"]),
        global_delta=selected_delta,
        global_prior=float(selection["global_prior"]),
        fitted_global_delta=float(selection["global_offset"]))
    if selection["primary_simple_baseline"] == "machine_offset":
        baseline_sigma = float(selection["primary_simple_baseline_sigma"])
        baseline_mode = selection["primary_simple_baseline_base_mode"]
        if (baseline_sigma != float(selection["selected_sigma"])
                or baseline_mode != selection["selected_base_mode"]):
            baseline_scored, _ = make_predictions(
                examples, model, sigma=baseline_sigma,
                gate_threshold=float(selection["selected_gate_threshold"]),
                global_delta=(0.0 if baseline_mode == "pooled"
                              else float(selection["global_offset"])),
                global_prior=float(selection["global_prior"]),
                fitted_global_delta=float(selection["global_offset"]))
            keys = ["machine_hash", "gpu_index", "decision_utc_s"]
            if not scored[keys].equals(baseline_scored[keys]):
                raise ValueError("Baseline/proposed test decision keys differ")
            scored["machine_offset"] = baseline_scored.machine_offset.to_numpy(float)
    if len(scored) != dry["test_future_complete"]:
        raise ValueError("Scored test rows differ from dry-run observed rows")
    metrics = _score(scored)
    primary = selection["primary_simple_baseline"]
    bootstrap = risk_metrics.paired_machine_bootstrap(
        scored, "validated_offset", primary, draws=2000, seed=43)
    span = _bounds()["spans"]["test"]
    start = int(span["start_utc_day"])
    stop = int(span["end_utc_day_exclusive"])
    midpoint = start + 7
    halves = []
    for left, right in ((start, midpoint), (midpoint, stop)):
        rows = scored.loc[scored.utc_day.ge(left) & scored.utc_day.lt(right)]
        halves.append({"start_utc_day": left, "end_utc_day_exclusive": right,
                       "rows": len(rows),
                       "proposed_brier": None if rows.empty else _brier(rows, rows.validated_offset.to_numpy(float)),
                       "baseline_brier": None if rows.empty else _brier(rows, rows[primary].to_numpy(float))})
    by_machine = []
    for mid, rows in scored.groupby("machine_hash", sort=True):
        by_machine.append({"machine_hash": mid, "rows": len(rows),
                           "observed_rate": float(rows.y.mean()),
                           "proposed_mean": float(rows.validated_offset.mean()),
                           "baseline_mean": float(rows[primary].mean()),
                           "proposed_brier": _brier(rows, rows.validated_offset.to_numpy(float)),
                           "baseline_brier": _brier(rows, rows[primary].to_numpy(float))})
    by_day = []
    for day, rows in scored.groupby("utc_day", sort=True):
        by_day.append({"utc_day": int(day), "rows": len(rows),
                       "observed_rate": float(rows.y.mean()),
                       "proposed_mean": float(rows.validated_offset.mean()),
                       "baseline_mean": float(rows[primary].mean()),
                       "proposed_brier": _brier(rows, rows.validated_offset.to_numpy(float)),
                       "baseline_brier": _brier(rows, rows[primary].to_numpy(float))})
    relative = 1 - metrics["validated_offset"]["brier"]/metrics[primary]["brier"]
    passes = (relative>=0.05 and bootstrap["percentile_95"][1]<0
              and metrics["validated_offset"]["machine_equal_brier"]
                 < metrics[primary]["machine_equal_brier"]
              and metrics["validated_offset"]["machine_equal_absolute_calibration_gap"]
                 <= metrics[primary]["machine_equal_absolute_calibration_gap"]
              and all(h["proposed_brier"] is not None
                      and h["proposed_brier"]<h["baseline_brier"] for h in halves))
    result = {"kind": "philly_disjoint_machine_calibration_test",
              "protocol_sha256": expected_protocol,
              "selection_sha256": expected_selection,
              "dry_run_sha256": expected_audit,
              "extract": extract, "rows": len(scored),
              "machines": scored.machine_hash.nunique(),
              "metrics": metrics, "selected_base_mode": selection["selected_base_mode"],
              "primary_simple_baseline": primary,
              "primary_simple_baseline_sigma": selection["primary_simple_baseline_sigma"],
              "primary_simple_baseline_base_mode": selection["primary_simple_baseline_base_mode"],
              "relative_brier_improvement": relative,
              "paired_machine_bootstrap": bootstrap,
              "calendar_halves": halves,
              "by_day": by_day,
              "by_machine": by_machine,
              "gate_decisions": decisions,
              "gated_machines": sum(x["gate_on"] for x in decisions),
              "method_passes_frozen_gate": passes}
    output.parent.mkdir(parents=True, exist_ok=True)
    if TEST_PREDICTIONS.exists():
        raise FileExistsError(TEST_PREDICTIONS)
    scored.to_parquet(TEST_PREDICTIONS, index=False)
    result["test_predictions_sha256"] = sha256(TEST_PREDICTIONS)
    output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("develop", "audit", "score"))
    parser.add_argument("--expected-protocol-sha256")
    parser.add_argument("--expected-selection-sha256")
    parser.add_argument("--expected-audit-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.stage == "develop":
        result = develop(args.output or SELECTION)
    elif args.stage == "audit":
        result = audit_test(args.expected_protocol_sha256,
                            args.expected_selection_sha256, args.output or TEST_AUDIT)
    else:
        result = score_test(args.expected_protocol_sha256,
                            args.expected_selection_sha256,
                            args.expected_audit_sha256, args.output or TEST_RESULT)
    print(json.dumps({"stage": args.stage, "kind": result["kind"],
                      "output": str(args.output or {
                          "develop": SELECTION, "audit": TEST_AUDIT,
                          "score": TEST_RESULT}[args.stage])}, sort_keys=True))


if __name__ == "__main__":
    main()
