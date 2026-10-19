"""Exploratory e/g training for Borg JOB start-within-5s risk.

The label is not a VM Ready time, AI-App latency, or an action effect. Cells
f/h are deliberately rejected: they require a separate, frozen evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier

import build_google_2019_job_schedule_5s as builder


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/google_2019_start_delay_protocol.md"
DAY_US = 86_400_000_000
HOUR_US = 3_600_000_000
FIVE_SECONDS_US = 5_000_000
STATIC = ("scheduling_class", "priority", "scheduler", "has_alloc_collection",
          "has_parent_collection", "start_after_count")
COLUMNS = ("cell", "job_key", "submit_time_us", *STATIC, "late_schedule_5s")
GROUP = ("scheduler", "scheduling_class", "priority")
FULL_GROUP = ("scheduling_class", "priority", "scheduler", "has_alloc_collection",
              "has_parent_collection", "start_after_count")
SHRINKAGES = (10.0, 50.0, 200.0)
MODEL_KWARGS = dict(max_iter=100, max_leaf_nodes=15, min_samples_leaf=200,
                    learning_rate=0.05, l2_regularization=1.0,
                    early_stopping=False, random_state=42)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jobs(cell_e_dir: Path, cell_g_dir: Path) -> tuple[pd.DataFrame, dict]:
    frames = []
    provenance = {}
    for cell, dataset_dir in (("e", cell_e_dir), ("g", cell_g_dir)):
        data_path = dataset_dir / "jobs.csv"
        metadata_path = dataset_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        frame = pd.read_csv(data_path, dtype={"cell": "str", "job_key": "str"})
        if set(frame.cell) != {cell} or metadata.get("cell") != cell:
            raise ValueError("development_requires_exactly_exposed_e_and_g")
        if metadata.get("builder_sha256") != sha256(Path(builder.__file__)):
            raise ValueError("builder_code_sha256_mismatch")
        source = metadata.get("source", {})
        if source.get("sha256") != builder.PINNED_SHA256[cell]:
            raise ValueError("pinned_source_sha256_mismatch")
        official = builder.verify_official_input(Path(source["input_path"]), cell)
        if any(source.get(name) != value for name, value in official.items()):
            raise ValueError("verified_official_source_mismatch")
        if source.get("bytes") != official["source_bytes"]:
            raise ValueError("official_source_bytes_mismatch")
        if metadata.get("csv_sha256") != sha256(data_path):
            raise ValueError("builder_csv_sha256_mismatch")
        if metadata.get("audit", {}).get("labeled_jobs") != len(frame):
            raise ValueError("builder_labeled_job_count_mismatch")
        for label in (0, 1):
            if metadata.get("audit", {}).get(f"label_{label}_jobs", 0) != int(
                    frame.late_schedule_5s.eq(label).sum()):
                raise ValueError("builder_label_count_mismatch")
        frames.append(frame)
        provenance[cell] = {"jobs_csv_sha256": sha256(data_path),
                            "builder_metadata_sha256": sha256(metadata_path),
                            "builder_metadata": metadata}
    frame = pd.concat(frames, ignore_index=True)
    if tuple(frame.columns) != COLUMNS:
        raise ValueError("unexpected_job_columns")
    if frame.job_key.isna().any() or frame.duplicated(["cell", "job_key"]).any():
        raise ValueError("missing_or_duplicate_job_key")
    for name in ("submit_time_us", *STATIC, "late_schedule_5s"):
        values = pd.to_numeric(frame[name], errors="raise")
        if values.isna().any() or not np.all(np.equal(values, np.floor(values))):
            raise ValueError("noninteger_" + name)
        frame[name] = values.astype("int64")
    if (frame.submit_time_us <= 0).any() or (frame.submit_time_us >= 2**63 - 1).any():
        raise ValueError("invalid_submit_time")
    if not frame.late_schedule_5s.isin([0, 1]).all():
        raise ValueError("invalid_label")
    if (frame.start_after_count < 0).any():
        raise ValueError("negative_dependency_count")
    for name in ("has_alloc_collection", "has_parent_collection"):
        if not frame[name].isin([0, 1]).all():
            raise ValueError("invalid_" + name)
    frame = frame.sort_values(["cell", "submit_time_us", "job_key"],
                              kind="mergesort").reset_index(drop=True)
    return frame, provenance


def _prior_window(times: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """At t, use labels whose five-second outcome was known strictly before t."""
    left = np.searchsorted(times, times - HOUR_US, side="left")
    right = np.searchsorted(times, times - FIVE_SECONDS_US, side="left")
    prefix = np.concatenate(([0], np.cumsum(labels, dtype=np.int64)))
    count = right - left
    positives = prefix[right] - prefix[left]
    if np.any(count < 0) or np.any(positives < 0) or np.any(positives > count):
        raise AssertionError("invalid_causal_history")
    return count, positives


def causal_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute previous-one-hour cell and scheduler/class/priority histories."""
    history = pd.DataFrame(index=frame.index)
    for field in ("cell_count", "cell_late", "group_count", "group_late",
                  "full_count", "full_late"):
        history[field] = np.zeros(len(frame), dtype=np.int64)
    for _cell, idx in frame.groupby("cell", sort=False).indices.items():
        times = frame.submit_time_us.to_numpy(dtype=np.int64)[idx]
        labels = frame.late_schedule_5s.to_numpy(dtype=np.int64)[idx]
        count, positives = _prior_window(times, labels)
        history.loc[idx, "cell_count"] = count
        history.loc[idx, "cell_late"] = positives
    for _group, idx in frame.groupby(["cell", *GROUP], sort=False).indices.items():
        times = frame.submit_time_us.to_numpy(dtype=np.int64)[idx]
        labels = frame.late_schedule_5s.to_numpy(dtype=np.int64)[idx]
        count, positives = _prior_window(times, labels)
        history.loc[idx, "group_count"] = count
        history.loc[idx, "group_late"] = positives
    for _group, idx in frame.groupby(["cell", *FULL_GROUP], sort=False).indices.items():
        times = frame.submit_time_us.to_numpy(dtype=np.int64)[idx]
        labels = frame.late_schedule_5s.to_numpy(dtype=np.int64)[idx]
        count, positives = _prior_window(times, labels)
        history.loc[idx, "full_count"] = count
        history.loc[idx, "full_late"] = positives
    return history


