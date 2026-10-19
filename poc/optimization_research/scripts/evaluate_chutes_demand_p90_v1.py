#!/usr/bin/env python3
"""Frozen Chutes next-five-minute observed streaming-demand p90 study."""

from __future__ import annotations

import argparse
from collections import defaultdict
import gc
import hashlib
import json
from pathlib import Path
import shutil

import fsspec
import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import requests
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


REVISION = "85722e694bf7bb8a5a5ccdf70424fa5998372467"
SOURCE_SIZE = 2046207562
SOURCE_SHA256 = "ca99b91b33d04bf56f3d0b26241c4a083a7e1579f0f571887eee4ee1004c90dd"
SOURCE_URL = (
    f"https://huggingface.co/datasets/harvardMadsys/chutes_metadata/resolve/{REVISION}"
    "/data/trace/train-00044-of-00045.parquet"
)
ROOT_OLD = Path("data/external/chutes_ttft_tail_v1")
RG65 = Path("data/external/chutes_metadata/sample_shard44_rg065.parquet")
RG65_SHA256 = "92fe8c48ab1aadfe33d214570a167cebb4bf01e0fdb7eda296304cc91c0eac71"
ROOT_NEW = Path("data/external/chutes_demand_p90_v1")
OLD_EXTRACTED = Path("artifacts/chutes_ttft_tail_v1/extracted_sha256.json")
OLD_VALIDATION = Path("artifacts/chutes_ttft_tail_v1/validation.json")
ROOT_ART = Path("artifacts/chutes_demand_p90_v1")
PROTOCOL = Path("reports/chutes_demand_p90_v1_protocol.md")
SCRIPT = Path("scripts/evaluate_chutes_demand_p90_v1.py")
TESTS = Path("tests/test_evaluate_chutes_demand_p90_v1.py")
FROZEN = ROOT_ART / "frozen_manifest.json"
EXTRACTED = ROOT_ART / "test_extracted_sha256.json"
VALIDATION = ROOT_ART / "validation.json"
TEST = ROOT_ART / "test.json"
BASE_MODEL = ROOT_ART / "absolute_model.joblib"
RELATIVE_MODEL = ROOT_ART / "relative_model.joblib"
VAL_PRED = ROOT_ART / "validation_predictions.parquet"
TEST_PRED = ROOT_ART / "test_predictions.parquet"

COLUMNS = ["invocation_id", "function_name", "chute_id", "started_at"]
STREAM = {"chat_stream", "completion_stream"}
STEP = pd.Timedelta(minutes=5)
SPLITS = {
    "train": (pd.Timestamp("1970-12-27 23:15"), pd.Timestamp("1970-12-28 10:15")),
    "val": (pd.Timestamp("1970-12-28 11:25"), pd.Timestamp("1970-12-28 16:00")),
    "test": (pd.Timestamp("1970-12-28 17:05"), pd.Timestamp("1970-12-28 23:30")),
}
WINDOWS = (1, 2, 6, 12)  # 5, 10, 30, 60 minutes.
MODEL_FEATURES = ("m5", "m10", "m30", "m60")
GLOBAL_FEATURES = ("g5", "g10", "g30", "g60")


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


def prior_registry() -> list[str]:
    old = json.loads(OLD_VALIDATION.read_text())
    registry = old["registry"]
    if len(registry) != 35 or registry != sorted(set(registry)):
        raise ValueError("prior train-only registry is not the frozen 35-model set")
    return registry


def verify_old_extractions() -> dict:
    if sha256(RG65) != RG65_SHA256:
        raise ValueError("development row group 65 changed")
    manifest = json.loads(OLD_EXTRACTED.read_text())
    if manifest["source_revision"] != REVISION or manifest["source_sha256"] != SOURCE_SHA256:
        raise ValueError("prior extraction source mismatch")
    for group in range(66, 74):
        record = manifest["files"][str(group)]
        path = ROOT_OLD / f"rg{group:03d}.parquet"
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"prior extraction changed: {path}")
    return manifest


