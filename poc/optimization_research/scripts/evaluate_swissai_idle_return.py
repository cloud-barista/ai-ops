"""Causal SwissAI idle-return signal, with a locked chronological test mode.

The outcome is the next *request arrival*, not a VM scale-in benefit.  The
test mode requires the independently frozen protocol and fitted selection
specification; fitting and model selection only see train/validation labels.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.preprocessing import StandardScaler

from fetch_swissai_trace import file_digest as source_digest, local_source_path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/swissai_serving_trace"
OUTPUT = ROOT / "artifacts/swissai_idle_return_v1"
PROTOCOL = ROOT / "reports/swissai_idle_return_protocol.md"
PROTOCOL_SHA256 = "fde797fe622163b796565ebbb97a88656ae1c1a4e1a77f8035c370037cc9e415"
RAW_SHA256 = "79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7"
COMPACT_SHA256 = "d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b"
SOURCE_ROWS = 16_329_237
US = 1_000_000
MIN_US = 60 * US
HOUR_US = 60 * MIN_US
DAY_US = 24 * HOUR_US
IDLE_US = 5 * MIN_US
TARGET_US = 10 * MIN_US
GAP_US = 6 * HOUR_US
COOLDOWN_US = 120 * MIN_US
SPLIT_DATES = ("2025-01-16", "2025-06-16", "2025-07-16", "2025-08-16")
SPLIT_NAMES = ("fit", "validation", "test")
SPLIT_STARTS = np.array([
    int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * US)
    for d in SPLIT_DATES[:-1]
], dtype=np.int64)
SPLIT_ENDS = np.array([
    int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * US)
    for d in SPLIT_DATES[1:]
], dtype=np.int64)
FEATURES = (
    "own10_log", "own30_log", "own120_log",
    "global10_log", "global30_log", "global120_log",
    "gap20_mean_log", "gap20_p90_log", "gap20_count", "age_min_log",
    "sin_day", "cos_day", "sin_week", "cos_week",
)
EXPECTED_SUPPORT = {
    "fit": (2237, 846), "validation": (780, 335), "test": (845, 276),
}
EXPECTED_TEST_WEEKS = ((123, 32), (239, 84), (217, 60), (157, 42), (109, 58))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _refuse_existing(*paths: Path) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite fixed evaluation artifacts: " + ", ".join(existing))


def _alias(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]


@dataclass
class Trace:
    global_us: np.ndarray
    model_requests: dict[str, np.ndarray]
    source_hashes: dict[str, str]


def load_trace(source_dir: Path = SOURCE, *, verify_raw: bool = True) -> Trace:
    """Validate the audited compact file and keep only arrival data in memory."""
    audit_path = source_dir / "audit.json"
    compact_path = source_dir / "requests_sorted.parquet"
    raw_path = local_source_path(source_dir)
    audit = json.loads(audit_path.read_text())
    if audit["source"]["sha256"] != RAW_SHA256 or audit["source"]["revision"] != "c3dd66602b564ce189f7db81115620413e646cd5":
        raise ValueError("Pinned SwissAI source audit differs")
    if audit["compact"]["rows"] != SOURCE_ROWS or audit["counts"]["compact_rows"] != SOURCE_ROWS:
        raise ValueError("SwissAI source row count differs")
    if sha256(compact_path) != COMPACT_SHA256:
        raise ValueError("Sorted SwissAI compact SHA256 differs")
    if verify_raw and source_digest(raw_path) != (audit["source"]["bytes"], RAW_SHA256):
        raise ValueError("SwissAI raw source SHA256 differs")

    pf = pq.ParquetFile(compact_path)
    if not {"created_utc_us", "model", "source_row"}.issubset(pf.schema_arrow.names):
        raise ValueError("SwissAI compact file lacks arrival columns")
    if pf.metadata.num_rows != SOURCE_ROWS:
        raise ValueError("SwissAI compact metadata row count differs")
    global_parts: list[np.ndarray] = []
    by_alias: dict[str, list[np.ndarray]] = {}
    seen_names: dict[str, str] = {}
    previous: int | None = None
    previous_source_row: int | None = None
    for batch in pf.iter_batches(columns=["created_utc_us", "model", "source_row"], batch_size=500_000):
        created_col = batch.column("created_utc_us")
        if created_col.null_count:
            raise ValueError("Unparsed creation time")
        created = created_col.to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
        source_row = batch.column("source_row").to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
        if len(created) == 0:
            continue
        if np.any(np.diff(created) < 0) or (previous is not None and created[0] < previous):
            raise ValueError("Compact arrivals are not sorted")
        if np.any(np.diff(source_row)[np.diff(created) == 0] <= 0):
            raise ValueError("Compact tied arrivals are not sorted by source_row")
        if previous is not None and created[0] == previous and source_row[0] <= previous_source_row:
            raise ValueError("Compact tied arrivals cross a batch out of source-row order")
        previous = int(created[-1])
        previous_source_row = int(source_row[-1])
        global_parts.append(created)
        encoded = pc.dictionary_encode(pc.fill_null(batch.column("model"), "\\N"))
        codes = encoded.indices.to_numpy(zero_copy_only=False).astype(np.int32, copy=False)
        dictionary = encoded.dictionary.to_pylist()
        order = np.argsort(codes, kind="stable")
        sorted_codes = codes[order]
        starts = np.r_[0, np.flatnonzero(np.diff(sorted_codes)) + 1]
        ends = np.r_[starts[1:], len(order)]
        for left, right in zip(starts, ends):
            name = str(dictionary[int(sorted_codes[left])])
            if name == "\\N":
                continue
            alias = _alias(name)
            if alias in seen_names and seen_names[alias] != name:
                raise ValueError("Model alias collision")
            seen_names[alias] = name
            by_alias.setdefault(alias, []).append(created[order[left:right]])
    global_us = np.concatenate(global_parts)
    if len(global_us) != SOURCE_ROWS:
        raise ValueError("Compact scan row count differs")
    model_requests = {a: np.concatenate(parts) for a, parts in by_alias.items()}
    return Trace(global_us, model_requests, {
        "raw_sha256": RAW_SHA256,
        "compact_sha256": COMPACT_SHA256,
        "audit_sha256": sha256(audit_path),
    })


def _counts_before(times: np.ndarray, t: np.ndarray, width_us: int) -> np.ndarray:
    return np.searchsorted(times, t, side="left") - np.searchsorted(times, t - width_us, side="left")


def _gap_features(unique_us: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Use at most 20 fully observed prior distinct-arrival intervals."""
    gaps = np.diff(unique_us)
    means = np.zeros(len(positions), dtype=np.float64)
    p90s = np.zeros(len(positions), dtype=np.float64)
    counts = np.zeros(len(positions), dtype=np.float64)
    for j, position in enumerate(positions):
        end = int(position)
        values = gaps[max(0, end - 20):end].astype(np.float64) / MIN_US
        if len(values):
            means[j] = float(np.log1p(values.mean()))
            p90s[j] = float(np.log1p(np.sort(values)[math.ceil(.9 * len(values)) - 1]))
            counts[j] = len(values)
    return means, p90s, counts