def split_masks(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    times = frame.submit_time_us.to_numpy(dtype=np.int64)
    days = times // DAY_US
    burn_in = times >= HOUR_US
    return {
        "train": burn_in & (days <= 19) & (times + FIVE_SECONDS_US < 20 * DAY_US),
        "validation": (days >= 20) & (days <= 24) &
                      (times + FIVE_SECONDS_US < 25 * DAY_US),
        "development_stress": (days >= 25) & (days <= 30),
    }


def _safe_rate(pos: np.ndarray, count: np.ndarray, fallback: float) -> np.ndarray:
    return np.divide(pos, count, out=np.full(len(count), fallback, dtype=float),
                     where=count > 0)


def probabilities(history: pd.DataFrame, prior: float, shrinkage: float) -> dict[str, np.ndarray]:
    n = history.cell_count.to_numpy(dtype=float)
    k = history.group_count.to_numpy(dtype=float)
    recent = _safe_rate(history.cell_late.to_numpy(dtype=float), n, prior)
    group = (history.group_late.to_numpy(dtype=float) + shrinkage * recent) / (k + shrinkage)
    full_n = history.full_count.to_numpy(dtype=float)
    full = (history.full_late.to_numpy(dtype=float) + shrinkage * recent) / (full_n + shrinkage)
    return {"constant": np.full(len(history), prior, dtype=float),
            "recent_cell": recent, "recent_group": group, "recent_full": full}


def static_lookup(frame: pd.DataFrame, train: np.ndarray,
                  prior_by_cell: dict[str, float],
                  columns: tuple[str, ...], shrinkage: float, *,
                  pooled: bool = False) -> np.ndarray:
    """Training-only exact-context rate, shrunk toward a train prior."""
    keys = [*columns] if pooled else ["cell", *columns]
    table = (frame.loc[train, [*keys, "late_schedule_5s"]]
             .groupby(keys, dropna=False)["late_schedule_5s"]
             .agg(["sum", "count"]).reset_index())
    joined = frame.loc[:, keys].merge(table, on=keys, how="left", sort=False)
    positives = joined["sum"].fillna(0).to_numpy(dtype=float)
    count = joined["count"].fillna(0).to_numpy(dtype=float)
    prior = (np.full(len(frame), float(np.mean(
                 frame.late_schedule_5s.to_numpy(dtype=np.int64)[train])), dtype=float)
             if pooled else frame.cell.map(prior_by_cell).to_numpy(dtype=float))
    return (positives + shrinkage * prior) / (count + shrinkage)


def model_features(frame: pd.DataFrame, history: pd.DataFrame, fallback: float) -> np.ndarray:
    n = history.cell_count.to_numpy(dtype=float)
    k = history.group_count.to_numpy(dtype=float)
    recent = _safe_rate(history.cell_late.to_numpy(dtype=float), n, fallback)
    group = (history.group_late.to_numpy(dtype=float) + 50.0 * recent) / (k + 50.0)
    static = frame.loc[:, STATIC].to_numpy(dtype=float)
    return np.column_stack((static, recent, group, np.log1p(n), np.log1p(k)))


def scored(actual: np.ndarray, prediction: np.ndarray, job_keys: np.ndarray) -> dict:
    if len(actual) == 0 or len(prediction) != len(actual):
        raise ValueError("empty_or_mismatched_scoring_rows")
    p = np.clip(np.asarray(prediction, dtype=float), 1e-12, 1 - 1e-12)
    if not np.all(np.isfinite(p)):
        raise ValueError("nonfinite_probability")
    y = np.asarray(actual, dtype=np.int64)
    budget = math.ceil(0.1 * len(y))
    order = np.lexsort((job_keys, -p))
    alarm = order[:budget]
    positives = int(np.sum(y))
    bins = np.minimum((p * 10).astype(int), 9)
    calibration = []
    for b in range(10):
        selected = bins == b
        count = int(np.sum(selected))
        calibration.append({"lower": b / 10, "upper": (b + 1) / 10,
                            "jobs": count,
                            "observed_late_rate": float(np.mean(y[selected])) if count else None,
                            "mean_predicted_risk": float(np.mean(p[selected])) if count else None})
    ece = sum(item["jobs"] / len(y) * abs(item["observed_late_rate"] -
              item["mean_predicted_risk"]) for item in calibration if item["jobs"])
    return {"jobs": int(len(y)), "late_jobs": positives,
            "on_time_jobs": int(len(y) - positives),
            "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(np.mean(-y * np.log(p) - (1 - y) * np.log1p(-p))),
            "ece_fixed_probability_deciles": ece,
            "calibration_fixed_probability_deciles": calibration,
            "top_10pct_alerts": budget,
            "top_10pct_recall": float(np.sum(y[alarm]) / positives) if positives else None,
            "top_10pct_precision": float(np.mean(y[alarm]))}


def day_block_brier_difference_ci(y: np.ndarray, model_p: np.ndarray,
                                  baseline_p: np.ndarray, days: np.ndarray,
                                  *, repeats: int = 2000, seed: int = 42) -> dict:
    """Resample whole trace days; positive values favor the fitted model."""
    unique, inverse = np.unique(days, return_inverse=True)
    if len(unique) < 2:
        return {"days": int(len(unique)), "bootstrap_repeats": repeats,
                "lower_95": None, "upper_95": None}
    diff = (baseline_p - y) ** 2 - (model_p - y) ** 2
    sums = np.bincount(inverse, weights=diff, minlength=len(unique))
    counts = np.bincount(inverse, minlength=len(unique))
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(unique), size=(repeats, len(unique)))
    replicate = np.sum(sums[draws], axis=1) / np.sum(counts[draws], axis=1)
    lower, upper = np.quantile(replicate, [.025, .975])
    return {"days": int(len(unique)), "bootstrap_repeats": repeats,
            "lower_95": float(lower), "upper_95": float(upper)}


