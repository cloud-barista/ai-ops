#!/usr/bin/env python3
"""Validation-only, retrospective GenTD26 FAILED-status risk experiment.

This script deliberately has no held-out test scoring command. Final status
availability is unknown, so the result is not an online deployment policy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/gentd26/lora_request_trace.csv"
PROTOCOL = RESEARCH / "reports/gentd26_request_failure_protocol.md"
TESTS = RESEARCH / "tests/test_gentd26_request_failure.py"
DEFAULT_OUTPUT = RESEARCH / "artifacts/gentd26_request_failure_v1"
SOURCE_SHA256 = "263fb88f1095f62d360cf89ded8cfc24fa2c5d111c1b16e7aaa9ccbfc4b37d78"
TRAIN_END = datetime.fromisoformat("2024-11-29 00:00:00")
VALID_END = datetime.fromisoformat("2024-12-04 00:00:00")
VALIDATION_DAYS = (
    "2024-11-29", "2024-11-30", "2024-12-01", "2024-12-02", "2024-12-03",
)
BASELINE_ORDER = ("global", "type", "model", "model_type")
EPS = 1e-6
REQUIRED = (
    "gmt_create", "predict_status", "predict_type", "checkpoint_model_version_id",
    "groupId", "prompt_length", "negative_prompt_length", "num_images_per_prompt",
    "num_inference_steps", "num_lora",
)
NUMERIC_INPUTS = (
    "prompt_length", "negative_prompt_length", "num_images_per_prompt",
    "num_inference_steps", "num_lora",
)
FEATURE_NUMERIC = (
    "log_prompt_length", "log_negative_prompt_length", "negative_prompt_missing",
    "log_num_images", "log_num_steps", "log_num_lora", "log_image_steps",
)
FEATURE_CATEGORICAL = ("predict_type", "checkpoint_model_version_id")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clipped(probs: np.ndarray) -> np.ndarray:
    values = np.asarray(probs, dtype=float)
    if not np.all(np.isfinite(values)):
        raise ValueError("non-finite prediction")
    return np.clip(values, EPS, 1.0 - EPS)


def _required_nonnegative(value: str, name: str, *, allow_missing: bool = False) -> float:
    if value == "" and allow_missing:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc
    if not np.isfinite(parsed) or parsed < 0:
        raise ValueError(f"invalid {name}: {value!r}")
    return parsed


def load_pretest(path: Path, *, valid_end: datetime = VALID_END) -> pd.DataFrame:
    """Read train/validation only; stop before inspecting first test status."""
    rows: list[dict] = []
    excluded = {"PENDING": 0, "PROCESSING": 0}
    previous: datetime | None = None
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        missing = set(REQUIRED) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"missing source columns: {sorted(missing)}")
        for source_row, row in enumerate(reader, start=2):
            created = datetime.fromisoformat(row["gmt_create"])
            if previous is not None and created < previous:
                raise ValueError("source request timestamps are not monotone")
            previous = created
            if created >= valid_end:
                break
            status = row["predict_status"]
            if status in ("PENDING", "PROCESSING"):
                excluded[status] += 1
                continue
            if status not in ("SUCCEED", "FAILED"):
                raise ValueError(f"unexpected status at source row {source_row}")
            for name in ("predict_type", "checkpoint_model_version_id", "groupId"):
                if not row[name]:
                    raise ValueError(f"missing {name} at source row {source_row}")
            numeric = {
                name: _required_nonnegative(
                    row[name], name, allow_missing=name == "negative_prompt_length"
                )
                for name in NUMERIC_INPUTS
            }
            rows.append({
                "source_row": source_row,
                "created": created,
                "day": created.date().isoformat(),
                "target": int(status == "FAILED"),
                "predict_type": row["predict_type"],
                "checkpoint_model_version_id": row["checkpoint_model_version_id"],
                "groupId": row["groupId"],
                "negative_prompt_missing": int(row["negative_prompt_length"] == ""),
                **numeric,
            })
    if not rows:
        raise ValueError("no finalized pre-test requests")
    result = pd.DataFrame(rows)
    result.attrs["excluded_unfinalized_status"] = excluded
    return result


def split_rows(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = data.loc[data.created < TRAIN_END].copy()
    valid = data.loc[(data.created >= TRAIN_END) & (data.created < VALID_END)].copy()
    if train.empty or valid.empty:
        raise ValueError("empty train or validation split")
    return train, valid


def support(data: pd.DataFrame) -> dict:
    failed = data.loc[data.target.eq(1)]
    groups = failed.groupId.value_counts()
    return {
        "requests": int(len(data)),
        "failed": int(len(failed)),
        "failed_groups": int(groups.size),
        "failed_days": int(failed.day.nunique()),
        "largest_group_failed": int(groups.max()) if not groups.empty else 0,
        "largest_group_failed_share": float(groups.max() / len(failed)) if len(failed) else 0.0,
        "top_five_group_failed_counts": [int(value) for value in groups.head(5)],
        "day_counts": {
            day: {"requests": int(len(part)), "failed": int(part.target.sum())}
            for day, part in data.groupby("day", sort=True)
        },
    }


def check_support(train: pd.DataFrame, valid: pd.DataFrame) -> dict:
    tr, va = support(train), support(valid)
    if tr["requests"] < 10_000 or tr["failed"] < 200 or tr["failed_groups"] < 30:
        raise ValueError(f"train support gate failed: {tr}")
    if (va["requests"] < 5_000 or va["failed"] < 75 or va["failed_groups"] < 20
            or va["failed_days"] < 3 or va["largest_group_failed_share"] > 0.30):
        raise ValueError(f"validation support gate failed: {va}")
    if tuple(sorted(va["day_counts"])) != VALIDATION_DAYS:
        raise ValueError("validation must contain requests on exactly the five frozen dates")
    return {"train": tr, "validation": va}


def baseline_predictions(train: pd.DataFrame, valid: pd.DataFrame) -> dict[str, np.ndarray]:
    """Training-only hierarchical frequency priors, including unseen fallbacks."""
    global_rate = (float(train.target.sum()) + 1.0) / (len(train) + 2.0)
    by_type = train.groupby("predict_type").target.agg(["sum", "count"])
    by_model = train.groupby("checkpoint_model_version_id").target.agg(["sum", "count"])
    by_pair = train.groupby(["checkpoint_model_version_id", "predict_type"]).target.agg(["sum", "count"])
    estimates = {key: [] for key in BASELINE_ORDER}
    for model, kind in zip(valid.checkpoint_model_version_id, valid.predict_type):
        typ = by_type.loc[kind] if kind in by_type.index else None
        mod = by_model.loc[model] if model in by_model.index else None
        type_rate = ((float(typ["sum"]) + 100 * global_rate) / (float(typ["count"]) + 100)
                     if typ is not None else global_rate)
        model_rate = ((float(mod["sum"]) + 100 * global_rate) / (float(mod["count"]) + 100)
                      if mod is not None else global_rate)
        pair = by_pair.loc[(model, kind)] if (model, kind) in by_pair.index else None
        parent = 0.5 * (type_rate + model_rate)
        pair_rate = ((float(pair["sum"]) + 50 * parent) / (float(pair["count"]) + 50)
                     if pair is not None else parent)
        for name, value in (("global", global_rate), ("type", type_rate),
                            ("model", model_rate), ("model_type", pair_rate)):
            estimates[name].append(value)
    return {name: clipped(values) for name, values in estimates.items()}


def feature_frame(data: pd.DataFrame) -> pd.DataFrame:
    values = data.loc[:, list(FEATURE_CATEGORICAL)].copy()
    values["log_prompt_length"] = np.log1p(data.prompt_length.to_numpy(float))
    values["log_negative_prompt_length"] = np.log1p(data.negative_prompt_length.to_numpy(float))
    values["negative_prompt_missing"] = data.negative_prompt_missing.to_numpy(float)
    values["log_num_images"] = np.log1p(data.num_images_per_prompt.to_numpy(float))
    values["log_num_steps"] = np.log1p(data.num_inference_steps.to_numpy(float))
    values["log_num_lora"] = np.log1p(data.num_lora.to_numpy(float))
    values["log_image_steps"] = np.log1p(
        data.num_images_per_prompt.to_numpy(float) * data.num_inference_steps.to_numpy(float)
    )
    return values


def fit_candidate(train: pd.DataFrame) -> Pipeline:
    features = ColumnTransformer(
        transformers=[
            ("category", OneHotEncoder(handle_unknown="ignore"), list(FEATURE_CATEGORICAL)),
            ("number", StandardScaler(), list(FEATURE_NUMERIC)),
        ],
        sparse_threshold=1.0,
    )
    model = Pipeline([
        ("features", features),
        ("logistic", LogisticRegression(
            C=1.0, max_iter=2000, solver="lbfgs", class_weight=None,
            random_state=42,
        )),
    ])
    model.fit(feature_frame(train), train.target.to_numpy(int))
    return model


def score(data: pd.DataFrame, probs: np.ndarray) -> dict:
    y = data.target.to_numpy(int)
    p = clipped(probs)
    if len(y) != len(p):
        raise ValueError("prediction length mismatch")
    days = {}
    day_briers = []
    for day, indices in data.groupby("day", sort=True).indices.items():
        yd, pd_ = y[indices], p[indices]
        brier = float(brier_score_loss(yd, pd_))
        day_briers.append(brier)
        days[day] = {
            "n": int(len(yd)), "failed": int(yd.sum()), "brier": brier,
            "average_precision": float(average_precision_score(yd, pd_)) if yd.sum() else None,
        }
    group_briers = [float(np.mean((y[idx] - p[idx]) ** 2))
                    for idx in data.groupby("groupId", sort=False).indices.values()]
    return {
        "n": int(len(y)), "failed": int(y.sum()),
        "brier": float(brier_score_loss(y, p)),
        "day_equal_brier": float(np.mean(day_briers)),
        "group_equal_brier": float(np.mean(group_briers)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "average_precision": float(average_precision_score(y, p)) if y.sum() else None,
        "observed_failed_rate": float(np.mean(y)),
        "mean_predicted_rate": float(np.mean(p)),
        "days": days,
    }


def select_baseline(scores: dict[str, dict]) -> str:
    best = BASELINE_ORDER[0]
    for name in BASELINE_ORDER[1:]:
        if scores[name]["day_equal_brier"] < scores[best]["day_equal_brier"] - 1e-12:
            best = name
    return best


def adoption_gate(
    valid: pd.DataFrame, candidate: dict, baseline: dict,
    candidate_prob: np.ndarray, baseline_prob: np.ndarray,
    known_model: np.ndarray,
) -> dict:
    if (tuple(sorted(candidate["days"])) != VALIDATION_DAYS
            or tuple(sorted(baseline["days"])) != VALIDATION_DAYS):
        raise ValueError("adoption gate requires all five frozen validation dates")
    denom = baseline["day_equal_brier"]
    gain = ((denom - candidate["day_equal_brier"]) / denom) if denom > 0 else 0.0
    day_wins = sum(
        candidate["days"][day]["brier"] < baseline["days"][day]["brier"]
        for day in candidate["days"]
    )
    known = valid.loc[known_model]
    known_candidate = score(known, candidate_prob[known_model])["brier"] if len(known) else None
    known_baseline = score(known, baseline_prob[known_model])["brier"] if len(known) else None
    checks = {
        "day_equal_brier_gain_at_least_5pct": bool(gain >= 0.05),
        "wins_at_least_3_of_5_days": bool(day_wins >= 3),
        "average_precision_not_lower": bool(candidate["average_precision"] >= baseline["average_precision"]),
        "known_model_brier_not_higher": bool(
            known_candidate is not None and known_candidate <= known_baseline
        ),
    }
    return {
        "checks": checks, "exploratory_validation_go": bool(all(checks.values())),
        "relative_day_equal_brier_gain": float(gain), "day_wins": int(day_wins),
        "known_model_requests": int(len(known)),
        "known_model_candidate_brier": known_candidate,
        "known_model_baseline_brier": known_baseline,
    }


def group_familiarity_sensitivity(
    valid: pd.DataFrame, candidate_prob: np.ndarray, baseline_prob: np.ndarray,
    known_group: np.ndarray,
) -> dict:
    """Post-hoc diagnostic only; groupId is not a predictor or generalization unit."""
    if len(known_group) != len(valid):
        raise ValueError("group membership length mismatch")
    result = {}
    for name, mask in (("seen_group", known_group), ("new_group", ~known_group)):
        subset = valid.loc[mask]
        candidate_metrics = score(subset, candidate_prob[mask]) if len(subset) else None
        baseline_metrics = score(subset, baseline_prob[mask]) if len(subset) else None
        result[name] = {
            "requests": int(len(subset)),
            "failed": int(subset.target.sum()),
            "groups": int(subset.groupId.nunique()),
            "candidate_brier": candidate_metrics["brier"] if candidate_metrics else None,
            "baseline_brier": baseline_metrics["brier"] if baseline_metrics else None,
            "candidate_average_precision": (
                candidate_metrics["average_precision"] if candidate_metrics else None
            ),
            "baseline_average_precision": (
                baseline_metrics["average_precision"] if baseline_metrics else None
            ),
        }
    return result


def manifest_payload() -> dict:
    if sha256(SOURCE) != SOURCE_SHA256:
        raise ValueError("GenTD26 request CSV hash changed")
    return {
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "tests_sha256": sha256(TESTS),
        "versions": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__,
                     "joblib": joblib.__version__},
        "train_end_exclusive": TRAIN_END.isoformat(),
        "validation_end_exclusive": VALID_END.isoformat(),
        "test_scoring_available": False,
    }


def freeze(output: Path) -> None:
    payload = manifest_payload()
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "freeze_manifest.json"
    with manifest.open("x") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"stage": "freeze", "manifest": str(manifest)}, sort_keys=True))


def select(output: Path) -> None:
    manifest_path = output / "freeze_manifest.json"
    frozen = json.loads(manifest_path.read_text())
    if frozen != manifest_payload():
        raise ValueError("source, protocol, script, test, or library version differs from freeze")
    destination = output / "selection"
    if destination.exists():
        raise FileExistsError(destination)
    data = load_pretest(SOURCE)
    train, valid = split_rows(data)
    support_stats = check_support(train, valid)
    baselines = baseline_predictions(train, valid)
    model = fit_candidate(train)
    candidate_prob = clipped(model.predict_proba(feature_frame(valid))[:, 1])
    baseline_scores = {name: score(valid, probs) for name, probs in baselines.items()}
    selected = select_baseline(baseline_scores)
    candidate_scores = score(valid, candidate_prob)
    known_model = valid.checkpoint_model_version_id.isin(
        set(train.checkpoint_model_version_id)
    ).to_numpy(bool)
    gate = adoption_gate(valid, candidate_scores, baseline_scores[selected],
                         candidate_prob, baselines[selected], known_model)
    known_group = valid.groupId.isin(set(train.groupId)).to_numpy(bool)
    group_sensitivity = group_familiarity_sensitivity(
        valid, candidate_prob, baselines[selected], known_group
    )
    seen_stats = {
        "known_model": {"requests": int(known_model.sum()),
                        "failed": int(valid.target.to_numpy(int)[known_model].sum())},
        "new_model": {"requests": int((~known_model).sum()),
                      "failed": int(valid.target.to_numpy(int)[~known_model].sum())},
    }
    outcome = {
        "study": "GenTD26 retrospective FAILED status, validation only",
        "support": support_stats,
        "excluded_unfinalized_status": data.attrs["excluded_unfinalized_status"],
        "selected_baseline": selected,
        "baseline_scores": baseline_scores,
        "candidate_score": candidate_scores,
        "adoption_gate": gate,
        "model_familiarity": seen_stats,
        "group_familiarity_sensitivity": group_sensitivity,
        "source_sha256": frozen["source_sha256"],
        "freeze_manifest_sha256": sha256(manifest_path),
        "test_score_opened": False,
    }
    validation_rows = valid.loc[:, ["source_row", "day", "target"]].copy()
    for name in BASELINE_ORDER:
        validation_rows[f"p_{name}"] = baselines[name]
    validation_rows["p_candidate"] = candidate_prob
    staging = Path(tempfile.mkdtemp(prefix="selection_tmp_", dir=output))
    try:
        joblib.dump(model, staging / "model.joblib")
        validation_rows.to_csv(staging / "validation_predictions.csv", index=False)
        with (staging / "selection_metrics.json").open("x") as stream:
            json.dump(outcome, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps({"stage": "select", "selected_baseline": selected,
                      "exploratory_validation_go": gate["exploratory_validation_go"]}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "select"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.stage == "freeze":
        freeze(args.output)
    else:
        select(args.output)


if __name__ == "__main__":
    main()