def freeze() -> None:
    if FROZEN.exists():
        raise FileExistsError(f"already frozen: {FROZEN}")
    if any((ROOT_NEW / f"rg{group:03d}.parquet").exists() for group in range(74, 77)):
        raise ValueError("test row group exists before freeze")
    if any((ROOT_OLD / f"rg{group:03d}.parquet").exists() for group in range(74, 77)):
        raise ValueError("prior study's sealed test row group was opened")
    old = verify_old_extractions()
    registry = prior_registry()
    record = {
        "status": "FROZEN_BEFORE_NEW_DEMAND_MODEL_SCORES_AND_RG74_TO_RG76_VALUES",
        "source_revision": REVISION,
        "source_size": SOURCE_SIZE,
        "source_sha256": SOURCE_SHA256,
        "row_groups": list(range(65, 77)),
        "old_rg65_sha256": RG65_SHA256,
        "old_extracted_sha256": sha256(OLD_EXTRACTED),
        "old_rg66_to_rg73_sha256": {str(g): old["files"][str(g)]["sha256"] for g in range(66, 74)},
        "old_validation_sha256": sha256(OLD_VALIDATION),
        "registry": registry,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(SCRIPT),
        "tests_sha256": sha256(TESTS),
        "environment": {
            "python": __import__("sys").version.split()[0],
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
            "pyarrow": __import__("pyarrow").__version__,
        },
    }
    save_json(FROZEN, record)
    print(json.dumps(record, indent=2))


def verify_frozen() -> dict:
    frozen = json.loads(FROZEN.read_text())
    expected = {
        "status": "FROZEN_BEFORE_NEW_DEMAND_MODEL_SCORES_AND_RG74_TO_RG76_VALUES",
        "source_revision": REVISION,
        "source_size": SOURCE_SIZE,
        "source_sha256": SOURCE_SHA256,
        "row_groups": list(range(65, 77)),
        "old_rg65_sha256": RG65_SHA256,
        "old_extracted_sha256": sha256(OLD_EXTRACTED),
        "old_validation_sha256": sha256(OLD_VALIDATION),
        "registry": prior_registry(),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(SCRIPT),
        "tests_sha256": sha256(TESTS),
    }
    for key, value in expected.items():
        if frozen.get(key) != value:
            raise ValueError(f"frozen manifest mismatch: {key}")
    old = verify_old_extractions()
    for group in range(66, 74):
        if frozen["old_rg66_to_rg73_sha256"][str(group)] != old["files"][str(group)]["sha256"]:
            raise ValueError(f"frozen old extraction mismatch: {group}")
    return frozen


def verify_source_metadata() -> None:
    response = requests.head(SOURCE_URL, allow_redirects=False, timeout=30)
    response.raise_for_status()
    observed = (
        response.headers.get("X-Repo-Commit"),
        int(response.headers.get("X-Linked-Size", "-1")),
        response.headers.get("X-Linked-ETag", "").strip('"'),
    )
    if observed != (REVISION, SOURCE_SIZE, SOURCE_SHA256):
        raise ValueError(f"source metadata mismatch: {observed}")


def require_validation_go() -> dict:
    frozen = verify_frozen()
    value = json.loads(VALIDATION.read_text())
    if value.get("frozen_manifest_sha256") != sha256(FROZEN):
        raise ValueError("validation is detached from frozen manifest")
    if not value.get("validation_pass"):
        raise ValueError("validation gate failed; keep row groups 74–76 sealed")
    if not value.get("support_pass") or not value.get("metrics"):
        raise ValueError("validation lacks passed support and scores")
    passed, _ = score_gate(value["metrics"], "val")
    if not passed:
        raise ValueError("recorded validation scores do not pass frozen gate")
    for name, path in (("absolute", BASE_MODEL), ("relative", RELATIVE_MODEL)):
        if value["model_sha256"][name] != sha256(path):
            raise ValueError(f"fitted model changed: {name}")
    if value["prediction_sha256"] != sha256(VAL_PRED):
        raise ValueError("validation predictions changed")
    if value["registry"] != frozen["registry"]:
        raise ValueError("validation registry changed")
    return value


def verify_test_extractions() -> dict:
    manifest = json.loads(EXTRACTED.read_text())
    if manifest.get("source_revision") != REVISION or manifest.get("source_sha256") != SOURCE_SHA256:
        raise ValueError("test extraction source mismatch")
    for group in range(74, 77):
        record = manifest["files"][str(group)]
        path = ROOT_NEW / f"rg{group:03d}.parquet"
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"test extraction changed: {path}")
    return manifest


