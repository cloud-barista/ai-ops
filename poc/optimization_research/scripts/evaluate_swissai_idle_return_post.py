"""Frozen follow-up for SwissAI 5-minute-idle request return.

This is an offline trace forecast.  A recorded request return is not an
observed SCALE_IN action, its effect, or a user latency SLO.

`freeze` never constructs post-2025-08-16 episodes.  `score` requires the
SHA-256 of the previously inspected freeze manifest.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

import evaluate_swissai_idle_return as original


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/swissai_idle_return_post_protocol.md"
PROTOCOL_SHA256 = "971d3fb3fa65d380be4a18ab83afbf886aaf4c4db8b8706179f13c3b8c05bddd"
MODEL_SPEC = ROOT / "artifacts/swissai_idle_return_v1/model_spec.json"
MODEL = ROOT / "artifacts/swissai_idle_return_v1/selected_model.joblib"
MODEL_SHA256 = "385201d2ba936f259847de7a52d8cdc57ec6a9109bed952bbf0151b9d46d59ae"
MODEL_SPEC_SHA256 = "f0211ebaeeb8f45260e45be5625be7472217f7891e447985762893fb2a5ca719"
ORIGINAL_EVALUATOR_SHA256 = "51707577c35ea9f4c1a810fb5dc5fe43313cb9ee634fe08f4e8bb7fe2a395966"
OUTPUT = ROOT / "artifacts/swissai_idle_return_post_v1"
POST_START = int(datetime(2025, 8, 16, tzinfo=timezone.utc).timestamp() * original.US)
POST_END = int(datetime(2025, 10, 16, tzinfo=timezone.utc).timestamp() * original.US)
KAPPA = 100
PRIOR_NUMERATOR = 846.5
PRIOR_DENOMINATOR = 2238
PRIOR = PRIOR_NUMERATOR / PRIOR_DENOMINATOR
EXPECTED_PRE = {0: (2237, 846), 1: (780, 335), 2: (845, 276)}
EXPECTED_POST = (1266, 443)
EXPECTED_WEEKS = ((203, 42), (372, 129), (388, 152), (24, 7), (14, 4),
                  (35, 14), (11, 1), (99, 38), (120, 56))
SUPPORTED_WEEKS = (0, 1, 2, 7, 8)
EXPECTED_POST_MODELS = 96
EXPECTED_NEW_MODELS = 89
EXPECTED_NEW_EPISODES = (913, 339)
EXPECTED_UNKNOWN_GLOBAL = 464
EXPECTED_BALANCED_MODELS = (24, 1061, 389)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def _refuse_existing(*paths: Path) -> None:
    found = [str(path) for path in paths if path.exists()]
    if found:
        raise FileExistsError("Refusing to overwrite frozen artifacts: " + ", ".join(found))


def _check_fixed_inputs() -> dict[str, Any]:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("Post-period protocol SHA-256 differs")
    if sha256(MODEL_SPEC) != MODEL_SPEC_SHA256:
        raise ValueError("Original model specification SHA-256 differs")
    spec = json.loads(MODEL_SPEC.read_text())
    if sha256(MODEL) != MODEL_SHA256 or spec["model_sha256"] != MODEL_SHA256:
        raise ValueError("Original selected model SHA-256 differs")
    if (sha256(Path(original.__file__)) != ORIGINAL_EVALUATOR_SHA256 or
            spec["code_sha256"] != ORIGINAL_EVALUATOR_SHA256):
        raise ValueError("Original episode implementation SHA-256 differs")
    if spec["selected_model"] != "logistic" or spec["selected_shrinkage_k"] != KAPPA:
        raise ValueError("Original frozen selection differs")
    if list(original.FEATURES) != spec["features"]:
        raise ValueError("Original model features differ")
    if spec["fit_rows"] != 2237 or spec["fit_positive"] != 846:
        raise ValueError("Original fit support differs")
    if spec["global_prior"] != PRIOR:
        raise ValueError("Original fixed prior differs")
    expected_versions = spec["library_versions"]
    if expected_versions["scikit_learn"] != sklearn.__version__:
        raise ValueError("Stored model requires its original scikit-learn version")
    if expected_versions["numpy"] != np.__version__:
        raise ValueError("Stored model requires its original NumPy version")
    if expected_versions["pandas"] != pd.__version__:
        raise ValueError("Stored model requires its original pandas version")
    return spec


def _pre_episodes(trace: original.Trace) -> pd.DataFrame:
    rows = original.build_episodes(trace, include_test_labels=True)
    for split, expected in EXPECTED_PRE.items():
        group = rows[rows.split == split]
        observed = (len(group), int(group.y.sum()))
        if observed != expected:
            raise ValueError(f"Pre-period split {split} support {observed} != {expected}")
    if (rows.y < 0).any() or (rows.t_us >= POST_START).any():
        raise ValueError("Pre-period episodes contain unavailable or post-period labels")
    return rows


def _freeze_payload(trace: original.Trace, pre: pd.DataFrame, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "swissai-idle-return-post-freeze/v1",
        "research_scope": "observed_request_return_only",
        "source_hashes": trace.source_hashes,
        "original_model_sha256": sha256(MODEL),
        "original_model_spec_sha256": sha256(MODEL_SPEC),
        "original_evaluator_sha256": sha256(Path(original.__file__)),
        "post_evaluator_sha256": sha256(Path(__file__)),
        "protocol_sha256": PROTOCOL_SHA256,
        "scikit_learn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "selected_original_model": spec["selected_model"],
        "model_features": list(original.FEATURES),
        "kappa": KAPPA,
        "prior_numerator": PRIOR_NUMERATOR,
        "prior_denominator": PRIOR_DENOMINATOR,
        "prior_probability": PRIOR,
        "post_start_utc": "2025-08-16T00:00:00Z",
        "post_end_utc": "2025-10-16T00:00:00Z",
        "pre_support": {
            str(split): {"episodes": len(pre[pre.split == split]),
                         "positive": int(pre.loc[pre.split == split, "y"].sum())}
            for split in EXPECTED_PRE
        },
        "post_support_gate": {"episodes": EXPECTED_POST[0], "positive": EXPECTED_POST[1]},
        "supported_week_indices": list(SUPPORTED_WEEKS),
        "week_support_gate": [list(pair) for pair in EXPECTED_WEEKS],
    }


def freeze(*, manifest_path: Path = OUTPUT / "freeze_manifest.json") -> str:
    """Pin inputs and old episodes without constructing post episodes or predictions."""
    _refuse_existing(manifest_path)
    spec = _check_fixed_inputs()
    trace = original.load_trace(verify_raw=True)
    if trace.source_hashes != spec["source_hashes"]:
        raise ValueError("Original trace hashes differ")
    pre = _pre_episodes(trace)
    payload = _freeze_payload(trace, pre, spec)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(_json_bytes(payload))
    return sha256(manifest_path)


def build_post_episodes(trace: original.Trace) -> pd.DataFrame:
    """Reuse original episode semantics on only the new, fixed UTC period."""
    global_us = np.asarray(trace.global_us, dtype=np.int64)
    if len(global_us) < 2 or np.any(np.diff(global_us) < 0):
        raise ValueError("Global request timestamps are not sorted")
    gap_ends = global_us[1:][np.diff(global_us) >= original.GAP_US]
    pieces: list[pd.DataFrame] = []
    for alias in sorted(trace.model_requests):
        own = np.asarray(trace.model_requests[alias], dtype=np.int64)
        if not len(own):
            continue
        if np.any(np.diff(own) < 0):
            raise ValueError("Model request timestamps are not sorted")
        distinct = np.unique(own)
        t_all = distinct + original.IDLE_US
        next_us = np.r_[distinct[1:], np.iinfo(np.int64).max]
        valid = ((t_all >= POST_START) & (t_all + original.TARGET_US <= POST_END)
                 & (t_all + original.TARGET_US <= global_us[-1]) & (next_us > t_all))
        positions = np.flatnonzero(valid)
        t = t_all[valid]
        if not len(t):
            continue
        global_recent = original._counts_before(global_us, t, original.TARGET_US) > 0
        if len(gap_ends):
            last_gap = np.searchsorted(gap_ends, t, side="right") - 1
            cooldown = ((last_gap >= 0)
                        & (t < gap_ends[np.maximum(last_gap, 0)] + original.COOLDOWN_US))
        else:
            cooldown = np.zeros(len(t), dtype=bool)
        eligible = global_recent & ~cooldown
        t = t[eligible]
        positions = positions[eligible]
        if not len(t):
            continue
        y = (next_us[positions] < t + original.TARGET_US).astype(np.int8)
        gap_mean, gap_p90, gap_count = original._gap_features(distinct, positions)
        day_phase = 2 * np.pi * ((t % original.DAY_US) / original.DAY_US)
        weekday = ((t // original.DAY_US + 3) % 7).astype(np.float64)
        week_phase = 2 * np.pi * (weekday + ((t % original.DAY_US) / original.DAY_US)) / 7
        future_global = original._counts_before(
            global_us, t + original.TARGET_US, original.TARGET_US) > 0
        data: dict[str, Any] = {
            "model_alias": np.repeat(alias, len(t)), "t_us": t,
            "split": np.full(len(t), 3, dtype=np.int8), "y": y,
            "future_global_observed": future_global,
            "gap20_mean_log": gap_mean, "gap20_p90_log": gap_p90,
            "gap20_count": gap_count,
            "age_min_log": np.log1p((t - distinct[0]).astype(np.float64) / original.MIN_US),
            "sin_day": np.sin(day_phase), "cos_day": np.cos(day_phase),
            "sin_week": np.sin(week_phase), "cos_week": np.cos(week_phase),
            "first_seen_us": np.repeat(distinct[0], len(t)),
        }
        for width in (10, 30, 120):
            own_count = original._counts_before(own, t, width * original.MIN_US)
            global_count = original._counts_before(global_us, t, width * original.MIN_US)
            data[f"own{width}"] = own_count
            data[f"global{width}"] = global_count
            data[f"own{width}_log"] = np.log1p(own_count)
            data[f"global{width}_log"] = np.log1p(global_count)
        pieces.append(pd.DataFrame(data))
    if not pieces:
        raise ValueError("No post-period eligible episodes")
    post = pd.concat(pieces, ignore_index=True)
    post = post.sort_values(["model_alias", "t_us"], kind="stable").reset_index(drop=True)
    if post.duplicated(["model_alias", "t_us"]).any():
        raise ValueError("Duplicated model decision timestamp")
    return post


def _check_post_support(post: pd.DataFrame) -> None:
    if (len(post), int(post.y.sum())) != EXPECTED_POST:
        raise ValueError("Post support differs from pre-score audit")
    if not ((post.t_us >= POST_START) &
            (post.t_us + original.TARGET_US <= POST_END)).all():
        raise ValueError("Post decision exceeds fixed time boundaries")
    weeks = ((post.t_us.to_numpy(dtype=np.int64) - POST_START) // (7 * original.DAY_US))
    for week, expected in enumerate(EXPECTED_WEEKS):
        ix = weeks == week
        if (int(ix.sum()), int(post.y.to_numpy()[ix].sum())) != expected:
            raise ValueError(f"Post week {week} support differs from pre-score audit")
    if post.model_alias.nunique() != EXPECTED_POST_MODELS:
        raise ValueError("Post model count differs from pre-score audit")
    new = post.first_seen_us >= POST_START
    if (post.loc[new, "model_alias"].nunique() != EXPECTED_NEW_MODELS or
            (int(new.sum()), int(post.loc[new, "y"].sum())) != EXPECTED_NEW_EPISODES):
        raise ValueError("New-model support differs from pre-score audit")
    if int((~post.future_global_observed).sum()) != EXPECTED_UNKNOWN_GLOBAL:
        raise ValueError("Future-global-observation diagnostic count differs")
    model_counts = post.groupby("model_alias").y.agg(["size", "sum"])
    selected = model_counts[model_counts["size"] >= 10]
    if (len(selected), int(selected["size"].sum()), int(selected["sum"].sum())) != EXPECTED_BALANCED_MODELS:
        raise ValueError("Model-balanced support differs from pre-score audit")
    if ((selected["sum"] == 0) | (selected["sum"] == selected["size"])).any():
        raise ValueError("A model-balanced cohort has only one observed class")


def fixed_blend(p_logistic: np.ndarray, n: np.ndarray, a: np.ndarray,
                *, kappa: int = KAPPA, prior: float = PRIOR) -> tuple[np.ndarray, np.ndarray]:
    """Local shrinkage of a fixed model and the preselected empirical baseline."""
    p = np.asarray(p_logistic, dtype=np.float64)
    n = np.asarray(n, dtype=np.int64)
    a = np.asarray(a, dtype=np.int64)
    if p.ndim != 1 or n.shape != p.shape or a.shape != p.shape:
        raise ValueError("Probabilities and completed-history arrays must align")
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Invalid fixed-model probabilities")
    if (n < 0).any() or (a < 0).any() or (a > n).any() or kappa <= 0:
        raise ValueError("Invalid completed-history counts")
    if not np.isfinite(prior) or not 0 <= prior <= 1:
        raise ValueError("Invalid empirical prior")
    denominator = n + kappa
    blend = (a + kappa * p) / denominator
    empirical = (a + kappa * prior) / denominator
    return blend, empirical


def _fixed_model_probabilities(rows: pd.DataFrame) -> np.ndarray:
    fitted = joblib.load(MODEL)
    if fitted.get("selected") != "logistic":
        raise ValueError("Stored selected model is not logistic")
    classes = fitted["model"].classes_
    if len(classes) != 2 or list(classes) != [0, 1]:
        raise ValueError("Stored model class order differs")
    x = rows.loc[:, original.FEATURES].to_numpy(dtype=np.float64)
    p = fitted["model"].predict_proba(fitted["scaler"].transform(x))[:, 1]
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Stored model emitted invalid probabilities")
    return p


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return original._metrics(y, p)


def _summarize(post: pd.DataFrame, p_logistic: np.ndarray,
               p_empirical: np.ndarray, p_blend: np.ndarray,
               n: np.ndarray, a: np.ndarray) -> tuple[dict[str, Any], pd.DataFrame]:
    y = post.y.to_numpy(dtype=np.int8)
    predictions = {"logistic": p_logistic, "empirical": p_empirical, "blend": p_blend}
    overall = {name: _metrics(y, p) for name, p in predictions.items()}
    weeks = ((post.t_us.to_numpy(dtype=np.int64) - POST_START) // (7 * original.DAY_US)).astype(int)
    by_week: dict[str, Any] = {}
    for week in range(len(EXPECTED_WEEKS)):
        ix = weeks == week
        by_week[str(week)] = {
            "episodes": int(ix.sum()), "positive": int(y[ix].sum()),
            "sufficient_support": week in SUPPORTED_WEEKS,
            "metrics": {name: _metrics(y[ix], p[ix]) for name, p in predictions.items()},
        }
    model_rows = []
    for alias, group in post.groupby("model_alias", sort=True):
        ix = group.index.to_numpy()
        model_rows.append({
            "model_alias": alias, "episodes": len(ix), "positive": int(y[ix].sum()),
            "first_seen_after_post_start": bool(group.first_seen_us.iloc[0] >= POST_START),
            "brier": {name: float(np.mean((y[ix] - p[ix]) ** 2)) for name, p in predictions.items()},
        })
    balanced = [row for row in model_rows if row["episodes"] >= 10]
    balanced_brier = {name: float(np.mean([row["brier"][name] for row in balanced]))
                      for name in predictions}
    by_cohort: dict[str, Any] = {}
    for name, ix in (("new_model", post.first_seen_us.to_numpy() >= POST_START),
                     ("existing_model", post.first_seen_us.to_numpy() < POST_START),
                     ("no_global_request_in_target_window", ~post.future_global_observed.to_numpy()),
                     ("global_request_in_target_window", post.future_global_observed.to_numpy())):
        by_cohort[name] = {
            "episodes": int(ix.sum()), "positive": int(y[ix].sum()),
            "metrics": {method: _metrics(y[ix], p[ix]) for method, p in predictions.items()}
        }
    relative = {name: float((overall[name]["brier"] - overall["blend"]["brier"])
                            / overall[name]["brier"])
                for name in ("logistic", "empirical")}
    supported_wins = sum(
        all(by_week[str(week)]["metrics"]["blend"]["brier"]
            < by_week[str(week)]["metrics"][base]["brier"]
            for base in ("logistic", "empirical"))
        for week in SUPPORTED_WEEKS
    )
    accept = (all(value >= .05 for value in relative.values()) and supported_wins >= 4
              and all(balanced_brier["blend"] <= balanced_brier[base]
                      for base in ("logistic", "empirical")))
    output = {
        "scope": "observed_request_return_risk_only",
        "post_episodes": len(post), "post_positive": int(y.sum()),
        "post_models": post.model_alias.nunique(),
        "overall": overall,
        "relative_brier_gain_vs_baseline": relative,
        "by_week": by_week,
        "supported_week_wins_against_both": supported_wins,
        "model_balanced": {"minimum_episodes_per_model": 10,
                           "models": len(balanced), "episodes": sum(row["episodes"] for row in balanced),
                           "positive": sum(row["positive"] for row in balanced),
                           "equal_model_brier": balanced_brier},
        "by_cohort": by_cohort,
        "by_model": model_rows,
        "exploratory_acceptance": bool(accept),
        "acceptance_rule": "both baseline relative Brier gains >=5%; at least 4/5 fixed supported weeks beat both; equal-model Brier no worse than both",
    }
    table = pd.DataFrame({
        "model_alias": post.model_alias.to_numpy(), "t_us": post.t_us.to_numpy(),
        "y": y, "week": weeks, "completed_history_n": n,
        "completed_history_positive": a,
        "first_seen_after_post_start": post.first_seen_us.to_numpy() >= POST_START,
        "future_global_observed_diagnostic_only": post.future_global_observed.to_numpy(),
        "p_logistic": p_logistic, "p_empirical": p_empirical, "p_blend": p_blend,
    })
    return output, table


def score(*, manifest_path: Path = OUTPUT / "freeze_manifest.json",
          expected_manifest_sha256: str, output_dir: Path = OUTPUT) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise ValueError("An exact 64-hex freeze manifest SHA-256 is required")
    metrics_path = output_dir / "post_metrics.json"
    predictions_path = output_dir / "post_predictions.parquet"
    _refuse_existing(metrics_path, predictions_path)
    if sha256(manifest_path) != expected_manifest_sha256:
        raise ValueError("Freeze manifest SHA-256 differs")
    frozen = json.loads(manifest_path.read_text())
    spec = _check_fixed_inputs()
    trace = original.load_trace(verify_raw=True)
    if trace.source_hashes != spec["source_hashes"]:
        raise ValueError("Original trace hashes differ")
    pre = _pre_episodes(trace)
    if frozen != _freeze_payload(trace, pre, spec):
        raise ValueError("Freeze manifest fields differ from current source and code")
    post = build_post_episodes(trace)
    _check_post_support(post)  # Every support gate runs before model scoring.
    rows = pd.concat([pre, post], ignore_index=True)
    rows = rows.sort_values(["model_alias", "t_us"], kind="stable").reset_index(drop=True)
    if rows.duplicated(["model_alias", "t_us"]).any():
        raise ValueError("Duplicated decision across time splits")
    n, a = original._history(rows)  # Strictly only t_j + 10 minutes < t.
    p_logistic_all = _fixed_model_probabilities(rows)
    p_blend_all, p_empirical_all = fixed_blend(p_logistic_all, n, a)
    ix = rows.split.to_numpy() == 3
    post_rows = rows.loc[ix].reset_index(drop=True)
    result, predictions = _summarize(
        post_rows, p_logistic_all[ix], p_empirical_all[ix], p_blend_all[ix], n[ix], a[ix])
    result["freeze_manifest_sha256"] = expected_manifest_sha256
    result["protocol_sha256"] = PROTOCOL_SHA256
    result["post_evaluator_sha256"] = sha256(Path(__file__))
    result["source_hashes"] = trace.source_hashes
    result["original_model_sha256"] = sha256(MODEL)
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(predictions_path, index=False)
    metrics_path.write_bytes(_json_bytes(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "score"))
    parser.add_argument("--manifest", type=Path, default=OUTPUT / "freeze_manifest.json")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--expected-manifest-sha256")
    args = parser.parse_args()
    if args.phase == "freeze":
        if args.expected_manifest_sha256:
            parser.error("--expected-manifest-sha256 is only for score")
        print(json.dumps({"freeze_manifest_sha256": freeze(manifest_path=args.manifest)}))
    else:
        if not args.expected_manifest_sha256:
            parser.error("score requires --expected-manifest-sha256")
        result = score(manifest_path=args.manifest,
                       expected_manifest_sha256=args.expected_manifest_sha256,
                       output_dir=args.output_dir)
        print(json.dumps({"post_episodes": result["post_episodes"],
                          "exploratory_acceptance": result["exploratory_acceptance"]}))


if __name__ == "__main__":
    main()
