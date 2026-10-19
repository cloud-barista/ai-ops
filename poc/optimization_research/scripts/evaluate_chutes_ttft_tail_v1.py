#!/usr/bin/env python3
"""Frozen, bounded Chutes TTFT-p90 study; see protocol before using scores."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
from sklearn.ensemble import HistGradientBoostingRegressor


REVISION = "85722e694bf7bb8a5a5ccdf70424fa5998372467"
SOURCE_SIZE = 2046207562
SOURCE_SHA256 = "ca99b91b33d04bf56f3d0b26241c4a083a7e1579f0f571887eee4ee1004c90dd"
SOURCE_URL = (
    f"https://huggingface.co/datasets/harvardMadsys/chutes_metadata/resolve/{REVISION}"
    "/data/trace/train-00044-of-00045.parquet"
)
EXPOSED_RG65_SHA256 = "92fe8c48ab1aadfe33d214570a167cebb4bf01e0fdb7eda296304cc91c0eac71"
DATA_ROOT = Path("data/external/chutes_ttft_tail_v1")
EXPOSED_RG65 = Path("data/external/chutes_metadata/sample_shard44_rg065.parquet")
ARTIFACT_ROOT = Path("artifacts/chutes_ttft_tail_v1")
PROTOCOL_PATH = Path("reports/chutes_ttft_tail_v1_protocol.md")
TEST_PATH_SOURCE = Path("tests/test_evaluate_chutes_ttft_tail_v1.py")
MANIFEST_PATH = ARTIFACT_ROOT / "frozen_manifest.json"
EXTRACTED_PATH = ARTIFACT_ROOT / "extracted_sha256.json"
VALIDATION_PATH = ARTIFACT_ROOT / "validation.json"
TEST_PATH = ARTIFACT_ROOT / "test.json"
MODEL_BASE_PATH = ARTIFACT_ROOT / "base_model.joblib"
MODEL_CANDIDATE_PATH = ARTIFACT_ROOT / "instance_cache_model.joblib"

COLUMNS = [
    "invocation_id", "function_name", "chute_id", "instance_id", "started_at",
    "completed_at", "it", "ot", "ct", "ttft",
]
STREAM_FUNCTIONS = {"chat_stream", "completion_stream"}
DELTA5 = pd.Timedelta(minutes=5)
DELTA30 = pd.Timedelta(minutes=30)
DELTA65 = pd.Timedelta(minutes=65)
SPLITS = {
    "train": (pd.Timestamp("1970-12-27 23:15:00"), pd.Timestamp("1970-12-28 10:15:00")),
    "val": (pd.Timestamp("1970-12-28 11:25:00"), pd.Timestamp("1970-12-28 16:00:00")),
    "test": (pd.Timestamp("1970-12-28 17:05:00"), pd.Timestamp("1970-12-28 23:30:00")),
}
BASE_FEATURES = [
    "arrivals5", "arrivals30", "completed5", "completed30",
    "p90_ttft5", "p90_ttft30", "mean_it30", "mean_ot30",
]
EXTRA_FEATURES = [
    "observed_instances5", "observed_instances30", "max_instance_share5",
    "max_instance_share30", "cache_token_fraction30", "cache_positive_fraction30",
]
LOG_FEATURES = {
    "arrivals5", "arrivals30", "completed5", "completed30", "p90_ttft5",
    "p90_ttft30", "mean_it30", "mean_ot30", "observed_instances5",
    "observed_instances30",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def verify_frozen() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected = {
        "revision": REVISION,
        "source_sha256": SOURCE_SHA256,
        "source_size": SOURCE_SIZE,
        "script_sha256": sha256(Path(__file__)),
        "protocol_sha256": sha256(PROTOCOL_PATH),
        "test_sha256": sha256(TEST_PATH_SOURCE),
        "row_groups": list(range(65, 77)),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"frozen manifest mismatch for {key}")
    return manifest


def verify_source_metadata() -> None:
    response = requests.head(SOURCE_URL, allow_redirects=False, timeout=30)
    response.raise_for_status()
    actual = (
        response.headers.get("X-Repo-Commit"),
        int(response.headers.get("X-Linked-Size", "-1")),
        response.headers.get("X-Linked-ETag", "").strip('"'),
    )
    if actual != (REVISION, SOURCE_SIZE, SOURCE_SHA256):
        raise ValueError(f"source revision/size/SHA mismatch: {actual}")


def verify_extracted(through: int) -> dict:
    if sha256(EXPOSED_RG65) != EXPOSED_RG65_SHA256:
        raise ValueError("exposed row group 65 changed")
    manifest = json.loads(EXTRACTED_PATH.read_text()) if EXTRACTED_PATH.exists() else {}
    if manifest.get("source_sha256", SOURCE_SHA256) != SOURCE_SHA256:
        raise ValueError("extraction source SHA mismatch")
    records = manifest.get("files", {})
    for group in range(66, through + 1):
        key = str(group)
        path = DATA_ROOT / f"rg{group:03d}.parquet"
        if not path.exists() or key not in records:
            raise FileNotFoundError(f"missing pinned extraction: {path}")
        if sha256(path) != records[key]["sha256"] or path.stat().st_size != records[key]["bytes"]:
            raise ValueError(f"extraction checksum mismatch: {path}")
    return manifest


def fetch(through: int) -> None:
    verify_frozen()
    if through not in (73, 76):
        raise ValueError("only the predeclared row-group stages 73 or 76 are allowed")
    if through == 76:
        validation = json.loads(VALIDATION_PATH.read_text())
        if not validation.get("validation_pass"):
            raise ValueError("validation gate failed; do not open test row groups")
    verify_source_metadata()
    if sha256(EXPOSED_RG65) != EXPOSED_RG65_SHA256:
        raise ValueError("exposed row group 65 changed")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    extracted = json.loads(EXTRACTED_PATH.read_text()) if EXTRACTED_PATH.exists() else {
        "source_revision": REVISION, "source_sha256": SOURCE_SHA256, "files": {}
    }
    if extracted["source_sha256"] != SOURCE_SHA256:
        raise ValueError("wrong source in extraction manifest")
    with fsspec.open(SOURCE_URL, "rb", block_size=1024 * 1024, cache_type="readahead") as stream:
        parquet = pq.ParquetFile(stream)
        for group in range(66, through + 1):
            key = str(group)
            path = DATA_ROOT / f"rg{group:03d}.parquet"
            if path.exists():
                if key not in extracted["files"] or sha256(path) != extracted["files"][key]["sha256"]:
                    raise ValueError(f"untracked or changed extraction: {path}")
                continue
            if shutil.disk_usage(DATA_ROOT).free < 1024**3:
                raise OSError("less than 1 GiB free before bounded extraction")
            if parquet.metadata.row_group(group).num_rows != 1001472:
                raise ValueError(f"source row-group {group} size changed")
            table = parquet.read_row_group(group, columns=COLUMNS)
            pq.write_table(table, path, compression="zstd")
            extracted["files"][key] = {"sha256": sha256(path), "bytes": path.stat().st_size}
            save_json(EXTRACTED_PATH, extracted)
            print(f"fetched rg{group:03d}: {path.stat().st_size} bytes; free {shutil.disk_usage(DATA_ROOT).free}", flush=True)
    verify_extracted(through)


class Bins:
    def __init__(self) -> None:
        self.first_started: pd.Timestamp | None = None
        self.last_started: pd.Timestamp | None = None
        self.request_id_parts: list[np.ndarray] = []
        self.raw_rows = 0
        self.arrivals: dict[tuple[str, pd.Timestamp], int] = defaultdict(int)
        self.missing_instance_starts: dict[tuple[str, pd.Timestamp], int] = defaultdict(int)
        self.served_instances: dict[tuple[str, pd.Timestamp], Counter] = defaultdict(Counter)
        self.feedback_parts: dict[tuple[str, pd.Timestamp], list[np.ndarray]] = defaultdict(list)
        self.labels_parts: dict[tuple[str, pd.Timestamp], list[np.ndarray]] = defaultdict(list)
        # token count, input total, output total, cache-valid count,
        # cache input total, cache token total, positive-cache count
        self.tokens: dict[tuple[str, pd.Timestamp], np.ndarray] = defaultdict(
            lambda: np.zeros(7, dtype=np.float64)
        )
        self.feedback: dict[tuple[str, pd.Timestamp], np.ndarray] = {}
        self.labels: dict[tuple[str, pd.Timestamp], np.ndarray] = {}

    def add_frame(self, frame: pd.DataFrame) -> None:
        frame = frame[COLUMNS].copy()
        if frame.started_at.isna().any() or not frame.started_at.is_monotonic_increasing:
            raise ValueError("source starts are missing or out of order")
        if frame.invocation_id.isna().any():
            raise ValueError("source invocation_id missing")
        first = frame.started_at.iloc[0]
        last = frame.started_at.iloc[-1]
        if self.last_started is not None and first < self.last_started:
            raise ValueError("source row groups are not globally ordered by started_at")
        if self.first_started is None:
            self.first_started = first
        self.last_started = last
        self.raw_rows += len(frame)
        self.request_id_parts.append(frame.invocation_id.to_numpy(dtype=np.int64, copy=True))
        started_ok = frame.started_at.notna() & frame.chute_id.notna()
        stream = frame.function_name.isin(STREAM_FUNCTIONS)
        started = frame.loc[started_ok & stream,
                            ["chute_id", "instance_id", "started_at"]].copy()
        started["bin"] = started.started_at.dt.floor("5min")
        counts = started.groupby(["chute_id", "bin"], sort=False).size()
        for (model, window), count in counts.items():
            self.arrivals[(str(model), window)] += int(count)
        missing = started.loc[started.instance_id.isna()].groupby(["chute_id", "bin"], sort=False).size()
        for (model, window), count in missing.items():
            self.missing_instance_starts[(str(model), window)] += int(count)

        # The source does not say when routing assigned instance_id. Treat it as
        # observable only after a request completed, never merely after start.
        served = frame.loc[
            started_ok & stream & frame.completed_at.notna() & frame.instance_id.notna()
            & (frame.completed_at >= frame.started_at),
            ["chute_id", "instance_id", "completed_at"],
        ].copy()
        served["bin"] = served.completed_at.dt.floor("5min")
        completed_counts = served.groupby(["chute_id", "bin", "instance_id"], sort=False).size()
        for (model, window, instance), count in completed_counts.items():
            self.served_instances[(str(model), window)][str(instance)] += int(count)

        duration = (frame.completed_at - frame.started_at).dt.total_seconds()
        valid = started_ok & stream & frame.completed_at.notna() & np.isfinite(frame.ttft) & (
            (frame.ttft > 0) & (duration > 0) & (frame.ttft <= duration + 0.001)
        )
        observed = frame.loc[valid].copy()
        observed["started_bin"] = observed.started_at.dt.floor("5min")
        observed["completed_bin"] = observed.completed_at.dt.floor("5min")

        labelled = observed.loc[
            observed.completed_at <= observed.started_bin + DELTA65,
            ["chute_id", "started_bin", "ttft"],
        ]
        for (model, window), part in labelled.groupby(["chute_id", "started_bin"], sort=False):
            self.labels_parts[(str(model), window)].append(part.ttft.to_numpy(dtype=np.float32))

        for (model, window), part in observed.groupby(["chute_id", "completed_bin"], sort=False):
            key = (str(model), window)
            self.feedback_parts[key].append(part.ttft.to_numpy(dtype=np.float32))
            it = part.it.to_numpy(dtype=float)
            ot = part.ot.to_numpy(dtype=float)
            ct = part.ct.to_numpy(dtype=float)
            token_ok = np.isfinite(it) & np.isfinite(ot) & (it >= 0) & (ot >= 0)
            cache_ok = token_ok & np.isfinite(ct) & (ct >= 0) & (ct <= it)
            summary = self.tokens[key]
            summary[0] += int(token_ok.sum())
            summary[1] += float(it[token_ok].sum())
            summary[2] += float(ot[token_ok].sum())
            summary[3] += int(cache_ok.sum())
            summary[4] += float(it[cache_ok].sum())
            summary[5] += float(ct[cache_ok].sum())
            summary[6] += int((ct[cache_ok] > 0).sum())

    def finish(self) -> None:
        ids = np.concatenate(self.request_id_parts)
        ids.sort()
        if np.any(ids[1:] == ids[:-1]):
            raise ValueError("duplicate invocation_id across row groups")
        self.request_id_parts.clear()
        del ids
        self.feedback = {key: np.concatenate(parts) for key, parts in self.feedback_parts.items()}
        self.labels = {key: np.concatenate(parts) for key, parts in self.labels_parts.items()}
        self.feedback_parts.clear()
        self.labels_parts.clear()


def load_bins(through: int) -> Bins:
    verify_frozen()
    verify_extracted(through)
    bins = Bins()
    for group in range(65, through + 1):
        path = EXPOSED_RG65 if group == 65 else DATA_ROOT / f"rg{group:03d}.parquet"
        frame = pd.read_parquet(path, columns=COLUMNS)
        bins.add_frame(frame)
        del frame
        gc.collect()
        print(f"aggregated rg{group:03d}", flush=True)
    bins.finish()
    expected_rows = (through - 64) * 1001472
    if bins.raw_rows != expected_rows:
        raise ValueError(f"incomplete row-group span: {bins.raw_rows} != {expected_rows}")
    for split in (["train", "val"] if through == 73 else ["train", "val", "test"]):
        start, end = SPLITS[split]
        if bins.first_started > start - pd.Timedelta(hours=1) or bins.last_started < end:
            raise ValueError(f"incomplete one-hour warmup or final label window: {split}")
    return bins


def grid_times(split: str) -> pd.DatetimeIndex:
    start, end = SPLITS[split]
    return pd.date_range(start, end - DELTA5, freq="5min")


def registered_models(bins: Bins) -> list[str]:
    # Cheap train-only prescreen, then enforce *past-only* eligibility before
    # registering a model. Future count alone cannot make an online decision.
    windows: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    start, end = SPLITS["train"]
    for (model, window), ttft in bins.labels.items():
        if start <= window < end and len(ttft) >= 100:
            windows[model] += 1
            totals[model] += len(ttft)
    preliminary = sorted(model for model, count in windows.items() if count >= 12 and totals[model] >= 1000)
    if not preliminary:
        return []
    candidates = build_decisions(bins, preliminary, "train")
    scored = candidates.loc[candidates.eligible_past & candidates.future_valid_ttft.ge(100)]
    by_model = scored.groupby("model").agg(
        windows=("future_valid_ttft", "size"),
        requests=("future_valid_ttft", "sum"),
    )
    return sorted(
        str(model) for model, row in by_model.iterrows()
        if row.windows >= 12 and row.requests >= 1000
    )


def _parts30(bins: Bins, model: str, t: pd.Timestamp) -> tuple[list[np.ndarray], Counter, np.ndarray, int]:
    feedback = []
    instances = Counter()
    token_stats = np.zeros(7, dtype=float)
    arrivals = 0
    for step in range(1, 7):
        key = (model, t - step * DELTA5)
        part = bins.feedback.get(key)
        if part is not None:
            feedback.append(part)
        instances.update(bins.served_instances.get(key, Counter()))
        arrivals += bins.arrivals.get(key, 0)
        token_stats += bins.tokens.get(key, 0)
    return feedback, instances, token_stats, arrivals


def build_decisions(bins: Bins, models: list[str], split: str) -> pd.DataFrame:
    rows = []
    for model in models:
        for t in grid_times(split):
            recent_key = (model, t - DELTA5)
            recent_feedback = bins.feedback.get(recent_key, np.empty(0, dtype=np.float32))
            feedback_parts, instances30, token_stats, arrivals30 = _parts30(bins, model, t)
            completed30 = sum(len(part) for part in feedback_parts)
            future = bins.labels.get((model, t), np.empty(0, dtype=np.float32))
            row = {
                "model": model,
                "t": t,
                "future_stream_starts": int(bins.arrivals.get((model, t), 0)),
                "future_missing_instance_starts": int(bins.missing_instance_starts.get((model, t), 0)),
                "future_valid_ttft": int(len(future)),
                "eligible_past": bool(completed30 >= 100),
            }
            if completed30 >= 100:
                all_feedback = np.concatenate(feedback_parts)
                p90_30 = float(np.quantile(all_feedback, 0.9))
                p90_5 = float(np.quantile(recent_feedback, 0.9)) if len(recent_feedback) >= 30 else p90_30
                instances5 = bins.served_instances.get(recent_key, Counter())
                arrivals5 = bins.arrivals.get(recent_key, 0)
                served5 = sum(instances5.values())
                served30 = sum(instances30.values())
                cache_count = int(token_stats[3])
                row.update({
                    "completed5": int(len(recent_feedback)),
                    "completed30": int(completed30),
                    "p90_ttft5": p90_5,
                    "p90_ttft30": p90_30,
                    "arrivals5": arrivals5,
                    "arrivals30": arrivals30,
                    "observed_instances5": len(instances5),
                    "observed_instances30": len(instances30),
                    "max_instance_share5": max(instances5.values()) / served5 if served5 else 0.0,
                    "max_instance_share30": max(instances30.values()) / served30 if served30 else 0.0,
                    "mean_it30": token_stats[1] / token_stats[0] if token_stats[0] else 0.0,
                    "mean_ot30": token_stats[2] / token_stats[0] if token_stats[0] else 0.0,
                    "cache_coverage30": cache_count / completed30,
                    "cache_token_fraction30": token_stats[5] / token_stats[4] if token_stats[4] else 0.0,
                    "cache_positive_fraction30": token_stats[6] / cache_count if cache_count else 0.0,
                    "last5": p90_5,
                    "prior30": p90_30,
                    "y": float(np.quantile(future, 0.9)) if len(future) >= 100 else np.nan,
                })
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=[
            "model", "t", "future_stream_starts", "future_missing_instance_starts",
            "future_valid_ttft", "eligible_past", "cache_coverage30",
        ])
    return pd.DataFrame(rows)


def support(rows: pd.DataFrame, split: str) -> dict:
    eligible = rows.loc[rows.eligible_past.fillna(False).astype(bool)]
    scored = eligible.loc[eligible.future_valid_ttft >= 100]
    counts = scored.groupby("model").size().sort_values(ascending=False)
    cache_coverage = float((eligible.cache_coverage30 >= 0.9).mean()) if len(eligible) else 0.0
    per_model = rows.groupby("model")[[
        "future_stream_starts", "future_valid_ttft", "future_missing_instance_starts"
    ]].sum()
    streams = int(rows.future_stream_starts.sum())
    valid = int(rows.future_valid_ttft.sum())
    missing_instance = int(rows.future_missing_instance_starts.sum())
    if valid > streams or missing_instance > streams:
        raise ValueError("invalid request-level coverage accounting")
    return {
        "split": split,
        "registered_models": int(rows.model.nunique()),
        "grid_windows_per_model": int(len(grid_times(split))),
        "model_grid_rows": int(len(rows)),
        "past_eligible_rows": int(len(eligible)),
        "past_eligible_fraction": float(len(eligible) / len(rows)) if len(rows) else 0.0,
        "conditional_scored_rows": int(len(scored)),
        "conditional_scored_fraction_of_grid": float(len(scored) / len(rows)) if len(rows) else 0.0,
        "conditional_scored_fraction_of_past_eligible": float(len(scored) / len(eligible)) if len(eligible) else 0.0,
        "future_under_100_among_past_eligible": int((eligible.future_valid_ttft < 100).sum()),
        "stream_starts_on_model_grid": streams,
        "valid_ttft_on_model_grid": valid,
        "valid_ttft_fraction_on_model_grid": valid / streams if streams else None,
        "missing_instance_starts_on_model_grid": missing_instance,
        "missing_instance_fraction_on_model_grid": missing_instance / streams if streams else None,
        "per_model_observed_label_coverage": {
            str(model): {
                "stream_starts": int(part.future_stream_starts),
                "valid_ttft": int(part.future_valid_ttft),
                "valid_ttft_fraction": float(part.future_valid_ttft / part.future_stream_starts)
                if part.future_stream_starts else None,
                "missing_instance_starts": int(part.future_missing_instance_starts),
            }
            for model, part in per_model.iterrows()
        },
        "models_with_12plus_scored_windows": int((counts >= 12).sum()),
        "models_with_1plus_scored_windows": int(len(counts)),
        "cache_coverage_90pct_eligible_fraction": cache_coverage,
        "scored_windows_per_model": {str(model): int(count) for model, count in counts.items()},
    }


def support_gate(supports: dict[str, dict], registry: list[str], split: str,
                 validation_models: list[str] | None = None) -> tuple[bool, list[str], list[str]]:
    train = supports["train"]
    current = supports[split]
    reasons = []
    if len(registry) < 10 or train["conditional_scored_rows"] < 500:
        reasons.append("train: <10 models or <500 scored windows")
    if current["past_eligible_fraction"] < 0.2:
        reasons.append(f"{split}: past eligibility <20% of model-grid")
    if current["cache_coverage_90pct_eligible_fraction"] < 0.9:
        reasons.append(f"{split}: <90% of eligible windows have >=90% cache coverage")
    counts = current["scored_windows_per_model"]
    if validation_models is None:
        eval_models = [model for model in registry if counts.get(model, 0) >= 12]
        if len(eval_models) < 10:
            reasons.append(f"{split}: <10 registered models with >=12 scored windows")
    else:
        eval_models = list(validation_models)
        missing = [model for model in eval_models if counts.get(model, 0) < 12]
        if missing:
            reasons.append(f"{split}: {len(missing)} validation-frozen models have <12 scored windows")
    if sum(counts.get(model, 0) for model in eval_models) < 300:
        reasons.append(f"{split}: <300 scored windows in fixed primary evaluation models")
    return not reasons, eval_models, reasons


def design(rows: pd.DataFrame, models: list[str], candidate: bool) -> np.ndarray:
    names = BASE_FEATURES + (EXTRA_FEATURES if candidate else [])
    values = rows[names].to_numpy(dtype=float, copy=True)
    for index, name in enumerate(names):
        if name in LOG_FEATURES:
            values[:, index] = np.log1p(values[:, index])
    one_hot = np.zeros((len(rows), len(models)), dtype=float)
    slots = {model: index for index, model in enumerate(models)}
    for row_index, model in enumerate(rows.model):
        one_hot[row_index, slots[model]] = 1.0
    result = np.column_stack([values, one_hot])
    if not np.isfinite(result).all():
        raise ValueError("non-finite pre-decision feature")
    return result


def fit_models(train: pd.DataFrame, registry: list[str]) -> tuple[object, object]:
    scored = train.loc[train.eligible_past & train.future_valid_ttft.ge(100) & train.model.isin(registry)]
    counts = scored.groupby("model").size()
    weights = np.array([len(scored) / (len(registry) * counts[model]) for model in scored.model], dtype=float)
    y = np.log1p(scored.y.to_numpy(dtype=float))
    config = dict(
        loss="squared_error", max_iter=120, learning_rate=0.05,
        max_leaf_nodes=15, min_samples_leaf=30, l2_regularization=1.0,
        early_stopping=False, random_state=42,
    )
    base = HistGradientBoostingRegressor(**config)
    candidate = HistGradientBoostingRegressor(**config)
    base.fit(design(scored, registry, False), y, sample_weight=weights)
    candidate.fit(design(scored, registry, True), y, sample_weight=weights)
    return base, candidate


def predictions(rows: pd.DataFrame, registry: list[str], base: object, candidate: object) -> pd.DataFrame:
    scored = rows.loc[rows.eligible_past & rows.future_valid_ttft.ge(100)].copy()
    scored["base"] = np.maximum(0.001, np.expm1(base.predict(design(scored, registry, False))))
    scored["instance_cache"] = np.maximum(0.001, np.expm1(candidate.predict(design(scored, registry, True))))
    return scored


def metrics(rows: pd.DataFrame, eval_models: list[str]) -> dict:
    scored = rows.loc[rows.model.isin(eval_models)].copy()
    methods = ["last5", "prior30", "base", "instance_cache"]
    for method in methods:
        scored[f"loss_{method}"] = np.abs(np.log(scored[method] / scored.y))
    model_scores = scored.groupby("model")[[f"loss_{name}" for name in methods]].mean()
    means = {name: float(model_scores[f"loss_{name}"].mean()) for name in methods}
    pooled = {name: float(scored[f"loss_{name}"].mean()) for name in methods}
    model_wins_base = int((model_scores.loss_instance_cache < model_scores.loss_base).sum())
    block_rows = []
    scored["hour"] = scored.t.dt.floor("1h")
    for hour, part in scored.groupby("hour"):
        block_model = part.groupby("model")[["loss_base", "loss_instance_cache"]].mean()
        if len(block_model) >= 5:
            block_rows.append({
                "hour": hour.isoformat(), "models": int(len(block_model)),
                "base": float(block_model.loss_base.mean()),
                "instance_cache": float(block_model.loss_instance_cache.mean()),
            })
    return {
        "scored_rows_primary": int(len(scored)),
        "models_primary": int(len(eval_models)),
        "equal_model_male": means,
        "pooled_male": pooled,
        "median_observed_ttft_p90_s": float(scored.y.median()),
        "median_predicted_ttft_p90_s": {name: float(scored[name].median()) for name in methods},
        "candidate_model_wins_over_base": model_wins_base,
        "candidate_model_win_fraction_over_base": model_wins_base / len(eval_models),
        "model_male": {
            str(model): {name: float(row[f"loss_{name}"]) for name in methods}
            for model, row in model_scores.iterrows()
        },
        "hour_blocks": block_rows,
    }


def validation_gate(result: dict) -> tuple[bool, list[str]]:
    means = result["equal_model_male"]
    best = min(means[name] for name in ["last5", "prior30", "base"])
    reasons = []
    if not means["instance_cache"] <= 0.95 * best:
        reasons.append("candidate did not beat every baseline by >=5% equal-model MALE")
    if result["candidate_model_win_fraction_over_base"] < 0.6:
        reasons.append("candidate won on <60% of evaluation models")
    return not reasons, reasons


def test_gate(result: dict) -> tuple[bool, list[str]]:
    means = result["equal_model_male"]
    best = min(means[name] for name in ["last5", "prior30", "base"])
    reasons = []
    if not means["instance_cache"] <= 0.90 * best:
        reasons.append("candidate did not beat every baseline by >=10% equal-model MALE")
    if result["candidate_model_win_fraction_over_base"] < 0.7:
        reasons.append("candidate won on <70% of evaluation models")
    blocks = result["hour_blocks"]
    if len(blocks) < 4 or np.mean([b["instance_cache"] < b["base"] for b in blocks]) < 0.7:
        reasons.append("candidate did not win >=70% of >=4 eligible hourly blocks")
    return not reasons, reasons


def validate() -> None:
    bins = load_bins(73)
    registry = registered_models(bins)
    train = build_decisions(bins, registry, "train")
    val = build_decisions(bins, registry, "val")
    supports = {"train": support(train, "train"), "val": support(val, "val")}
    ok, eval_models, reasons = support_gate(supports, registry, "val")
    result = {
        "frozen_manifest_sha256": sha256(MANIFEST_PATH),
        "source_revision": REVISION,
        "source_row_groups": [65, 73],
        "supports": supports,
        "registry": registry,
        "evaluation_models": eval_models,
        "primary_eval_support_rows": int(sum(
            supports["val"]["scored_windows_per_model"].get(model, 0) for model in eval_models
        )),
        "support_pass": ok,
        "support_reasons": reasons,
        "validation_pass": False,
    }
    if ok:
        base, candidate = fit_models(train, registry)
        joblib.dump(base, MODEL_BASE_PATH)
        joblib.dump(candidate, MODEL_CANDIDATE_PATH)
        result["model_sha256"] = {
            "base": sha256(MODEL_BASE_PATH),
            "instance_cache": sha256(MODEL_CANDIDATE_PATH),
        }
        result["metrics"] = metrics(predictions(val, registry, base, candidate), eval_models)
        result["validation_pass"], result["validation_reasons"] = validation_gate(result["metrics"])
    save_json(VALIDATION_PATH, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def test() -> None:
    verify_frozen()
    validation = json.loads(VALIDATION_PATH.read_text())
    if not validation.get("validation_pass"):
        raise ValueError("validation gate failed; do not inspect test")
    if validation["frozen_manifest_sha256"] != sha256(MANIFEST_PATH):
        raise ValueError("frozen manifest changed after validation")
    for name, path in [("base", MODEL_BASE_PATH), ("instance_cache", MODEL_CANDIDATE_PATH)]:
        if validation["model_sha256"][name] != sha256(path):
            raise ValueError(f"model artifact changed: {name}")
    bins = load_bins(76)
    registry = validation["registry"]
    test_rows = build_decisions(bins, registry, "test")
    support_test = support(test_rows, "test")
    supports = {"train": validation["supports"]["train"], "test": support_test}
    ok, eval_models, reasons = support_gate(
        supports, registry, "test", validation["evaluation_models"]
    )
    result = {
        "frozen_manifest_sha256": sha256(MANIFEST_PATH),
        "source_revision": REVISION,
        "source_row_groups": [65, 76],
        "support": support_test,
        "evaluation_models": eval_models,
        "primary_eval_support_rows": int(sum(
            support_test["scored_windows_per_model"].get(model, 0) for model in eval_models
        )),
        "support_pass": ok,
        "support_reasons": reasons,
        "adopt": False,
    }
    if ok:
        base = joblib.load(MODEL_BASE_PATH)
        candidate = joblib.load(MODEL_CANDIDATE_PATH)
        result["metrics"] = metrics(predictions(test_rows, registry, base, candidate), eval_models)
        result["adopt"], result["adoption_reasons"] = test_gate(result["metrics"])
    save_json(TEST_PATH, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("test")
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("--through", type=int, choices=(73, 76), required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.through)
    elif args.command == "validate":
        validate()
    elif args.command == "test":
        test()


if __name__ == "__main__":
    main()