def fetch_test() -> None:
    validation = require_validation_go()
    verify_source_metadata()
    ROOT_NEW.mkdir(parents=True, exist_ok=True)
    extracted = json.loads(EXTRACTED.read_text()) if EXTRACTED.exists() else {
        "source_revision": REVISION,
        "source_sha256": SOURCE_SHA256,
        "validation_sha256": sha256(VALIDATION),
        "files": {},
    }
    if extracted["validation_sha256"] != sha256(VALIDATION):
        raise ValueError("test extraction detached from validation go")
    with fsspec.open(SOURCE_URL, "rb", block_size=1024 * 1024, cache_type="readahead") as stream:
        parquet = pq.ParquetFile(stream)
        for group in range(74, 77):
            key = str(group)
            path = ROOT_NEW / f"rg{group:03d}.parquet"
            if path.exists():
                if key not in extracted["files"] or sha256(path) != extracted["files"][key]["sha256"]:
                    raise ValueError(f"untracked test extraction: {path}")
                continue
            if shutil.disk_usage(ROOT_NEW).free < 1024 ** 3:
                raise OSError("less than 1 GiB free before test extraction")
            if parquet.metadata.row_group(group).num_rows != 1001472:
                raise ValueError("source row-group row count changed")
            table = parquet.read_row_group(group, columns=COLUMNS)
            pq.write_table(table, path, compression="zstd")
            extracted["files"][key] = {"sha256": sha256(path), "bytes": path.stat().st_size}
            save_json(EXTRACTED, extracted)
            print(f"fetched rg{group:03d}: {path.stat().st_size} bytes", flush=True)
    verify_test_extractions()


class CountBins:
    def __init__(self) -> None:
        self.model: dict[tuple[str, pd.Timestamp], int] = defaultdict(int)
        self.global_stream: dict[pd.Timestamp, int] = defaultdict(int)
        self.ids: list[np.ndarray] = []
        self.first: pd.Timestamp | None = None
        self.last: pd.Timestamp | None = None
        self.raw_rows = 0
        self.stream_missing_model = 0

    def add_frame(self, frame: pd.DataFrame) -> None:
        if frame.started_at.isna().any() or not frame.started_at.is_monotonic_increasing:
            raise ValueError("source starts missing or out of order")
        if frame.invocation_id.isna().any():
            raise ValueError("missing invocation_id")
        first, last = frame.started_at.iloc[0], frame.started_at.iloc[-1]
        if self.last is not None and first < self.last:
            raise ValueError("source groups not globally ordered")
        if self.first is None:
            self.first = first
        self.last = last
        self.raw_rows += len(frame)
        self.ids.append(frame.invocation_id.to_numpy(dtype=np.int64, copy=True))

        starts = frame.loc[frame.function_name.isin(STREAM), ["chute_id", "started_at"]].copy()
        self.stream_missing_model += int(starts.chute_id.isna().sum())
        starts["bin"] = starts.started_at.dt.floor("5min")
        for timestamp, n in starts.groupby("bin", sort=False).size().items():
            self.global_stream[timestamp] += int(n)
        for (model, timestamp), n in starts.dropna(subset=["chute_id"]).groupby(
            ["chute_id", "bin"], sort=False
        ).size().items():
            self.model[(str(model), timestamp)] += int(n)

    def finish(self) -> None:
        ids = np.concatenate(self.ids)
        ids.sort()
        if np.any(ids[1:] == ids[:-1]):
            raise ValueError("duplicate invocation_id across row groups")
        self.ids.clear()


def load_counts(through: int) -> CountBins:
    verify_frozen()
    if through == 76:
        require_validation_go()
        verify_test_extractions()
    elif through != 73:
        raise ValueError("only predeclared groups through 73 or 76")
    bins = CountBins()
    for group in range(65, through + 1):
        if group == 65:
            path = RG65
        elif group <= 73:
            path = ROOT_OLD / f"rg{group:03d}.parquet"
        else:
            path = ROOT_NEW / f"rg{group:03d}.parquet"
        frame = pd.read_parquet(path, columns=COLUMNS)
        if len(frame) != 1001472:
            raise ValueError(f"row-group length changed: {group}")
        bins.add_frame(frame)
        print(f"aggregated rg{group:03d}", flush=True)
        del frame
        gc.collect()
    bins.finish()
    if bins.raw_rows != (through - 64) * 1001472:
        raise ValueError("incomplete source span")
    for split in (["train", "val"] if through == 73 else ["train", "val", "test"]):
        start, end = SPLITS[split]
        if bins.first > start - pd.Timedelta(hours=1) or bins.last < end:
            raise ValueError(f"source does not cover complete {split} history/labels")
    return bins


def grid_times(split: str) -> pd.DatetimeIndex:
    start, end = SPLITS[split]
    return pd.date_range(start, end - STEP, freq="5min")


