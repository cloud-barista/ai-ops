"""Frozen SwissAI scale-normalized demand follow-up on Aug 16--Oct 16.

The first run must be --dry-run. It audits eligibility before --score fits the
fixed models. Created requests on or after Oct 16 are not used.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import math
import time

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

import evaluate_swissai_demand as prior


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/swissai_serving_trace"
DEVELOPMENT = ROOT / "artifacts/swissai_demand_v1/decision_rows.parquet"
OUTPUT = ROOT / "artifacts/swissai_scale_adaptive_v1"
PROTOCOL = ROOT / "reports/swissai_scale_adaptive_protocol.md"
EXPECTED_PROTOCOL_SHA = "ee2064ed62a86a9eae0ff03c57ffb92fdea0fbf70d840a364db0a6a4e6aa3c59"
EXPECTED_DEVELOPMENT_SHA = "d8443b67a500ca443e8d556152e5da299d63ebb1f81f05f4e5661c17af418c89"
START = prior.T_VAL + 31 * prior.DAY_US  # 2025-08-16 UTC, same as prior.T_END
END = prior.timestamp_us("2025-10-16")
N_BINS = (END - prior.T0) // prior.BIN_US
ALIASES = prior.ALIASES
BASELINES = ("persistence", "recent_p90")
SCALE_FEATURES = (["model_code"] + [f"ratio_m{w}" for w in (5, 10, 30, 60, 120)] +
                  [f"ratio_g{w}" for w in (5, 10, 30, 60, 120)] +
                  ["sin_day", "cos_day", "sin_week", "cos_week"])


def guard_sources() -> dict:
    hashes = prior.assert_sources()
    if START != prior.T_END:
        raise ValueError("Holdout does not begin at the first study's sealed boundary")
    if prior.sha256(PROTOCOL) != EXPECTED_PROTOCOL_SHA:
        raise ValueError("Frozen scale-adaptive protocol SHA256 changed")
    if prior.sha256(DEVELOPMENT) != EXPECTED_DEVELOPMENT_SHA:
        raise ValueError("Frozen first-study decision rows changed")
    hashes["followup_protocol_sha256"] = EXPECTED_PROTOCOL_SHA
    hashes["development_sha256"] = EXPECTED_DEVELOPMENT_SHA
    return hashes


def validate_development(table: pa.Table) -> dict:
    needed = set(prior.ARRIVAL_FEATURES) | {"t_us", "model_alias", "split", "y", *BASELINES}
    if not needed.issubset(table.column_names):
        raise ValueError("First-study decision rows lack fixed arrival features")
    if table.num_rows != 18_893:
        raise ValueError("First-study decision-row count changed")
    t = table["t_us"].to_numpy(zero_copy_only=False)
    aliases = table["model_alias"].to_numpy(zero_copy_only=False)
    split = table["split"].to_numpy(zero_copy_only=False)
    if set(aliases.tolist()) != set(ALIASES):
        raise ValueError("Development model aliases changed")
    if np.any(t % prior.BIN_US) or len(set(zip(aliases.tolist(), t.tolist()))) != len(t):
        raise ValueError("Development grid or (model,t) uniqueness changed")
    split_counts = {}
    for code, (start, end) in enumerate(prior.SPLIT_BOUNDS):
        mask = split == code
        if np.any((t[mask] < start) | (t[mask] + 2 * prior.BIN_US > end)):
            raise ValueError("Development target crossed a split boundary")
        split_counts[prior.SPLITS[code]] = int(mask.sum())
    if split_counts != {"fit": 12_426, "validation": 3_039, "test": 3_428}:
        raise ValueError("Development split counts changed")
    if np.any(~np.isin(split, [0, 1, 2])):
        raise ValueError("Unknown development split code")
    for name in ("y", "m5", "m10", "m30", "m60", "m120", "g5", "g10", "g30", "g60", "g120"):
        values = table[name].to_numpy(zero_copy_only=False)
        if np.any(~np.isfinite(values)) or np.any(values < 0):
            raise ValueError(f"Invalid nonnegative development column: {name}")
    return {"rows": table.num_rows, "by_split": split_counts,
            "by_model": {a: int(np.count_nonzero(aliases == a)) for a in ALIASES}}


def ingest_arrivals_until_end(compact_path: Path, selected_names: tuple[str, ...]) -> tuple[np.ndarray, dict[str, np.ndarray], list[int], dict]:
    """Global and selected-model arrivals; stops before the Oct 16 holdout."""
    pf = pq.ParquetFile(compact_path)
    if not {"created_utc_us", "model"}.issubset(pf.schema_arrow.names):
        raise ValueError("Compact trace lacks creation or model column")
    raw_names = {hashlib.sha256(m.encode()).hexdigest()[:10]: m for m in selected_names}
    if set(raw_names) != set(ALIASES):
        raise ValueError("Selected model aliases changed")
    global_bins = np.zeros(N_BINS, dtype=np.int64)
    model_bins = {a: np.zeros(N_BINS, dtype=np.int64) for a in ALIASES}
    gap_ends: list[int] = []
    previous: int | None = None
    read_rows = 0
    for batch in pf.iter_batches(columns=["created_utc_us", "model"], batch_size=500_000):
        created_full = prior._as_int(batch.column("created_utc_us"))
        if not len(created_full):
            continue
        if np.any(np.diff(created_full) < 0) or (previous is not None and created_full[0] < previous):
            raise ValueError("Compact trace is not chronological")
        stop = int(np.searchsorted(created_full, END, side="left"))
        if stop == 0:
            break
        created = created_full[:stop]
        if previous is not None and created[0] - previous >= prior.GAP_US:
            gap_ends.append(int(created[0]))
        gap_ends.extend(int(created[k + 1]) for k in np.flatnonzero(np.diff(created) >= prior.GAP_US))
        previous = int(created[-1])
        read_rows += len(created)
        in_grid = created >= prior.T0
        bins = ((created[in_grid] - prior.T0) // prior.BIN_US).astype(np.int64)
        np.add.at(global_bins, bins, 1)
        model_col = batch.column("model").slice(0, stop)
        for alias in ALIASES:
            own = prior._eq(model_col, raw_names[alias]) & in_grid
            own_bins = ((created[own] - prior.T0) // prior.BIN_US).astype(np.int64)
            np.add.at(model_bins[alias], own_bins, 1)
        if stop < len(created_full):
            break
    return global_bins, model_bins, gap_ends, {
        "rows_read_before_2025_10_16": read_rows,
        "total_grid_arrivals": int(global_bins.sum()),
        "selected_grid_arrivals": {a: int(model_bins[a].sum()) for a in ALIASES},
        "known_6h_gap_ends": len(gap_ends),
    }


def holdout_decisions(global_bins: np.ndarray, model_bins: dict[str, np.ndarray], gap_ends: list[int]) -> tuple[dict[str, dict[str, np.ndarray]], dict]:
    indices = np.arange((START - prior.T0) // prior.BIN_US,
                        (END - prior.T0) // prior.BIN_US - 1, dtype=np.int64)
    global_sum = np.r_[0, np.cumsum(global_bins, dtype=np.int64)]
    recent_global = global_sum[indices] - global_sum[indices - 2] > 0
    after_gap = prior.causal_restart_mask(gap_ends, len(global_bins))[indices]
    common = recent_global & ~after_gap
    audit = {"grid_decisions": int(len(indices)), "causal_global_eligible": int(common.sum()),
             "no_global_arrival_previous_10m": int((~recent_global).sum()),
             "within_120m_after_known_6h_gap": int(after_gap.sum()),
             "raw_arrivals": int(global_bins[(START - prior.T0)//prior.BIN_US:].sum()),
             "by_model": {}}
    rows = {}
    for alias in ALIASES:
        own_sum = np.r_[0, np.cumsum(model_bins[alias], dtype=np.int64)]
        active = own_sum[indices] - own_sum[indices - 12] > 0
        idx = indices[common & active]
        y = own_sum[idx + 2] - own_sum[idx]
        audit["by_model"][alias] = {
            "active_decisions": int(active.sum()), "eligible_decisions": int(len(idx)),
            "positive_targets": int(np.count_nonzero(y)), "zero_targets": int(np.count_nonzero(y == 0)),
            "model_arrivals": int(model_bins[alias][(START - prior.T0)//prior.BIN_US:].sum()),
        }
        rows[alias] = {"idx": idx, "y": y}
    return rows, audit


def make_holdout_rows(alias: str, code: int, chosen: dict[str, np.ndarray],
                      global_bins: np.ndarray, own_bins: np.ndarray) -> dict[str, np.ndarray]:
    idx = chosen["idx"]
    t = prior.T0 + idx * prior.BIN_US
    own = np.r_[0, np.cumsum(own_bins, dtype=np.int64)]
    total = np.r_[0, np.cumsum(global_bins, dtype=np.int64)]
    data: dict[str, np.ndarray] = {"t_us": t, "model_alias": np.full(len(idx), alias),
                                   "model_code": np.full(len(idx), code), "y": chosen["y"]}
    for minutes in (5, 10, 30, 60, 120):
        bins = minutes // 5
        data[f"m{minutes}"] = own[idx] - own[idx - bins]
        data[f"g{minutes}"] = total[idx] - total[idx - bins]
    minute_day = (t // prior.MIN_US) % 1440
    weekday = ((t // prior.DAY_US) + 3) % 7
    minute_week = weekday * 1440 + minute_day
    data["sin_day"] = np.sin(2 * np.pi * minute_day / 1440)
    data["cos_day"] = np.cos(2 * np.pi * minute_day / 1440)
    data["sin_week"] = np.sin(2 * np.pi * minute_week / (7 * 1440))
    data["cos_week"] = np.cos(2 * np.pi * minute_week / (7 * 1440))
    data["persistence"] = data["m10"]
    recent = np.stack([own[idx - 2 * k] - own[idx - 2 * (k + 1)] for k in range(12)], axis=1)
    data["recent_p90"] = np.sort(recent, axis=1)[:, 10]
    return data


def quality_gate(audit: dict, development: pa.Table, holdout: pa.Table) -> dict:
    reasons = []
    if audit["causal_global_eligible"] / audit["grid_decisions"] < 0.20:
        reasons.append("Global causal decision eligibility below 20%")
    for alias in ALIASES:
        row = audit["by_model"][alias]
        if row["eligible_decisions"] < 500:
            reasons.append(f"{alias}: fewer than 500 eligible decisions")
        if min(row["positive_targets"], row["zero_targets"]) < 100:
            reasons.append(f"{alias}: fewer than 100 positive or zero targets")
        if row["eligible_decisions"] / max(row["active_decisions"], 1) < 0.50:
            reasons.append(f"{alias}: fewer than 50% of active decisions retained")
    dev_key = set(zip(development["model_alias"].to_pylist(), development["t_us"].to_pylist()))
    new_key = list(zip(holdout["model_alias"].to_pylist(), holdout["t_us"].to_pylist()))
    if len(new_key) != len(set(new_key)) or any(key in dev_key for key in new_key):
        reasons.append("Duplicate (model alias,decision time) in development or holdout")
    t = holdout["t_us"].to_numpy(zero_copy_only=False)
    if len(t) and (np.any(t < START) or np.any(t + 2 * prior.BIN_US > END) or np.any(t % prior.BIN_US)):
        reasons.append("Holdout time grid or 10-minute target crossed the frozen boundary")
    return {"passes": not reasons, "reasons": reasons}


def dry_run() -> dict:
    hashes = guard_sources()
    development = pq.read_table(DEVELOPMENT)
    dev_audit = validate_development(development)
    source_audit = json.loads((SOURCE / "audit.json").read_text())
    selected = prior.select_models(source_audit)
    global_bins, model_bins, gap_ends, ingest = ingest_arrivals_until_end(SOURCE / "requests_sorted.parquet", selected)
    chosen, audit = holdout_decisions(global_bins, model_bins, gap_ends)
    pieces = [make_holdout_rows(alias, code, chosen[alias], global_bins, model_bins[alias])
              for code, alias in enumerate(ALIASES)]
    columns = list(pieces[0])
    holdout = pa.table({name: np.concatenate([piece[name] for piece in pieces]) for name in columns})
    gate = quality_gate(audit, development, holdout)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / "holdout_rows.parquet"
    pq.write_table(holdout, path, compression="zstd")
    result = {"status": "dry_run", "hashes": hashes, "script_sha256": prior.sha256(Path(__file__)),
              "holdout_rows_sha256": prior.sha256(path), "holdout_rows": holdout.num_rows,
              "development": dev_audit, "ingest": ingest, "holdout_audit": audit,
              "quality_gate": gate, "holdout_columns": holdout.column_names,
              "max_decision_t_us": int(pc.max(holdout["t_us"]).as_py()) if holdout.num_rows else None,
              "sealed_from_utc": "2025-10-16"}
    (OUTPUT / "dry_run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def normalized_features(vals: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    sm = np.maximum.reduce([np.ones(len(vals["m10"]), dtype=np.float64),
                            vals["m10"].astype(float), vals["m30"] / 3.0, vals["m60"] / 6.0])
    sg = np.maximum.reduce([np.ones(len(vals["g10"]), dtype=np.float64),
                            vals["g10"].astype(float), vals["g30"] / 3.0, vals["g60"] / 6.0])
    arrays = [vals["model_code"].astype(float)]
    for minutes in (5, 10, 30, 60, 120):
        arrays.append(vals[f"m{minutes}"] / (sm * minutes / 10.0))
    for minutes in (5, 10, 30, 60, 120):
        arrays.append(vals[f"g{minutes}"] / (sg * minutes / 10.0))
    arrays.extend(vals[key].astype(float) for key in ("sin_day", "cos_day", "sin_week", "cos_week"))
    return np.column_stack(arrays).astype(np.float64), sm


def _values(table: pa.Table) -> dict[str, np.ndarray]:
    return {name: table[name].to_numpy(zero_copy_only=False) for name in table.column_names}


def score() -> dict:
    hashes = guard_sources()
    saved = json.loads((OUTPUT / "dry_run.json").read_text())
    holdout_path = OUTPUT / "holdout_rows.parquet"
    if saved["hashes"] != hashes or saved["script_sha256"] != prior.sha256(Path(__file__)):
        raise ValueError("Dry-run source, protocol, or script hash changed")
    if not saved["quality_gate"]["passes"] or saved["holdout_rows_sha256"] != prior.sha256(holdout_path):
        raise ValueError("Dry-run quality gate failed or holdout rows changed")
    dev_table = pq.read_table(DEVELOPMENT)
    hold_table = pq.read_table(holdout_path)
    validate_development(dev_table)
    if hold_table.num_rows != saved["holdout_rows"]:
        raise ValueError("Dry-run holdout-row count changed")
    dev = _values(dev_table)
    test = _values(hold_table)
    if len(test["t_us"]) == 0 or np.max(test["t_us"]) >= END:
        raise ValueError("No holdout rows or sealed dates entered the score")
    if not set(prior.ARRIVAL_FEATURES + ["y", "t_us", "model_alias", *BASELINES]).issubset(test):
        raise ValueError("Holdout feature schema changed")
    n = len(dev["y"])
    aliases = dev["model_alias"]
    weights = np.zeros(n, dtype=np.float64)
    for alias in ALIASES:
        mask = aliases == alias
        weights[mask] = n / (2 * int(mask.sum()))
    params = dict(loss="quantile", quantile=0.9, max_iter=200, learning_rate=0.05,
                  max_leaf_nodes=15, min_samples_leaf=100, l2_regularization=1.0,
                  early_stopping=False, random_state=42)
    x_abs_dev = np.column_stack([dev[key] for key in prior.ARRIVAL_FEATURES]).astype(float)
    x_abs_test = np.column_stack([test[key] for key in prior.ARRIVAL_FEATURES]).astype(float)
    x_norm_dev, sm_dev = normalized_features(dev)
    x_norm_test, sm_test = normalized_features(test)
    if not np.isfinite(x_norm_dev).all() or not np.isfinite(x_norm_test).all():
        raise ValueError("Non-finite scale-normalized features")
    absolute = HistGradientBoostingRegressor(**params)
    relative = HistGradientBoostingRegressor(**params)
    t0 = time.perf_counter()
    absolute.fit(x_abs_dev, dev["y"].astype(float), sample_weight=weights)
    absolute_fit_seconds = time.perf_counter() - t0
    t1 = time.perf_counter()
    relative.fit(x_norm_dev, np.log1p(dev["y"] / sm_dev), sample_weight=weights)
    relative_fit_seconds = time.perf_counter() - t1
    t2 = time.perf_counter()
    q_abs = np.maximum(0.0, absolute.predict(x_abs_test))
    absolute_predict_seconds = time.perf_counter() - t2
    t3 = time.perf_counter()
    q_relative = np.maximum(0.0, sm_test * np.expm1(relative.predict(x_norm_test)))
    relative_predict_seconds = time.perf_counter() - t3
    predictions = {"absolute_hgb": q_abs, "scale_normalized_hgb": q_relative,
                   "persistence": test["persistence"].astype(float),
                   "recent_p90": test["recent_p90"].astype(float)}
    if not all(np.isfinite(x).all() for x in predictions.values()):
        raise ValueError("Non-finite holdout predictions")
    y = test["y"].astype(float)
    model_alias = test["model_alias"]
    all_rows = np.ones(len(y), dtype=bool)
    scored = {name: prior.summarize(y, pred, model_alias, all_rows) for name, pred in predictions.items()}
    weekly = []
    for week in range(9):
        start = START + week * 7 * prior.DAY_US
        end = min(END, start + 7 * prior.DAY_US)
        mask = (test["t_us"] >= start) & (test["t_us"] < end)
        record = {"block": week + 1, "start_us": start, "end_us": end,
                  "complete_7d": end - start == 7 * prior.DAY_US, "rows": int(mask.sum()),
                  "by_model_rows": {a: int(np.count_nonzero(mask & (model_alias == a))) for a in ALIASES},
                  "scores": {name: prior.summarize(y, pred, model_alias, mask)
                             for name, pred in predictions.items()}}
        weekly.append(record)
    fit_blocks = [w for w in weekly if w["complete_7d"] and
                  all(w["by_model_rows"][a] >= 100 for a in ALIASES)]
    wins = sum(w["scores"]["scale_normalized_hgb"]["equal_model_mean_pinball"] <
               w["scores"]["absolute_hgb"]["equal_model_mean_pinball"] for w in fit_blocks)
    rel = scored["scale_normalized_hgb"]
    comparisons = ("absolute_hgb", "persistence", "recent_p90")
    gains = {name: float((scored[name]["equal_model_mean_pinball"] - rel["equal_model_mean_pinball"]) /
                         scored[name]["equal_model_mean_pinball"]) for name in comparisons}
    success = (len(fit_blocks) >= 4 and wins / len(fit_blocks) >= 0.75 and
               all(gains[name] >= 0.10 for name in comparisons) and
               all(rel["by_model"][a]["coverage"] >= 0.85 and
                   rel["by_model"][a]["mean_pinball"] <= scored["persistence"]["by_model"][a]["mean_pinball"] and
                   rel["by_model"][a]["mean_pinball"] <= scored["recent_p90"]["by_model"][a]["mean_pinball"]
                   for a in ALIASES))
    result = {"status": "scored", "scope": "post_first_test_exploratory_withheld_period",
              "hashes": hashes, "script_sha256": prior.sha256(Path(__file__)),
              "holdout_rows_sha256": saved["holdout_rows_sha256"],
              "development_rows": n, "holdout_rows": len(y), "selected_aliases": list(ALIASES),
              "method_features": {"absolute_hgb": prior.ARRIVAL_FEATURES,
                                  "scale_normalized_hgb": SCALE_FEATURES},
              "normalized_formula": {
                  "s_m": "max(1,m10,m30/3,m60/6)",
                  "s_g": "max(1,g10,g30/3,g60/6)",
                  "ratio_mw": "m_w/(s_m*w/10)", "ratio_gw": "g_w/(s_g*w/10)",
                  "training_target": "log1p(y/s_m)",
                  "inverse_prediction": "max(0,s_m*expm1(q_z))"},
              "holdout": scored, "weekly_holdout": weekly,
              "success": {"passes": bool(success), "relative_gains": gains,
                          "qualifying_full_blocks": len(fit_blocks), "block_wins_vs_absolute": int(wins)},
              "timing": {"absolute_fit_seconds": absolute_fit_seconds,
                         "relative_fit_seconds": relative_fit_seconds,
                         "absolute_predict_seconds": absolute_predict_seconds,
                         "relative_predict_seconds": relative_predict_seconds},
              "sklearn_version": sklearn.__version__, "sealed_from_utc": "2025-10-16"}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(absolute, OUTPUT / "absolute_hgb.joblib")
    joblib.dump(relative, OUTPUT / "scale_normalized_hgb.joblib")
    pred_path = OUTPUT / "holdout_predictions.parquet"
    pq.write_table(pa.table({"t_us": test["t_us"], "model_alias": model_alias,
                             "y": test["y"], **predictions}), pred_path, compression="zstd")
    result["prediction_sha256"] = prior.sha256(pred_path)
    result["model_sha256"] = {"absolute_hgb": prior.sha256(OUTPUT / "absolute_hgb.joblib"),
                              "scale_normalized_hgb": prior.sha256(OUTPUT / "scale_normalized_hgb.joblib")}
    (OUTPUT / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--score", action="store_true")
    args = parser.parse_args()
    result = dry_run() if args.dry_run else score()
    print(json.dumps({"status": result["status"], "holdout_rows": result.get("holdout_rows"),
                      "quality_gate": result.get("quality_gate"), "success": result.get("success")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