def _cell_scores(frame: pd.DataFrame, mask: np.ndarray, model_p: np.ndarray,
                 baselines: dict[str, np.ndarray], *,
                 fixed_benchmarks: dict[str, str] | None = None,
                 complete_days: dict[str, list[int]] | None = None) -> dict:
    output = {}
    for cell in ("e", "g"):
        chosen = mask & frame.cell.eq(cell).to_numpy()
        y = frame.late_schedule_5s.to_numpy(dtype=np.int64)[chosen]
        keys = frame.job_key.to_numpy()[chosen]
        days = frame.submit_time_us.to_numpy(dtype=np.int64)[chosen] // DAY_US
        scores = {name: scored(y, p[chosen], keys) for name, p in
                  (("model", model_p), *baselines.items())}
        benchmark = (fixed_benchmarks[cell] if fixed_benchmarks is not None else
                     min(baselines, key=lambda name: scores[name]["brier"]))
        main = scores["model"]
        base = scores[benchmark]
        relative = (base["brier"] - main["brier"]) / base["brier"]
        ci = day_block_brier_difference_ci(y, model_p[chosen],
                                           baselines[benchmark][chosen], days)
        recall_delta = (main["top_10pct_recall"] - base["top_10pct_recall"]
                        if main["top_10pct_recall"] is not None and
                           base["top_10pct_recall"] is not None else None)
        sub = (chosen & frame.scheduler.eq(0).to_numpy() &
               frame.start_after_count.eq(0).to_numpy())
        subgroup = {"jobs": int(sub.sum())}
        if sub.any():
            sub_y = frame.late_schedule_5s.to_numpy(dtype=np.int64)[sub]
            subgroup["model_brier"] = float(np.mean((model_p[sub] - sub_y) ** 2))
            subgroup["benchmark_brier"] = float(np.mean((baselines[benchmark][sub] - sub_y) ** 2))
        output[cell] = {
            "days": sorted(map(int, set(days))),
            "complete_observation_days": (complete_days[cell] if complete_days else []),
            "baseline_used": benchmark,
            "relative_brier_improvement": float(relative),
            "day_block_brier_difference_ci95": ci,
            "top_10pct_recall_delta": recall_delta,
            "development_gate": (bool(len(complete_days[cell]) >= 4 and
                                      main["late_jobs"] >= 10_000 and
                                      main["on_time_jobs"] >= 10_000 and
                                      relative >= .05 and recall_delta is not None
                                      and recall_delta >= -.01)
                                 if complete_days is not None else None),
            "default_scheduler_no_dependency": subgroup,
            "scores": scores,
        }
    return output