def decision_row(bins: CountBins, model: str, t: pd.Timestamp, include_target: bool = True) -> dict:
    recent_m = [bins.model.get((model, t - k * STEP), 0) for k in range(1, 13)]
    recent_g = [bins.global_stream.get(t - k * STEP, 0) for k in range(1, 13)]
    m = [sum(recent_m[:k]) for k in WINDOWS]
    g = [sum(recent_g[:k]) for k in WINDOWS]
    record = {
        "model": model, "t": t, "eligible": bool(m[2] >= 30),
        "last5": float(m[0]), "prior30_p90": float(max(recent_m[:6])),
        **dict(zip(MODEL_FEATURES, m)), **dict(zip(GLOBAL_FEATURES, g)),
    }
    if include_target:
        record["y"] = int(bins.model.get((model, t), 0))
    return record


def build_decisions(bins: CountBins, registry: list[str], split: str,
                    include_target: bool = True) -> pd.DataFrame:
    return pd.DataFrame([
        decision_row(bins, model, t, include_target)
        for model in registry for t in grid_times(split)
    ])


def support(rows: pd.DataFrame, bins: CountBins, split: str) -> dict:
    eligible = rows.loc[rows.eligible]
    counts = eligible.groupby("model").size()
    empty_global = sum(bins.global_stream.get(t, 0) == 0 for t in grid_times(split))
    result = {
        "split": split,
        "grid_rows": int(len(rows)),
        "grid_windows_per_model": int(len(grid_times(split))),
        "eligible_rows": int(len(eligible)),
        "eligible_fraction": float(len(eligible) / len(rows)),
        "eligible_per_model": {str(m): int(counts.get(m, 0)) for m in rows.model.unique()},
        "global_zero_5min_windows": int(empty_global),
        "global_zero_fraction": float(empty_global / len(grid_times(split))),
    }
    if "y" in rows:
        result.update({
            "stream_starts_on_grid": int(rows.y.sum()),
            "eligible_positive_targets": int((eligible.y > 0).sum()),
            "eligible_zero_targets": int((eligible.y == 0).sum()),
        })
    return result


def support_gate(supports: dict[str, dict], split: str) -> tuple[bool, list[str], list[str]]:
    train = supports["train"]
    current = supports[split]
    reasons = []
    registry = sorted(train["eligible_per_model"])
    if train["eligible_rows"] < 3000 or any(train["eligible_per_model"][m] < 50 for m in registry):
        reasons.append("train requires >=3000 eligible and each frozen model >=50")
    if current["global_zero_fraction"] > 0.05 or train["global_zero_fraction"] > 0.05:
        reasons.append("global stream zero in >5% of five-minute windows")
    if split == "val":
        eval_models = registry
        if current["eligible_rows"] < 1200 or any(current["eligible_per_model"][m] < 12 for m in registry):
            reasons.append("validation requires >=1200 eligible and each frozen model >=12")
    else:
        eval_models = [m for m in registry if current["eligible_per_model"].get(m, 0) >= 12]
        if current["eligible_rows"] < 800 or len(eval_models) < 25:
            reasons.append("test requires >=800 eligible and >=25 frozen models with >=12")
    return not reasons, eval_models, reasons


