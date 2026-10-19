#!/usr/bin/env python3
"""Frozen train-only gate and later held-out Chutes demand experiment."""

from __future__ import annotations

import argparse
from collections import deque
import gc
import hashlib
import json
import math
from pathlib import Path
import shutil

import fsspec
import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import evaluate_chutes_demand_p90_v1 as v1


ROOT = Path("artifacts/chutes_demand_p90_v2")
DATA = Path("data/external/chutes_demand_p90_v2")
PROTOCOL = Path("reports/chutes_demand_p90_v2_protocol.md")
SCRIPT = Path("scripts/evaluate_chutes_demand_p90_v2.py")
TESTS = Path("tests/test_evaluate_chutes_demand_p90_v2.py")
FROZEN = ROOT / "frozen_manifest.json"
CALIBRATION = ROOT / "calibration.json"
GATE = ROOT / "gate.json"
GATE_PRED = ROOT / "gate_predictions.parquet"
F0_ABS = ROOT / "f0_absolute.joblib"
F0_REL = ROOT / "f0_relative.joblib"
G_ABS = ROOT / "gate_absolute.joblib"
G_REL = ROOT / "gate_relative.joblib"
TEST_PREP = ROOT / "test_prepared.json"
TEST_ABS = ROOT / "test_absolute.joblib"
TEST_REL = ROOT / "test_relative.joblib"
EXTRACTED = ROOT / "test_extracted_sha256.json"
TEST = ROOT / "test.json"
TEST_PRED = ROOT / "test_predictions.parquet"