def build_episodes(trace: Trace, *, include_test_labels: bool) -> pd.DataFrame:
    """Create one decision per distinct-model arrival gap, on exact us boundaries."""
    global_us = np.asarray(trace.global_us, dtype=np.int64)
    if len(global_us) < 2 or np.any(np.diff(global_us) < 0):
        raise ValueError("Global arrivals must be sorted")
    gap_ends = global_us[1:][np.diff(global_us) >= GAP_US]
    pieces: list[pd.DataFrame] = []
    for alias in sorted(trace.model_requests):
        own = np.asarray(trace.model_requests[alias], dtype=np.int64)
        if not len(own):
            continue
        if np.any(np.diff(own) < 0):
            raise ValueError("Model arrivals must be sorted")
        distinct = np.unique(own)
        t_all = distinct + IDLE_US
        next_us = np.r_[distinct[1:], np.iinfo(np.int64).max]
        split_all = np.searchsorted(SPLIT_ENDS, t_all, side="right")
        in_split = split_all < 3
        split_ix = split_all[in_split]
        valid = np.zeros(len(distinct), dtype=bool)
        valid[in_split] = (
            (t_all[in_split] >= SPLIT_STARTS[split_ix])
            & (t_all[in_split] + TARGET_US <= SPLIT_ENDS[split_ix])
        )
        valid &= t_all + TARGET_US <= global_us[-1]
        valid &= next_us > t_all  # an arrival exactly at t is already known
        t = t_all[valid]
        if not len(t):
            continue
        original_positions = np.flatnonzero(valid)
        global_recent = _counts_before(global_us, t, TARGET_US) > 0
        if len(gap_ends):
            last_gap = np.searchsorted(gap_ends, t, side="right") - 1
            cooldown = (last_gap >= 0) & (t < gap_ends[np.maximum(last_gap, 0)] + COOLDOWN_US)
        else:
            cooldown = np.zeros(len(t), dtype=bool)
        eligible = global_recent & ~cooldown
        t = t[eligible]
        pos = original_positions[eligible]
        if not len(t):
            continue
        split = split_all[pos]
        y = np.full(len(t), -1, dtype=np.int8)
        label_allowed = np.ones(len(t), dtype=bool) if include_test_labels else split < 2
        y[label_allowed] = (next_us[pos[label_allowed]] < t[label_allowed] + TARGET_US).astype(np.int8)
        gap_mean, gap_p90, gap_count = _gap_features(distinct, pos)
        day_phase = 2 * np.pi * ((t % DAY_US) / DAY_US)
        weekday = ((t // DAY_US + 3) % 7).astype(np.float64)
        week_phase = 2 * np.pi * (weekday + ((t % DAY_US) / DAY_US)) / 7
        future_global = np.zeros(len(t), dtype=bool)
        future_allowed = np.ones(len(t), dtype=bool) if include_test_labels else split < 2
        future_global[future_allowed] = _counts_before(
            global_us, t[future_allowed] + TARGET_US, TARGET_US) > 0
        data: dict[str, Any] = {
            "model_alias": np.repeat(alias, len(t)), "t_us": t, "split": split.astype(np.int8), "y": y,
            "future_global_observed": future_global,
            "gap20_mean_log": gap_mean, "gap20_p90_log": gap_p90, "gap20_count": gap_count,
            "age_min_log": np.log1p((t - distinct[0]).astype(np.float64) / MIN_US),
            "sin_day": np.sin(day_phase), "cos_day": np.cos(day_phase),
            "sin_week": np.sin(week_phase), "cos_week": np.cos(week_phase),
            "first_seen_us": np.repeat(distinct[0], len(t)),
        }
        for w in (10, 30, 120):
            own_count = _counts_before(own, t, w * MIN_US)
            global_count = _counts_before(global_us, t, w * MIN_US)
            data[f"own{w}"] = own_count
            data[f"global{w}"] = global_count
            data[f"own{w}_log"] = np.log1p(own_count)
            data[f"global{w}_log"] = np.log1p(global_count)
        pieces.append(pd.DataFrame(data))
    if not pieces:
        return pd.DataFrame(columns=["model_alias", "t_us", "split", "y", *FEATURES])
    rows = pd.concat(pieces, ignore_index=True).sort_values(["t_us", "model_alias"], kind="stable").reset_index(drop=True)
    if rows.duplicated(["model_alias", "t_us"]).any():
        raise ValueError("Duplicate model decision")
    if not np.isfinite(rows[list(FEATURES)].to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite arrival feature")
    return rows


def _candidate_sha(rows: pd.DataFrame) -> str:
    """Hash candidate keys and causal features, without outcome values."""
    h = hashlib.sha256()
    columns = ["model_alias", "t_us", "split", "first_seen_us", "own10", "own30", "own120", "global10", *FEATURES]
    for row in rows[columns].itertuples(index=False, name=None):
        h.update(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _labeled_pretest_sha(rows: pd.DataFrame) -> str:
    pretest = rows[rows.split < 2]
    if (pretest.y < 0).any():
        raise ValueError("Fit/validation label missing")
    h = hashlib.sha256()
    for alias, t, split, label in pretest[["model_alias", "t_us", "split", "y"]].itertuples(index=False, name=None):
        h.update(f"{alias},{int(t)},{int(split)},{int(label)}\n".encode())
    return h.hexdigest()


def _support(rows: pd.DataFrame) -> dict[str, Any]:
    by_split: dict[str, Any] = {}
    for code, name in enumerate(SPLIT_NAMES):
        subset = rows[rows.split == code]
        if (subset.y < 0).any():
            raise ValueError("Support audit needs labels from every split")
        by_split[name] = {
            "rows": int(len(subset)), "positive": int(subset.y.sum()),
            "negative": int(len(subset) - subset.y.sum()),
            "models": int(subset.model_alias.nunique()),
            "future_global_zero": int((~subset.future_global_observed).sum()),
        }
    test = rows[rows.split == 2]
    other_recent = (test.global10.to_numpy(dtype=np.int64) - test.own10.to_numpy(dtype=np.int64)) > 0
    online_support = {
        "rows": int(other_recent.sum()),
        "positive": int(test.y.to_numpy(dtype=np.int8)[other_recent].sum()),
        "negative": int(other_recent.sum() - test.y.to_numpy(dtype=np.int8)[other_recent].sum()),
    }
    start = int(SPLIT_STARTS[2])
    weeks = {}
    for k in range(5):
        left = start + k * 7 * DAY_US
        right = min(start + (k + 1) * 7 * DAY_US, int(SPLIT_ENDS[2]))
        subset = test[(test.t_us >= left) & (test.t_us < right)]
        weeks[str(k)] = {"rows": int(len(subset)), "positive": int(subset.y.sum()),
                         "negative": int(len(subset) - subset.y.sum()), "complete": bool(k < 4)}
    fit = by_split["fit"]
    val = by_split["validation"]
    tst = by_split["test"]
    exact_support = all((by_split[name]["rows"], by_split[name]["positive"]) == pair
                        for name, pair in EXPECTED_SUPPORT.items())
    exact_weeks = all((weeks[str(k)]["rows"], weeks[str(k)]["positive"]) == pair
                      for k, pair in enumerate(EXPECTED_TEST_WEEKS))
    gate = (
        exact_support and exact_weeks
        and fit["rows"] >= 1000 and fit["positive"] >= 100 and fit["negative"] >= 100
        and val["rows"] >= 500 and val["positive"] >= 100 and val["negative"] >= 100
        and tst["rows"] >= 500 and tst["positive"] >= 100 and tst["negative"] >= 100
        and all(weeks[str(k)]["rows"] >= 100 and weeks[str(k)]["positive"] >= 20
                and weeks[str(k)]["negative"] >= 20 for k in range(4))
    )
    return {"by_split": by_split, "test_weeks": weeks,
            "test_other_model_recent": online_support,
            "exact_support_match": bool(exact_support), "exact_week_match": bool(exact_weeks),
            "support_gate_pass": bool(gate)}


def dry_run(trace: Trace, output: Path = OUTPUT) -> dict[str, Any]:
    if (output / "selection_manifest.json").exists():
        raise FileExistsError("A selection freeze already exists; dry-run cannot be replaced")
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("Frozen idle-return protocol SHA256 differs")
    rows = build_episodes(trace, include_test_labels=True)
    support = _support(rows)
    report = {
        "mode": "dry_run_support_only", "source_hashes": trace.source_hashes,
        "protocol_sha256": PROTOCOL_SHA256,
        "code_sha256": sha256(Path(__file__)), "candidate_sha256": _candidate_sha(rows),
        "fit_validation_labeled_sha256": _labeled_pretest_sha(rows),
        **support,
    }
    _json(output / "dry_run.json", report)
    return report


def _verify_dry_run(rows: pd.DataFrame, trace: Trace, path: Path, expected_sha: str) -> dict[str, Any]:
    if sha256(path) != expected_sha:
        raise ValueError("Dry-run SHA256 changed")
    report = json.loads(path.read_text())
    if not report["support_gate_pass"]:
        raise ValueError("Frozen support gate failed")
    if report["source_hashes"] != trace.source_hashes or report["candidate_sha256"] != _candidate_sha(rows):
        raise ValueError("Dry-run source or candidates changed")
    if report["fit_validation_labeled_sha256"] != _labeled_pretest_sha(rows):
        raise ValueError("Dry-run fit/validation labels changed")
    if report["protocol_sha256"] != PROTOCOL_SHA256 or sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("Frozen idle-return protocol differs")
    if report["code_sha256"] != sha256(Path(__file__)):
        raise ValueError("Dry-run implementation changed")
    return report


def _history(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Count only earlier eligible outcomes whose full 10m window ended < t."""
    n = len(rows)
    count = np.zeros(n, dtype=np.int32)
    success = np.zeros(n, dtype=np.int32)
    for _alias_name, group in rows.groupby("model_alias", sort=False):
        group = group.sort_values("t_us")
        positions = group.index.to_numpy()
        times = group.t_us.to_numpy(dtype=np.int64)
        labels = group.y.to_numpy(dtype=np.int8)
        if np.any(np.diff(times) <= 0):
            raise ValueError("Model episode times must increase")
        available = times + TARGET_US
        m = np.searchsorted(available, times, side="left")
        # A fit/validation row never uses any test outcome.  Test scoring is
        # called only with test labels present after the freeze.
        if np.any(labels[:int(m.max(initial=0))] < 0):
            raise ValueError("Historical episode outcome is unavailable")
        prefix = np.r_[0, np.cumsum(np.maximum(labels, 0), dtype=np.int64)]
        count[positions] = m
        success[positions] = prefix[m]
    return count, success


def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((y.astype(float) - p) ** 2))


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = p.astype(float)
    return {
        "brier": _brier(y, p), "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])),
        "observed_rate": float(np.mean(y)), "predicted_mean": float(np.mean(p)),
    }


def _calibration(y: np.ndarray, p: np.ndarray) -> list[dict[str, float | int]]:
    result = []
    for k in range(5):
        low = k / 5
        high = (k + 1) / 5
        take = (p >= low) & ((p < high) if k < 4 else (p <= high))
        result.append({
            "lower": low, "upper": high, "rows": int(take.sum()),
            "predicted_mean": float(p[take].mean()) if take.any() else None,
            "observed_rate": float(y[take].mean()) if take.any() else None,
        })
    return result


def _empirical(rows: pd.DataFrame, prior: float, k: int) -> np.ndarray:
    count, success = _history(rows)
    return (k * prior + success) / (k + count)


def _poisson(rows: pd.DataFrame, width_min: int) -> np.ndarray:
    return -np.expm1(-10 * rows[f"own{width_min}"].to_numpy(dtype=float) / width_min)


def fit_validate(trace: Trace, *, dry_run_path: Path, expected_dry_run_sha256: str, output: Path = OUTPUT) -> dict[str, Any]:
    """Fit only Jan--Jun, select only Jun--Jul; never expose July--Aug y."""
    fitted_path = output / "selected_model.joblib"
    spec_path = output / "model_spec.json"
    _refuse_existing(fitted_path, spec_path)
    rows = build_episodes(trace, include_test_labels=False)
    _verify_dry_run(rows, trace, dry_run_path, expected_dry_run_sha256)
    fit = rows[rows.split == 0]
    val = rows[rows.split == 1]
    X_fit = fit[list(FEATURES)].to_numpy(dtype=np.float64)
    X_val = val[list(FEATURES)].to_numpy(dtype=np.float64)
    y_fit = fit.y.to_numpy(dtype=np.int8)
    y_val = val.y.to_numpy(dtype=np.int8)
    scaler = StandardScaler().fit(X_fit)
    logistic = LogisticRegression(C=1, solver="lbfgs", max_iter=2000, class_weight=None)
    logistic.fit(scaler.transform(X_fit), y_fit)
    boosted = HistGradientBoostingClassifier(
        max_iter=100, learning_rate=.05, max_leaf_nodes=7,
        min_samples_leaf=20, l2_regularization=1, early_stopping=False, random_state=42,
    )
    boosted.fit(X_fit, y_fit)
    model_scores = {
        "logistic": _metrics(y_val, logistic.predict_proba(scaler.transform(X_val))[:, 1]),
        "boosted": _metrics(y_val, boosted.predict_proba(X_val)[:, 1]),
    }
    selected = min(("logistic", "boosted"), key=lambda key: (model_scores[key]["brier"], key != "logistic"))
    prior = (int(y_fit.sum()) + .5) / (len(y_fit) + 1)
    # The history routine is called on all pre-test episodes so a validation
    # row sees only fully matured earlier fit/validation outcomes.
    pretest = rows[rows.split < 2].copy().reset_index(drop=True)
    k_scores = {str(k): _brier(y_val, _empirical(pretest, prior, k)[len(fit):]) for k in (5, 20, 100)}
    selected_k = min((5, 20, 100), key=lambda k: (k_scores[str(k)], k))
    width_scores = {str(w): _brier(y_val, _poisson(val, w)) for w in (30, 120)}
    selected_width = min((120, 30), key=lambda w: (width_scores[str(w)], w != 120))
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump({"selected": selected, "scaler": scaler, "model": logistic if selected == "logistic" else boosted}, fitted_path)
    spec = {
        "mode": "fit_validation_only", "selected_model": selected,
        "validation_model_metrics": model_scores,
        "global_prior": prior, "selected_shrinkage_k": selected_k,
        "validation_empirical_brier_by_k": k_scores,
        "selected_poisson_width_minutes": selected_width,
        "validation_poisson_brier_by_width": width_scores,
        "fit_rows": int(len(fit)), "validation_rows": int(len(val)),
        "fit_positive": int(y_fit.sum()), "validation_positive": int(y_val.sum()),
        "features": list(FEATURES), "hyperparameters": {
            "logistic": {"C": 1, "solver": "lbfgs", "max_iter": 2000, "class_weight": None},
            "boosted": {"max_iter": 100, "learning_rate": .05, "max_leaf_nodes": 7,
                        "min_samples_leaf": 20, "l2_regularization": 1,
                        "early_stopping": False, "random_state": 42},
        },
        "source_hashes": trace.source_hashes,
        "dry_run_sha256": expected_dry_run_sha256,
        "fit_validation_candidate_sha256": _candidate_sha(pretest),
        "fit_validation_labeled_sha256": _labeled_pretest_sha(pretest),
        "code_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(fitted_path),
        "library_versions": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
    }
    _json(spec_path, spec)
    return spec


def freeze_selection(*, protocol_path: Path, dry_run_path: Path, model_spec_path: Path,
                     expected_protocol_sha256: str, expected_dry_run_sha256: str,
                     expected_model_spec_sha256: str, manifest_path: Path) -> dict[str, Any]:
    """Create a separate post-validation manifest without editing the criteria."""
    _refuse_existing(manifest_path)
    if expected_protocol_sha256 != PROTOCOL_SHA256 or sha256(protocol_path) != expected_protocol_sha256:
        raise ValueError("Frozen criteria protocol SHA256 differs")
    if not re.search(r"^\*\*상태:\s*FROZEN\b", protocol_path.read_text(), flags=re.MULTILINE):
        raise ValueError("Criteria protocol is not FROZEN")
    if sha256(dry_run_path) != expected_dry_run_sha256 or sha256(model_spec_path) != expected_model_spec_sha256:
        raise ValueError("Dry-run or selected specification SHA256 differs")
    dry = json.loads(dry_run_path.read_text())
    spec = json.loads(model_spec_path.read_text())
    if (not dry["support_gate_pass"] or dry["protocol_sha256"] != PROTOCOL_SHA256
            or dry["code_sha256"] != sha256(Path(__file__))):
        raise ValueError("Dry-run gate or criteria protocol differs")
    if spec["dry_run_sha256"] != expected_dry_run_sha256 or spec["code_sha256"] != sha256(Path(__file__)):
        raise ValueError("Selected specification has stale code or dry-run")
    if spec["fit_validation_labeled_sha256"] != dry["fit_validation_labeled_sha256"]:
        raise ValueError("Fit/validation label digest differs between selection and dry-run")
    model_path = model_spec_path.parent / "selected_model.joblib"
    if sha256(model_path) != spec["model_sha256"]:
        raise ValueError("Selected model artifact SHA256 differs")
    manifest = {
        "status": "FROZEN_SELECTION",
        "protocol_sha256": expected_protocol_sha256,
        "dry_run_sha256": expected_dry_run_sha256,
        "model_spec_sha256": expected_model_spec_sha256,
        "model_sha256": spec["model_sha256"],
        "code_sha256": spec["code_sha256"],
        "source_hashes": spec["source_hashes"],
        "selected_model": spec["selected_model"],
        "selected_shrinkage_k": spec["selected_shrinkage_k"],
        "selected_poisson_width_minutes": spec["selected_poisson_width_minutes"],
    }
    _json(manifest_path, manifest)
    return manifest


def _require_frozen(protocol_path: Path, expected_protocol_sha256: str, model_spec_path: Path,
                    expected_model_spec_sha256: str, dry_run_path: Path, expected_dry_run_sha256: str,
                    manifest_path: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    for value in (expected_protocol_sha256, expected_model_spec_sha256,
                  expected_dry_run_sha256, expected_manifest_sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Test scoring requires four explicit SHA256 locks")
    if expected_protocol_sha256 != PROTOCOL_SHA256 or sha256(protocol_path) != expected_protocol_sha256:
        raise ValueError("Frozen idle-return protocol SHA256 differs")
    if not re.search(r"^\*\*상태:\s*FROZEN\b", protocol_path.read_text(), flags=re.MULTILINE):
        raise ValueError("Idle-return protocol is not marked FROZEN")
    if sha256(model_spec_path) != expected_model_spec_sha256:
        raise ValueError("Selected model specification SHA256 differs")
    if sha256(dry_run_path) != expected_dry_run_sha256:
        raise ValueError("Dry-run SHA256 differs")
    spec = json.loads(model_spec_path.read_text())
    if spec["dry_run_sha256"] != expected_dry_run_sha256 or spec["code_sha256"] != sha256(Path(__file__)):
        raise ValueError("Locked model specification has stale data/code")
    if sha256(manifest_path) != expected_manifest_sha256:
        raise ValueError("Post-validation selection manifest SHA256 differs")
    manifest = json.loads(manifest_path.read_text())
    if manifest != {
        "status": "FROZEN_SELECTION", "protocol_sha256": expected_protocol_sha256,
        "dry_run_sha256": expected_dry_run_sha256,
        "model_spec_sha256": expected_model_spec_sha256,
        "model_sha256": spec["model_sha256"], "code_sha256": spec["code_sha256"],
        "source_hashes": spec["source_hashes"], "selected_model": spec["selected_model"],
        "selected_shrinkage_k": spec["selected_shrinkage_k"],
        "selected_poisson_width_minutes": spec["selected_poisson_width_minutes"],
    }:
        raise ValueError("Selection manifest content differs")
    return spec


def _test_metrics(rows: pd.DataFrame, p_model: np.ndarray, p_empirical: np.ndarray,
                  p_poisson: np.ndarray, p_poisson30: np.ndarray, p_poisson120: np.ndarray) -> dict[str, Any]:
    test = rows[rows.split == 2].reset_index(drop=True)
    y = test.y.to_numpy(dtype=np.int8)
    if np.any(y < 0):
        raise ValueError("Test labels are unavailable")
    predictions = {"selected_model": p_model, "empirical": p_empirical,
                   "poisson_selected": p_poisson, "poisson30": p_poisson30, "poisson120": p_poisson120}
    overall = {name: _metrics(y, p) for name, p in predictions.items()}
    calibration = {name: _calibration(y, p) for name, p in predictions.items()}
    by_week: dict[str, Any] = {}
    t = test.t_us.to_numpy(dtype=np.int64)
    for k in range(5):
        left = SPLIT_STARTS[2] + k * 7 * DAY_US
        right = min(SPLIT_STARTS[2] + (k + 1) * 7 * DAY_US, SPLIT_ENDS[2])
        take = (t >= left) & (t < right)
        model_groups = {}
        for alias in sorted(test.loc[take, "model_alias"].unique()):
            own = take & (test.model_alias.to_numpy() == alias)
            model_groups[alias] = {
                "rows": int(own.sum()), "positive": int(y[own].sum()),
                "brier": {name: _brier(y[own], p[own]) for name, p in predictions.items()},
            }
        metrics = {name: _metrics(y[take], p[take]) for name, p in predictions.items()}
        by_week[str(k)] = {
            "rows": int(take.sum()), "positive": int(y[take].sum()),
            "models": int(len(model_groups)), "metrics": metrics,
            "paired_brier_delta_vs_baseline": {
                baseline: metrics["selected_model"]["brier"] - metrics[baseline]["brier"]
                for baseline in ("empirical", "poisson_selected")
            },
            "by_model_alias": model_groups,
        }
    by_cohort: dict[str, Any] = {}
    fit_aliases = set(rows.loc[rows.split == 0, "model_alias"])
    first_seen = test.first_seen_us.to_numpy(dtype=np.int64)
    new_since_fit = first_seen >= SPLIT_ENDS[0]
    other_recent = (test.global10.to_numpy(dtype=np.int64) - test.own10.to_numpy(dtype=np.int64)) > 0
    cohorts = {
        "first_seen_since_fit_end": new_since_fit,
        "first_seen_before_fit_end": ~new_since_fit,
        "zero_fit_eligible_episodes": ~test.model_alias.isin(fit_aliases).to_numpy(),
        "has_fit_eligible_episode": test.model_alias.isin(fit_aliases).to_numpy(),
        "future_global_observed": test.future_global_observed.to_numpy(dtype=bool),
        "future_global_zero": ~test.future_global_observed.to_numpy(dtype=bool),
        "other_model_active_previous10m": other_recent,
    }
    for cohort, take in cohorts.items():
        by_cohort[cohort] = {
            "rows": int(take.sum()), "positive": int(y[take].sum()),
            "negative": int(take.sum() - y[take].sum()),
            "reportable": bool(cohort != "other_model_active_previous10m" or
                               (take.sum() >= 100 and y[take].sum() >= 20 and
                                take.sum() - y[take].sum() >= 20)),
            "metrics": {name: _metrics(y[take], p[take]) for name, p in predictions.items()} if take.any() else {},
        }
    by_model = {}
    for alias, group in test.groupby("model_alias"):
        ix = group.index.to_numpy()
        by_model[alias] = {"rows": len(ix), "positive": int(y[ix].sum()),
                           "brier": {name: _brier(y[ix], p[ix]) for name, p in predictions.items()}}
    eligible_macro = {alias: item for alias, item in by_model.items()
                      if item["rows"] >= 10 and 0 < item["positive"] < item["rows"]}
    macro = {name: float(np.mean([v["brier"][name] for v in eligible_macro.values()]))
             if eligible_macro else None for name in predictions}
    macro_meta = {"models_included": len(eligible_macro),
                  "models_excluded_fewer_than_10": sum(item["rows"] < 10 for item in by_model.values()),
                  "models_excluded_single_class": sum(item["rows"] >= 10 and
                                                        (item["positive"] == 0 or item["positive"] == item["rows"])
                                                        for item in by_model.values())}
    relative_gain = {
        baseline: (overall[baseline]["brier"] - overall["selected_model"]["brier"]) / overall[baseline]["brier"]
        for baseline in ("empirical", "poisson_selected")
    }
    passed = (
        all(relative_gain[baseline] >= .05 for baseline in ("empirical", "poisson_selected"))
        and sum(all(by_week[str(k)]["metrics"]["selected_model"]["brier"] <
                    by_week[str(k)]["metrics"][baseline]["brier"]
                    for baseline in ("empirical", "poisson_selected")) for k in range(4)) >= 3
    )
    # Paired resampling of model x calendar-week cells, never individual rows.
    cell = ((t - SPLIT_STARTS[2]) // (7 * DAY_US)).astype(np.int16)
    key = np.array([f"{a}:{w}" for a, w in zip(test.model_alias, cell)])
    cells = np.unique(key)
    rng = np.random.default_rng(42)
    bootstrap: dict[str, Any] = {}
    for baseline in ("empirical", "poisson_selected"):
        deltas = (y - p_model) ** 2 - (y - predictions[baseline]) ** 2
        sums = np.array([deltas[key == c].sum() for c in cells])
        counts = np.array([(key == c).sum() for c in cells])
        selected = rng.integers(0, len(cells), size=(2000, len(cells)))
        samples = sums[selected].sum(axis=1) / counts[selected].sum(axis=1)
        bootstrap[baseline] = {"paired_model_week_cells": len(cells),
                               "brier_delta": float(deltas.mean()),
                               "ci95_low": float(np.quantile(samples, .025)),
                               "ci95_high": float(np.quantile(samples, .975))}
    return {"overall": overall, "calibration": calibration,
            "relative_brier_gain_vs_selected_baselines": relative_gain,
            "by_week": by_week, "by_cohort": by_cohort,
            "model_macro_brier": macro, "model_macro_support": macro_meta,
            "by_model_alias": by_model,
            "paired_model_week_bootstrap": bootstrap,
            "exploratory_adoption_gate_pass": bool(passed)}


def score_test(trace: Trace, *, protocol_path: Path, expected_protocol_sha256: str,
               model_spec_path: Path, expected_model_spec_sha256: str,
               dry_run_path: Path, expected_dry_run_sha256: str,
               manifest_path: Path, expected_manifest_sha256: str,
               output: Path = OUTPUT) -> dict[str, Any]:
    """Only this mode computes test performance; locks are checked first."""
    _refuse_existing(output / "test_metrics.json", output / "test_predictions.parquet")
    spec = _require_frozen(protocol_path, expected_protocol_sha256, model_spec_path,
                           expected_model_spec_sha256, dry_run_path, expected_dry_run_sha256,
                           manifest_path, expected_manifest_sha256)
    rows = build_episodes(trace, include_test_labels=True)
    report = _verify_dry_run(rows, trace, dry_run_path, expected_dry_run_sha256)
    if _support(rows) != {key: report[key] for key in ("by_split", "test_weeks", "test_other_model_recent", "exact_support_match", "exact_week_match", "support_gate_pass")}:
        raise ValueError("Current label support differs from frozen dry-run")
    if spec["fit_validation_candidate_sha256"] != _candidate_sha(rows[rows.split < 2]) or spec["source_hashes"] != trace.source_hashes:
        raise ValueError("Selected model input differs")
    if spec["fit_validation_labeled_sha256"] != _labeled_pretest_sha(rows):
        raise ValueError("Fit/validation labels differ from frozen selection")
    model_path = model_spec_path.parent / "selected_model.joblib"
    if sha256(model_path) != spec["model_sha256"]:
        raise ValueError("Selected model artifact SHA256 differs")
    fitted = joblib.load(model_path)
    if fitted["selected"] != spec["selected_model"] or list(FEATURES) != spec["features"]:
        raise ValueError("Selected model definition differs")
    test = rows[rows.split == 2]
    X = test[list(FEATURES)].to_numpy(dtype=np.float64)
    if fitted["selected"] == "logistic":
        X = fitted["scaler"].transform(X)
    p_model = fitted["model"].predict_proba(X)[:, 1]
    p_empirical_all = _empirical(rows, float(spec["global_prior"]), int(spec["selected_shrinkage_k"]))
    p_empirical = p_empirical_all[rows.split.to_numpy() == 2]
    p_poisson30 = _poisson(test, 30)
    p_poisson120 = _poisson(test, 120)
    p_poisson = p_poisson30 if int(spec["selected_poisson_width_minutes"]) == 30 else p_poisson120
    result = _test_metrics(rows, p_model, p_empirical, p_poisson, p_poisson30, p_poisson120)
    selected_baseline = min(
        ("empirical", "poisson_selected"),
        key=lambda name: (
            spec["validation_empirical_brier_by_k"][str(spec["selected_shrinkage_k"])]
            if name == "empirical" else
            spec["validation_poisson_brier_by_width"][str(spec["selected_poisson_width_minutes"])],
            name != "empirical",
        ),
    )
    result["baseline_to_retain_if_gate_fails"] = selected_baseline
    result.update({"mode": "locked_test", "protocol_sha256": expected_protocol_sha256,
                   "model_spec_sha256": expected_model_spec_sha256,
                   "selection_manifest_sha256": expected_manifest_sha256,
                   "dry_run_sha256": expected_dry_run_sha256,
                   "source_hashes": trace.source_hashes, "candidate_sha256": _candidate_sha(rows),
                   "code_sha256": sha256(Path(__file__))})
    output.mkdir(parents=True, exist_ok=True)
    _json(output / "test_metrics.json", result)
    predictions = pd.DataFrame({"model_alias": test.model_alias.to_numpy(), "t_us": test.t_us.to_numpy(),
                                "y": test.y.to_numpy(), "p_selected_model": p_model,
                                "p_empirical": p_empirical, "p_poisson_selected": p_poisson,
                                "p_poisson30": p_poisson30, "p_poisson120": p_poisson120,
                                "future_global_observed": test.future_global_observed.to_numpy(),
                                "other_model_active_previous10m":
                                    ((test.global10 - test.own10) > 0).to_numpy()})
    predictions.to_parquet(output / "test_predictions.parquet", index=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--fit-validate", action="store_true")
    modes.add_argument("--freeze-selection", action="store_true")
    modes.add_argument("--score-test", action="store_true")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--dry-run-path", type=Path, default=OUTPUT / "dry_run.json")
    parser.add_argument("--model-spec", type=Path, default=OUTPUT / "model_spec.json")
    parser.add_argument("--selection-manifest", type=Path, default=OUTPUT / "selection_manifest.json")
    parser.add_argument("--expected-protocol-sha256", default="")
    parser.add_argument("--expected-dry-run-sha256", default="")
    parser.add_argument("--expected-model-spec-sha256", default="")
    parser.add_argument("--expected-selection-manifest-sha256", default="")
    args = parser.parse_args()
    if args.score_test:
        # Refuse to open test arrivals before confirming all freeze locks.
        _require_frozen(args.protocol, args.expected_protocol_sha256, args.model_spec,
                        args.expected_model_spec_sha256, args.dry_run_path, args.expected_dry_run_sha256,
                        args.selection_manifest, args.expected_selection_manifest_sha256)
        _refuse_existing(args.output / "test_metrics.json", args.output / "test_predictions.parquet")
    if args.freeze_selection:
        result = freeze_selection(protocol_path=args.protocol, dry_run_path=args.dry_run_path,
                                  model_spec_path=args.model_spec,
                                  expected_protocol_sha256=args.expected_protocol_sha256,
                                  expected_dry_run_sha256=args.expected_dry_run_sha256,
                                  expected_model_spec_sha256=args.expected_model_spec_sha256,
                                  manifest_path=args.selection_manifest)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    trace = load_trace(args.source)
    if args.dry_run:
        result = dry_run(trace, args.output)
    elif args.fit_validate:
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_dry_run_sha256):
            raise ValueError("Fit/validation requires explicit dry-run SHA256")
        result = fit_validate(trace, dry_run_path=args.dry_run_path,
                              expected_dry_run_sha256=args.expected_dry_run_sha256, output=args.output)
    else:
        result = score_test(trace, protocol_path=args.protocol,
                            expected_protocol_sha256=args.expected_protocol_sha256,
                            model_spec_path=args.model_spec,
                            expected_model_spec_sha256=args.expected_model_spec_sha256,
                            dry_run_path=args.dry_run_path,
                            expected_dry_run_sha256=args.expected_dry_run_sha256,
                            manifest_path=args.selection_manifest,
                            expected_manifest_sha256=args.expected_selection_manifest_sha256,
                            output=args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