def scales(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    m5, m30, m60 = (rows[name].to_numpy(dtype=float) for name in ("m5", "m30", "m60"))
    g5, g30, g60 = (rows[name].to_numpy(dtype=float) for name in ("g5", "g30", "g60"))
    return np.maximum.reduce([np.ones(len(rows)), m5, m30 / 6, m60 / 12]), np.maximum.reduce(
        [np.ones(len(rows)), g5, g30 / 6, g60 / 12]
    )


def design(rows: pd.DataFrame, registry: list[str], relative: bool) -> np.ndarray:
    raw_m = rows[list(MODEL_FEATURES)].to_numpy(dtype=float)
    raw_g = rows[list(GLOBAL_FEATURES)].to_numpy(dtype=float)
    if relative:
        sm, sg = scales(rows)
        denominator = np.asarray(WINDOWS, dtype=float)
        numeric = np.column_stack([raw_m / (sm[:, None] * denominator),
                                   raw_g / (sg[:, None] * denominator)])
    else:
        numeric = np.log1p(np.column_stack([raw_m, raw_g]))
    slots = {m: i for i, m in enumerate(registry)}
    onehot = np.zeros((len(rows), len(registry)), dtype=float)
    for i, model in enumerate(rows.model):
        onehot[i, slots[model]] = 1.0
    result = np.column_stack([numeric, onehot])
    if not np.isfinite(result).all():
        raise ValueError("nonfinite pre-decision design")
    return result


def fit_models(train: pd.DataFrame, registry: list[str]) -> tuple[object, object]:
    active = train.loc[train.eligible]
    counts = active.groupby("model").size()
    weights = np.asarray([len(active) / (len(registry) * counts[m]) for m in active.model])
    config = dict(
        loss="quantile", quantile=0.9, max_iter=200, learning_rate=0.05,
        max_leaf_nodes=15, min_samples_leaf=30, l2_regularization=1.0,
        early_stopping=False, random_state=42,
    )
    absolute = HistGradientBoostingRegressor(**config)
    relative = HistGradientBoostingRegressor(**config)
    absolute.fit(design(active, registry, False), active.y.to_numpy(dtype=float), sample_weight=weights)
    sm, _ = scales(active)
    relative.fit(design(active, registry, True), np.log1p(active.y.to_numpy(dtype=float) / sm),
                 sample_weight=weights)
    return absolute, relative


def predictions(rows: pd.DataFrame, registry: list[str], absolute: object,
                relative: object) -> pd.DataFrame:
    active = rows.loc[rows.eligible].copy()
    active["absolute"] = np.maximum(0, absolute.predict(design(active, registry, False)))
    sm, _ = scales(active)
    active["relative"] = np.maximum(0, sm * np.expm1(relative.predict(design(active, registry, True))))
    return active


def metrics(rows: pd.DataFrame, eval_models: list[str]) -> dict:
    data = rows.loc[rows.model.isin(eval_models)].copy()
    methods = ("last5", "prior30_p90", "absolute", "relative")
    for name in methods:
        residual = data.y.to_numpy(dtype=float) - data[name].to_numpy(dtype=float)
        data[f"loss_{name}"] = np.maximum(0.9 * residual, -0.1 * residual)
        data[f"covered_{name}"] = residual <= 0
        data[f"under_{name}"] = np.maximum(0, residual)
    by_model = data.groupby("model")[[f"loss_{m}" for m in methods] +
                                     [f"covered_{m}" for m in methods] +
                                     [f"under_{m}" for m in methods]].mean()
    blocks = []
    data["hour"] = data.t.dt.floor("1h")
    for hour, part in data.groupby("hour"):
        m = part.groupby("model")[["loss_absolute", "loss_relative"]].mean()
        if len(part) >= 30 and len(m) >= 10:
            blocks.append({
                "hour": hour.isoformat(), "rows": int(len(part)), "models": int(len(m)),
                "absolute": float(m.loss_absolute.mean()), "relative": float(m.loss_relative.mean()),
            })
    return {
        "primary_rows": int(len(data)),
        "primary_models": int(len(eval_models)),
        "equal_model_pinball": {m: float(by_model[f"loss_{m}"].mean()) for m in methods},
        "pooled_pinball": {m: float(data[f"loss_{m}"].mean()) for m in methods},
        "equal_model_coverage": {m: float(by_model[f"covered_{m}"].mean()) for m in methods},
        "pooled_coverage": {m: float(data[f"covered_{m}"].mean()) for m in methods},
        "equal_model_underprediction_mass": {
            m: float(by_model[f"under_{m}"].mean()) for m in methods
        },
        "relative_model_wins_over_absolute": int(
            (by_model.loss_relative < by_model.loss_absolute).sum()
        ),
        "relative_model_win_fraction_over_absolute": float(
            (by_model.loss_relative < by_model.loss_absolute).mean()
        ),
        "model_pinball": {
            str(model): {m: float(part[f"loss_{m}"]) for m in methods}
            for model, part in by_model.iterrows()
        },
        "hour_blocks": blocks,
        "observed_mean_requests_5min": float(data.y.mean()),
        "observed_median_requests_5min": float(data.y.median()),
        "predicted_mean_requests_5min": {m: float(data[m].mean()) for m in methods},
    }


def score_gate(value: dict, split: str) -> tuple[bool, list[str]]:
    q = value["equal_model_pinball"]
    candidate = q["relative"]
    best = min(q[name] for name in ("last5", "prior30_p90", "absolute"))
    reasons = []
    threshold = 0.95 if split == "val" else 0.90
    if best <= 0 or candidate > threshold * best:
        reasons.append(f"relative pinball did not beat all baselines by {(1-threshold)*100:.0f}%")
    win_threshold = 0.60 if split == "val" else 0.70
    if value["relative_model_win_fraction_over_absolute"] < win_threshold:
        reasons.append("relative did not win enough models")
    if value["equal_model_coverage"]["relative"] < 0.85:
        reasons.append("relative equal-model coverage <85%")
    if value["equal_model_underprediction_mass"]["relative"] > value[
        "equal_model_underprediction_mass"
    ]["absolute"]:
        reasons.append("relative equal-model underprediction mass exceeds absolute")
    if split == "test":
        blocks = value["hour_blocks"]
        if len(blocks) < 4 or np.mean([b["relative"] < b["absolute"] for b in blocks]) < 0.70:
            reasons.append("relative did not win >=70% of >=4 supported hourly blocks")
    return not reasons, reasons


def validate() -> None:
    verify_frozen()
    bins = load_counts(73)
    registry = prior_registry()
    train = build_decisions(bins, registry, "train")
    val = build_decisions(bins, registry, "val")
    supports = {"train": support(train, bins, "train"), "val": support(val, bins, "val")}
    passed, eval_models, reasons = support_gate(supports, "val")
    expected = {
        "train": (4620, 4004, 2623340),
        "val": (1925, 1514, 1164833),
    }
    for split, triple in expected.items():
        actual = tuple(supports[split][key] for key in
                       ("grid_rows", "eligible_rows", "stream_starts_on_grid"))
        if actual != triple:
            reasons.append(f"{split}: preregistered start-count support changed: {actual} != {triple}")
            passed = False
    result = {
        "frozen_manifest_sha256": sha256(FROZEN),
        "source_revision": REVISION,
        "source_row_groups": [65, 73],
        "source_raw_rows": bins.raw_rows,
        "source_first_start": bins.first.isoformat(),
        "source_last_start": bins.last.isoformat(),
        "source_stream_missing_model": bins.stream_missing_model,
        "registry": registry,
        "evaluation_models": eval_models,
        "supports": supports,
        "support_pass": passed,
        "support_reasons": reasons,
        "validation_pass": False,
    }
    if passed:
        absolute, relative = fit_models(train, registry)
        ROOT_ART.mkdir(parents=True, exist_ok=True)
        joblib.dump(absolute, BASE_MODEL)
        joblib.dump(relative, RELATIVE_MODEL)
        predicted = predictions(val, registry, absolute, relative)
        predicted.to_parquet(VAL_PRED, index=False)
        result["model_sha256"] = {"absolute": sha256(BASE_MODEL), "relative": sha256(RELATIVE_MODEL)}
        result["prediction_sha256"] = sha256(VAL_PRED)
        result["metrics"] = metrics(predicted, eval_models)
        result["validation_pass"], result["validation_reasons"] = score_gate(result["metrics"], "val")
    save_json(VALIDATION, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def test() -> None:
    validation = require_validation_go()
    bins = load_counts(76)
    registry = validation["registry"]
    train_support = validation["supports"]["train"]
    test_support_only = build_decisions(bins, registry, "test", include_target=False)
    test_support = support(test_support_only, bins, "test")
    passed, eval_models, reasons = support_gate({"train": train_support, "test": test_support}, "test")
    result = {
        "frozen_manifest_sha256": sha256(FROZEN),
        "validation_sha256": sha256(VALIDATION),
        "test_extracted_sha256": sha256(EXTRACTED),
        "source_revision": REVISION,
        "source_row_groups": [65, 76],
        "source_raw_rows": bins.raw_rows,
        "source_first_start": bins.first.isoformat(),
        "source_last_start": bins.last.isoformat(),
        "source_stream_missing_model": bins.stream_missing_model,
        "support": test_support,
        "evaluation_models": eval_models,
        "excluded_models": [m for m in registry if m not in eval_models],
        "support_pass": passed,
        "support_reasons": reasons,
        "adopt": False,
    }
    if passed:
        test_rows = build_decisions(bins, registry, "test", include_target=True)
        result["support_with_target"] = support(test_rows, bins, "test")
        absolute = joblib.load(BASE_MODEL)
        relative = joblib.load(RELATIVE_MODEL)
        predicted = predictions(test_rows, registry, absolute, relative)
        predicted.to_parquet(TEST_PRED, index=False)
        result["prediction_sha256"] = sha256(TEST_PRED)
        result["metrics"] = metrics(predicted, eval_models)
        result["adopt"], result["adoption_reasons"] = score_gate(result["metrics"], "test")
    save_json(TEST, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "validate", "fetch-test", "test"))
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.command == "validate":
        validate()
    elif args.command == "fetch-test":
        fetch_test()
    else:
        test()


if __name__ == "__main__":
    main()