F0 = (pd.Timestamp("1970-12-27 23:15"), pd.Timestamp("1970-12-28 04:15"))
C = (F0[1], pd.Timestamp("1970-12-28 07:15"))
G = (C[1], pd.Timestamp("1970-12-28 10:15"))
T = (pd.Timestamp("1970-12-29 00:40"), pd.Timestamp("1970-12-29 06:20"))
EXPERTS = ("absolute", "relative", "prior30_p90")
ORIGINAL = ("last5",) + EXPERTS
METHODS = ORIGINAL + ("static", "candidate")
EXPECTED_TRAIN = {"F0": (1821, 35, 16), "C": (1089, 35, 12), "G": (1094, 35, 17)}
TEST_GROUPS = {
    77: ("1970-12-28 23:37:00.080710", "1970-12-29 01:59:10.560916"),
    78: ("1970-12-29 01:59:10.572891", "1970-12-29 04:16:49.931794"),
    79: ("1970-12-29 04:16:49.941128", "1970-12-29 06:27:59.252252"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temp.replace(path)


def registry() -> list[str]:
    # The v1 manifest froze this train-selected list before demand validation.
    models = json.loads(v1.FROZEN.read_text())["registry"]
    if len(models) != 35 or models != sorted(set(models)):
        raise ValueError("unexpected frozen registry")
    return models


def source_row_group_metadata(parquet: pq.ParquetFile) -> None:
    if parquet.metadata.num_row_groups <= max(TEST_GROUPS):
        raise ValueError("source lacks preregistered row groups")
    column = parquet.schema.names.index("started_at")
    for group, expected in TEST_GROUPS.items():
        metadata = parquet.metadata.row_group(group)
        stats = metadata.column(column).statistics
        observed = (str(stats.min), str(stats.max)) if stats is not None else None
        if metadata.num_rows != 1001472 or observed != expected:
            raise ValueError(f"row group {group} metadata changed: {observed}")


def sealed_old_test_absent() -> None:
    for group in range(74, 77):
        for root in (v1.ROOT_OLD, v1.ROOT_NEW, DATA):
            if (root / f"rg{group:03d}.parquet").exists():
                raise ValueError(f"v1-sealed row group found: {root / f'rg{group:03d}.parquet'}")


def freeze() -> None:
    if FROZEN.exists() or GATE.exists() or TEST.exists():
        raise FileExistsError("v2 already frozen or scored")
    if any((DATA / f"rg{group:03d}.parquet").exists() for group in TEST_GROUPS):
        raise ValueError("v2 test group exists before freeze")
    sealed_old_test_absent()
    v1.verify_frozen()
    old = v1.verify_old_extractions()
    v1.verify_source_metadata()
    with fsspec.open(v1.SOURCE_URL, "rb", block_size=1024 * 1024,
                     cache_type="readahead") as source:
        source_row_group_metadata(pq.ParquetFile(source))
    frozen = {
        "status": "FROZEN_BEFORE_V2_TRAIN_ONLY_GATE_SCORES_AND_RG077_TO_RG079_VALUES",
        "source_revision": v1.REVISION,
        "source_size": v1.SOURCE_SIZE,
        "source_sha256": v1.SOURCE_SHA256,
        "test_groups_metadata_only": TEST_GROUPS,
        "test_grid": [v.isoformat() for v in T],
        "registry": registry(),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(SCRIPT),
        "tests_sha256": sha256(TESTS),
        "v1_script_sha256": sha256(v1.SCRIPT),
        "v1_frozen_sha256": sha256(v1.FROZEN),
        "old_extracted_sha256": sha256(v1.OLD_EXTRACTED),
        "old_rg65_sha256": v1.RG65_SHA256,
        "old_rg66_to_rg73_sha256": {str(g): old["files"][str(g)]["sha256"] for g in range(66, 74)},
        "environment": {
            "python": __import__("sys").version.split()[0],
            "pandas": pd.__version__,
            "sklearn": __import__("sklearn").__version__,
            "pyarrow": __import__("pyarrow").__version__,
        },
    }
    save_json(FROZEN, frozen)
    print(json.dumps(frozen, indent=2))


def verify_frozen() -> dict:
    frozen = json.loads(FROZEN.read_text())
    expected = {
        "status": "FROZEN_BEFORE_V2_TRAIN_ONLY_GATE_SCORES_AND_RG077_TO_RG079_VALUES",
        "source_revision": v1.REVISION,
        "source_size": v1.SOURCE_SIZE,
        "source_sha256": v1.SOURCE_SHA256,
        "test_groups_metadata_only": {str(k): list(v) for k, v in TEST_GROUPS.items()},
        "test_grid": [v.isoformat() for v in T],
        "registry": registry(),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(SCRIPT),
        "tests_sha256": sha256(TESTS),
        "v1_script_sha256": sha256(v1.SCRIPT),
        "v1_frozen_sha256": sha256(v1.FROZEN),
        "old_extracted_sha256": sha256(v1.OLD_EXTRACTED),
        "old_rg65_sha256": v1.RG65_SHA256,
    }
    for key, value in expected.items():
        if frozen.get(key) != value:
            raise ValueError(f"frozen v2 mismatch: {key}")
    old = v1.verify_old_extractions()
    for group in range(66, 74):
        if frozen["old_rg66_to_rg73_sha256"][str(group)] != old["files"][str(group)]["sha256"]:
            raise ValueError(f"old extraction changed: {group}")
    sealed_old_test_absent()
    return frozen


def slice_rows(rows: pd.DataFrame, interval: tuple[pd.Timestamp, pd.Timestamp]) -> pd.DataFrame:
    return rows.loc[(rows.t >= interval[0]) & (rows.t < interval[1])].copy()


def support(rows: pd.DataFrame, bins: v1.CountBins,
            interval: tuple[pd.Timestamp, pd.Timestamp]) -> dict:
    times = pd.date_range(interval[0], interval[1] - v1.STEP, freq="5min")
    if len(rows) != 35 * len(times):
        raise ValueError("missing decision-grid rows")
    active = rows.loc[rows.eligible]
    counts = active.groupby("model").size()
    zero = sum(bins.global_stream.get(t, 0) == 0 for t in times)
    return {
        "grid_rows": int(len(rows)),
        "eligible_rows": int(len(active)),
        "eligible_per_model": {m: int(counts.get(m, 0)) for m in registry()},
        "global_zero_windows": int(zero),
        "global_zero_fraction": float(zero / len(times)),
        "stream_starts_on_grid": int(rows.y.sum()) if "y" in rows else None,
    }


def check_train_support(rows: dict[str, pd.DataFrame], bins: v1.CountBins) -> dict:
    result = {}
    for name, interval in (("F0", F0), ("C", C), ("G", G)):
        part = rows[name]
        stats = support(part, bins, interval)
        values = list(stats["eligible_per_model"].values())
        expected = EXPECTED_TRAIN[name]
        actual = (stats["eligible_rows"], len([n for n in values if n > 0]), min(values))
        if actual != expected or stats["global_zero_fraction"] > .05:
            raise ValueError(f"preregistered {name} support failed: {actual}, {stats}")
        result[name] = stats
    return result


def pinball(y: np.ndarray, q: np.ndarray) -> np.ndarray:
    residual = np.asarray(y, dtype=float) - np.asarray(q, dtype=float)
    return np.maximum(.9 * residual, -.1 * residual)


def calibration(predicted: pd.DataFrame, models: list[str]) -> dict[str, dict[str, float]]:
    if sorted(predicted.model.unique()) != models:
        raise ValueError("calibration lacks a registered model")
    data = predicted.copy()
    scale = np.maximum(1., data.m30.to_numpy(dtype=float) / 6.)
    for expert in EXPERTS:
        data[f"d_{expert}"] = pinball(data.y, data[expert]) / scale
    averages = data.groupby("model")[[f"d_{e}" for e in EXPERTS]].mean()
    global_loss = averages.mean(axis=0)
    counts = data.groupby("model").size()
    result = {}
    for model in models:
        n = int(counts.get(model, 0))
        result[model] = {
            expert: float((n * averages.loc[model, f"d_{expert}"] +
                           12 * global_loss[f"d_{expert}"]) / (n + 12))
            for expert in EXPERTS
        }
    return result


def softmax_from_losses(losses: np.ndarray) -> np.ndarray:
    scaled = -8. * np.asarray(losses, dtype=float)
    scaled -= scaled.max()
    weights = np.exp(scaled)
    return weights / weights.sum()


def online_mixture(predicted: pd.DataFrame, calibrated: dict[str, dict[str, float]],
                   start: pd.Timestamp) -> pd.DataFrame:
    data = predicted.sort_values(["model", "t"]).copy()
    if not np.isfinite(data[list(EXPERTS) + ["y", "m30"]].to_numpy(dtype=float)).all():
        raise ValueError("nonfinite mixture input")
    data["candidate"] = np.nan
    data["static"] = np.nan
    data["prior_observations"] = 0
    for model, group in data.groupby("model", sort=False):
        prior = np.asarray([calibrated[model][expert] for expert in EXPERTS], dtype=float)
        static_weight = softmax_from_losses(prior)
        previous: deque[tuple[pd.Timestamp, np.ndarray]] = deque()
        for index, row in group.iterrows():
            t = pd.Timestamp(row.t)
            if t < start:
                raise ValueError("mixture observed rows before reset boundary")
            while previous and previous[0][0] < t - pd.Timedelta(hours=1):
                previous.popleft()
            losses = np.zeros(len(EXPERTS), dtype=float)
            total_weight = 0.
            used = 0
            for s, historic in previous:
                if s + v1.STEP > t:
                    raise ValueError("future outcome entered mixture")
                age = (t - s) / v1.STEP
                weight = .9 ** (age - 1)
                losses += weight * historic
                total_weight += weight
                used += 1
            weight = softmax_from_losses((6 * prior + losses) / (6 + total_weight))
            expert_values = np.asarray([row[e] for e in EXPERTS], dtype=float)
            data.at[index, "candidate"] = float(np.dot(weight, expert_values))
            data.at[index, "static"] = float(np.dot(static_weight, expert_values))
            data.at[index, "prior_observations"] = used
            scale = max(1., float(row.m30) / 6.)
            prior_loss = pinball(float(row.y), expert_values) / scale
            previous.append((t, np.asarray(prior_loss, dtype=float)))
    if data[["candidate", "static"]].isna().any().any():
        raise ValueError("missing mixture predictions")
    return data


def score_metrics(rows: pd.DataFrame, models: list[str]) -> dict:
    data = rows.loc[rows.model.isin(models)].copy()
    if len(data) == 0:
        raise ValueError("no score rows")
    for method in METHODS:
        values = data[method].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"invalid {method} predictions")
        residual = data.y.to_numpy(dtype=float) - values
        data[f"loss_{method}"] = pinball(data.y, values)
        data[f"cover_{method}"] = residual <= 0
        data[f"under_{method}"] = np.maximum(0, residual)
    by_model = data.groupby("model")[[f"loss_{m}" for m in METHODS] +
                                     [f"cover_{m}" for m in METHODS] +
                                     [f"under_{m}" for m in METHODS]].mean()
    if sorted(by_model.index) != sorted(models):
        raise ValueError("score models missing")
    overall = {m: float(by_model[f"loss_{m}"].mean()) for m in METHODS}
    best_original = min(ORIGINAL, key=lambda m: overall[m])
    loo = {}
    for model in models:
        subset = by_model.drop(model)
        baseline = min(float(subset[f"loss_{method}"].mean()) for method in ORIGINAL)
        if baseline <= 0:
            raise ValueError("nonpositive leave-one-out baseline")
        loo[model] = float(1 - subset.loss_candidate.mean() / baseline)
    data["hour"] = data.t.dt.floor("1h")
    blocks = []
    for hour, part in data.groupby("hour"):
        means = part.groupby("model")[["loss_static", "loss_candidate"]].mean()
        if len(part) >= 30 and len(means) >= 10:
            blocks.append({
                "hour": hour.isoformat(), "rows": int(len(part)), "models": int(len(means)),
                "static": float(means.loss_static.mean()),
                "candidate": float(means.loss_candidate.mean()),
            })
    return {
        "rows": int(len(data)), "models": int(len(models)),
        "equal_model_pinball": overall,
        "pooled_pinball": {m: float(data[f"loss_{m}"].mean()) for m in METHODS},
        "equal_model_coverage": {m: float(by_model[f"cover_{m}"].mean()) for m in METHODS},
        "equal_model_underprediction_mass": {
            m: float(by_model[f"under_{m}"].mean()) for m in METHODS
        },
        "best_original": best_original,
        "candidate_model_wins_over_best_original": int(
            (by_model.loss_candidate < by_model[f"loss_{best_original}"]).sum()
        ),
        "leave_one_model_out_min_gain": float(min(loo.values())),
        "leave_one_model_out_gain": loo,
        "model_pinball": {
            str(model): {m: float(part[f"loss_{m}"]) for m in METHODS}
            for model, part in by_model.iterrows()
        },
        "hour_blocks": blocks,
        "history_used": {
            "rows_with_prior_observation": int((data.prior_observations > 0).sum()),
            "max_prior_observations": int(data.prior_observations.max()),
        },
    }


def score_gate(metrics: dict, stage: str) -> tuple[bool, list[str]]:
    if stage not in ("G", "test"):
        raise ValueError("invalid score stage")
    q = metrics["equal_model_pinball"]
    candidate = q["candidate"]
    original_gain = .05 if stage == "G" else .10
    static_gain = .02 if stage == "G" else .05
    win_fraction = .60 if stage == "G" else .70
    min_blocks = 3 if stage == "G" else 4
    block_fraction = 2 / 3 if stage == "G" else .70
    reasons = []
    for method in ORIGINAL:
        if q[method] <= 0 or candidate > (1 - original_gain) * q[method]:
            reasons.append(f"candidate lacks {original_gain:.0%} gain over {method}")
    if q["static"] <= 0 or candidate > (1 - static_gain) * q["static"]:
        reasons.append(f"candidate lacks {static_gain:.0%} gain over static")
    if metrics["candidate_model_wins_over_best_original"] < math.ceil(
            win_fraction * metrics["models"]):
        reasons.append(f"candidate wins fewer than {win_fraction:.0%} models")
    if metrics["equal_model_coverage"]["candidate"] < .85:
        reasons.append("candidate equal-model coverage below 85%")
    if metrics["equal_model_underprediction_mass"]["candidate"] > metrics[
            "equal_model_underprediction_mass"]["absolute"]:
        reasons.append("candidate underprediction exceeds absolute")
    if metrics["leave_one_model_out_min_gain"] < .02:
        reasons.append("candidate leave-one-model-out gain below 2%")
    blocks = metrics["hour_blocks"]
    if len(blocks) < min_blocks or sum(
            b["candidate"] < b["static"] for b in blocks
    ) < math.ceil(block_fraction * len(blocks)):
        reasons.append("candidate lacks supported hourly wins over static")
    return not reasons, reasons


def run_gate() -> None:
    frozen = verify_frozen()
    bins = v1.load_counts(73)
    all_train = v1.build_decisions(bins, registry(), "train")
    partitions = {name: slice_rows(all_train, interval) for name, interval in
                  (("F0", F0), ("C", C), ("G", G))}
    supports = check_train_support(partitions, bins)
    first_abs, first_rel = v1.fit_models(partitions["F0"], registry())
    ROOT.mkdir(parents=True, exist_ok=True)
    joblib.dump(first_abs, F0_ABS)
    joblib.dump(first_rel, F0_REL)
    c_pred = v1.predictions(partitions["C"], registry(), first_abs, first_rel)
    calibrated = calibration(c_pred, registry())
    save_json(CALIBRATION, {"calibrated_loss": calibrated, "source": "F0-fit C predictions only"})
    fit_rows = pd.concat([partitions["F0"], partitions["C"]], ignore_index=True)
    gate_abs, gate_rel = v1.fit_models(fit_rows, registry())
    joblib.dump(gate_abs, G_ABS)
    joblib.dump(gate_rel, G_REL)
    g_pred = v1.predictions(partitions["G"], registry(), gate_abs, gate_rel)
    g_pred = online_mixture(g_pred, calibrated, G[0])
    g_pred.to_parquet(GATE_PRED, index=False)
    measured = score_metrics(g_pred, registry())
    passed, reasons = score_gate(measured, "G")
    result = {
        "frozen_manifest_sha256": sha256(FROZEN),
        "source_revision": v1.REVISION, "source_row_groups": [65, 73],
        "source_raw_rows": bins.raw_rows, "source_first_start": bins.first.isoformat(),
        "source_last_start": bins.last.isoformat(),
        "source_stream_missing_model": bins.stream_missing_model,
        "registry": registry(), "supports": supports,
        "calibration_sha256": sha256(CALIBRATION),
        "model_sha256": {name: sha256(path) for name, path in
                         (("f0_absolute", F0_ABS), ("f0_relative", F0_REL),
                          ("gate_absolute", G_ABS), ("gate_relative", G_REL))},
        "prediction_sha256": sha256(GATE_PRED),
        "metrics": measured, "gate_pass": passed, "gate_reasons": reasons,
    }
    save_json(GATE, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def require_gate_go() -> dict:
    verify_frozen()
    result = json.loads(GATE.read_text())
    if result.get("frozen_manifest_sha256") != sha256(FROZEN):
        raise ValueError("gate detached from freeze")
    if result.get("registry") != registry():
        raise ValueError("gate registry mismatch")
    if result.get("calibration_sha256") != sha256(CALIBRATION):
        raise ValueError("calibration changed")
    for name, path in (("f0_absolute", F0_ABS), ("f0_relative", F0_REL),
                       ("gate_absolute", G_ABS), ("gate_relative", G_REL)):
        if result["model_sha256"][name] != sha256(path):
            raise ValueError(f"gate model changed: {name}")
    if result.get("prediction_sha256") != sha256(GATE_PRED):
        raise ValueError("gate predictions changed")
    verified_metrics = score_metrics(pd.read_parquet(GATE_PRED), registry())
    if verified_metrics != result["metrics"]:
        raise ValueError("gate metrics do not match stored predictions")
    passed, reasons = score_gate(verified_metrics, "G")
    if not passed or reasons or not result.get("gate_pass") or result.get("gate_reasons"):
        raise ValueError("train-only gate failed; keep test groups sealed")
    return result


def prepare_test() -> None:
    result = require_gate_go()
    if TEST_PREP.exists():
        raise FileExistsError("test model already prepared")
    bins = v1.load_counts(73)
    train = v1.build_decisions(bins, registry(), "train")
    absolute, relative = v1.fit_models(train, registry())
    joblib.dump(absolute, TEST_ABS)
    joblib.dump(relative, TEST_REL)
    prepared = {
        "gate_sha256": sha256(GATE),
        "calibration_sha256": result["calibration_sha256"],
        "model_sha256": {"absolute": sha256(TEST_ABS), "relative": sha256(TEST_REL)},
        "fit_interval": [F0[0].isoformat(), G[1].isoformat()],
    }
    save_json(TEST_PREP, prepared)
    print(json.dumps(prepared, indent=2))


def require_test_prepared() -> dict:
    require_gate_go()
    prepared = json.loads(TEST_PREP.read_text())
    if prepared.get("gate_sha256") != sha256(GATE):
        raise ValueError("prepared model detached from gate")
    if prepared.get("calibration_sha256") != sha256(CALIBRATION):
        raise ValueError("prepared model detached from calibration")
    for name, path in (("absolute", TEST_ABS), ("relative", TEST_REL)):
        if prepared["model_sha256"][name] != sha256(path):
            raise ValueError(f"prepared test model changed: {name}")
    return prepared


def verify_test_extractions() -> dict:
    extracted = json.loads(EXTRACTED.read_text())
    if extracted.get("source_revision") != v1.REVISION or extracted.get("source_sha256") != v1.SOURCE_SHA256:
        raise ValueError("test extraction source mismatch")
    if extracted.get("gate_sha256") != sha256(GATE) or extracted.get("prepared_sha256") != sha256(TEST_PREP):
        raise ValueError("test extraction detached from passed gate")
    for group in TEST_GROUPS:
        path = DATA / f"rg{group:03d}.parquet"
        record = extracted["files"][str(group)]
        if sha256(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError(f"test extraction changed: {group}")
    return extracted


def fetch_test() -> None:
    require_test_prepared()  # This must precede all network access.
    sealed_old_test_absent()
    v1.verify_source_metadata()
    DATA.mkdir(parents=True, exist_ok=True)
    extracted = json.loads(EXTRACTED.read_text()) if EXTRACTED.exists() else {
        "source_revision": v1.REVISION, "source_sha256": v1.SOURCE_SHA256,
        "gate_sha256": sha256(GATE), "prepared_sha256": sha256(TEST_PREP), "files": {},
    }
    if extracted["gate_sha256"] != sha256(GATE) or extracted["prepared_sha256"] != sha256(TEST_PREP):
        raise ValueError("existing partial extraction detached from gate")
    with fsspec.open(v1.SOURCE_URL, "rb", block_size=1024 * 1024,
                     cache_type="readahead") as source:
        parquet = pq.ParquetFile(source)
        source_row_group_metadata(parquet)
        for group in TEST_GROUPS:
            path = DATA / f"rg{group:03d}.parquet"
            if path.exists():
                record = extracted["files"].get(str(group))
                if record is None or sha256(path) != record["sha256"]:
                    raise ValueError(f"untracked test extraction: {path}")
                continue
            if shutil.disk_usage(DATA).free < 1024 ** 3:
                raise OSError("less than 1 GiB free before test extraction")
            table = parquet.read_row_group(group, columns=v1.COLUMNS)
            pq.write_table(table, path, compression="zstd")
            extracted["files"][str(group)] = {"sha256": sha256(path), "bytes": path.stat().st_size}
            save_json(EXTRACTED, extracted)
            print(f"extracted rg{group:03d}: {path.stat().st_size} bytes", flush=True)
    verify_test_extractions()


def load_test_counts() -> v1.CountBins:
    verify_test_extractions()
    bins = v1.CountBins()
    for group in TEST_GROUPS:
        path = DATA / f"rg{group:03d}.parquet"
        frame = pd.read_parquet(path, columns=v1.COLUMNS)
        if len(frame) != 1001472:
            raise ValueError(f"test row group {group} row count changed")
        bins.add_frame(frame)
        del frame
        gc.collect()
    bins.finish()
    if bins.raw_rows != 3 * 1001472 or bins.first > T[0] - pd.Timedelta(hours=1) or bins.last < T[1]:
        raise ValueError("test lacks complete decision history or labels")
    return bins


def test_rows(bins: v1.CountBins, include_target: bool) -> pd.DataFrame:
    times = pd.date_range(T[0], T[1] - v1.STEP, freq="5min")
    return pd.DataFrame([
        v1.decision_row(bins, model, t, include_target=include_target)
        for model in registry() for t in times
    ])


def test_support_gate(stats: dict) -> tuple[bool, list[str], list[str]]:
    models = [m for m in registry() if stats["eligible_per_model"][m] >= 12]
    reasons = []
    if stats["eligible_rows"] < 800 or len(models) < 25:
        reasons.append("test requires >=800 eligible rows and >=25 models with >=12")
    if stats["global_zero_fraction"] > .05:
        reasons.append("global stream zero in >5% of test windows")
    return not reasons, models, reasons


def run_test() -> None:
    require_test_prepared()
    bins = load_test_counts()
    no_target = test_rows(bins, include_target=False)
    stats = support(no_target, bins, T)
    support_pass, models, reasons = test_support_gate(stats)
    result = {
        "frozen_manifest_sha256": sha256(FROZEN),
        "gate_sha256": sha256(GATE), "prepared_sha256": sha256(TEST_PREP),
        "extracted_sha256": sha256(EXTRACTED),
        "source_revision": v1.REVISION, "source_row_groups": list(TEST_GROUPS),
        "source_raw_rows": bins.raw_rows, "source_first_start": bins.first.isoformat(),
        "source_last_start": bins.last.isoformat(),
        "source_stream_missing_model": bins.stream_missing_model,
        "support": stats, "evaluation_models": models,
        "excluded_models": [m for m in registry() if m not in models],
        "support_pass": support_pass, "support_reasons": reasons, "adopt": False,
    }
    if support_pass:
        rows = test_rows(bins, include_target=True)
        result["support_with_target"] = support(rows, bins, T)
        absolute, relative = joblib.load(TEST_ABS), joblib.load(TEST_REL)
        predicted = v1.predictions(rows, registry(), absolute, relative)
        calibrated = json.loads(CALIBRATION.read_text())["calibrated_loss"]
        predicted = online_mixture(predicted, calibrated, T[0])
        predicted.to_parquet(TEST_PRED, index=False)
        result["prediction_sha256"] = sha256(TEST_PRED)
        result["metrics"] = score_metrics(predicted, models)
        result["adopt"], result["adoption_reasons"] = score_gate(result["metrics"], "test")
    save_json(TEST, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "gate", "prepare-test", "fetch-test", "test"))
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.command == "gate":
        run_gate()
    elif args.command == "prepare-test":
        prepare_test()
    elif args.command == "fetch-test":
        fetch_test()
    else:
        run_test()


if __name__ == "__main__":
    main()