def evaluate(cell_e_dir: Path, cell_g_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError("output_dir_must_be_new")
    frame, provenance = read_jobs(cell_e_dir, cell_g_dir)
    history = causal_history(frame)
    masks = split_masks(frame)
    if any(not np.any(mask & frame.cell.eq(cell).to_numpy())
           for mask in masks.values() for cell in ("e", "g")):
        raise ValueError("missing_cell_temporal_split")
    train = masks["train"]
    y = frame.late_schedule_5s.to_numpy(dtype=np.int64)
    prior = float(np.mean(y[train]))
    cell_priors = {cell: float(np.mean(y[train & frame.cell.eq(cell).to_numpy()]))
                   for cell in ("e", "g")}
    features = model_features(frame, history, prior)
    model = HistGradientBoostingClassifier(**MODEL_KWARGS)
    model.fit(features[train], y[train])
    model_p = model.predict_proba(features)[:, 1]
    baselines = {}
    for k in SHRINKAGES:
        predictions = {name: np.zeros(len(frame), dtype=float)
                       for name in ("constant", "recent_cell", "recent_group",
                                    "recent_full")}
        for cell, prior_cell in cell_priors.items():
            rows = frame.cell.eq(cell).to_numpy()
            cell_baselines = probabilities(history.loc[rows], prior_cell, k)
            for name, value in cell_baselines.items():
                predictions[name][rows] = value
        baselines.setdefault("constant", predictions["constant"])
        baselines.setdefault("recent_cell", predictions["recent_cell"])
        baselines[f"recent_group_k{int(k)}"] = predictions["recent_group"]
        baselines[f"recent_full_k{int(k)}"] = predictions["recent_full"]
    baselines["static_coarse_k10"] = static_lookup(frame, train, cell_priors, GROUP, 10.0)
    for k in (10.0, 100.0):
        baselines[f"static_full_k{int(k)}"] = static_lookup(
            frame, train, cell_priors, FULL_GROUP, k)
    pooled_static = static_lookup(frame, train, cell_priors, FULL_GROUP, 10.0,
                                  pooled=True)
    baselines["static_full_pooled_k10"] = pooled_static
    full_count = history.full_count.to_numpy(dtype=float)
    full_late = history.full_late.to_numpy(dtype=float)
    baselines["hierarchical_pool6_recent_full_k20"] = (
        full_late + 20.0 * pooled_static) / (full_count + 20.0)
    complete_validation_days = {}
    for cell in ("e", "g"):
        meta = provenance[cell]["builder_metadata"]
        first, last = int(meta["time_min_us"]), int(meta["time_max_us"])
        complete_validation_days[cell] = [day for day in range(20, 25)
                                          if first <= day * DAY_US and
                                          last >= (day + 1) * DAY_US + FIVE_SECONDS_US]
    validation = _cell_scores(frame, masks["validation"], model_p, baselines,
                              complete_days=complete_validation_days)
    fixed_benchmarks = {cell: validation[cell]["baseline_used"] for cell in ("e", "g")}
    stress = _cell_scores(frame, masks["development_stress"], model_p, baselines,
                          fixed_benchmarks=fixed_benchmarks)
    results = {"validation": validation, "development_stress": stress}
    results["training"] = {cell: {"jobs": int((train & frame.cell.eq(cell).to_numpy()).sum()),
                                  "late_rate": cell_priors[cell]}
                           for cell in ("e", "g")}
    results["scope"] = ("Exploratory Google 2019 Borg JOB SUBMIT-to-SCHEDULE >5s "
                        "risk only; no VM Ready, AI-App SLO, or scale-action effect")
    results["feature_names"] = [*STATIC, "recent_cell_rate", "recent_group_k50_rate",
                                "log1p_recent_cell_count", "log1p_recent_group_count"]
    results["model_kwargs"] = MODEL_KWARGS
    results["development_gate_both_cells"] = all(
        results["validation"][cell]["development_gate"] for cell in ("e", "g"))
    results["f_h_opened"] = False
    results["protocol_sha256"] = sha256(PROTOCOL)
    results["evaluator_sha256"] = sha256(Path(__file__))
    results["sklearn_version"] = sklearn.__version__
    results["numpy_version"] = np.__version__
    results["pandas_version"] = pd.__version__
    results["provenance"] = provenance
    output_dir.mkdir(parents=True, exist_ok=False)
    model_path = output_dir / "model.joblib"
    joblib.dump(model, model_path)
    results["model_sha256"] = sha256(model_path)
    (output_dir / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell-e-dir", type=Path, required=True)
    parser.add_argument("--cell-g-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.cell_e_dir, args.cell_g_dir, args.output_dir)
    print(json.dumps({"validation": {cell: result["validation"][cell]["development_gate"]
                                      for cell in ("e", "g")},
                      "development_gate_both_cells": result["development_gate_both_cells"],
                      "output_dir": str(args.output_dir)}, sort_keys=True))


if __name__ == "__main__":
    main()
