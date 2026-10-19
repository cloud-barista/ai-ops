#!/usr/bin/env python3
"""One-shot same-service temporal sensitivity for GenTD26 FAILED status.

The beta1 baseline was chosen after validation was inspected. Test status
distribution was partially viewed before this protocol. This is neither a
blind/independent evaluation nor an online or operational action policy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
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


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/gentd26/lora_request_trace.csv"
PROTOCOL = RESEARCH / "reports/gentd26_request_failure_temporal_protocol.md"
SCRIPT = Path(__file__).resolve()
TESTS = RESEARCH / "tests/test_gentd26_request_failure_temporal.py"
ORIGINAL_SCRIPT = RESEARCH / "scripts/evaluate_gentd26_request_failure.py"
ORIGINAL_TESTS = RESEARCH / "tests/test_gentd26_request_failure.py"
ORIGINAL_PROTOCOL = RESEARCH / "reports/gentd26_request_failure_protocol.md"
ORIGINAL_FREEZE = RESEARCH / "artifacts/gentd26_request_failure_v1/freeze_manifest.json"
SELECTION = RESEARCH / "artifacts/gentd26_request_failure_v1/selection"
MODEL = SELECTION / "model.joblib"
SELECTION_METRICS = SELECTION / "selection_metrics.json"
VALIDATION_PREDICTIONS = SELECTION / "validation_predictions.csv"
AUDIT_SCRIPT = RESEARCH / "scripts/audit_gentd26_request_failure_selection.py"
AUDIT_JSON = RESEARCH / "artifacts/gentd26_request_failure_v1/posthoc_selection_audit.json"
DEFAULT_OUTPUT = RESEARCH / "artifacts/gentd26_request_failure_temporal_v1"

FIXED_FILES = {
    "source": SOURCE,
    "original_protocol": ORIGINAL_PROTOCOL,
    "original_script": ORIGINAL_SCRIPT,
    "original_tests": ORIGINAL_TESTS,
    "original_freeze": ORIGINAL_FREEZE,
    "selection_model": MODEL,
    "selection_metrics": SELECTION_METRICS,
    "validation_predictions": VALIDATION_PREDICTIONS,
    "posthoc_audit_script": AUDIT_SCRIPT,
    "posthoc_audit_json": AUDIT_JSON,
}
EXPECTED_SHA256 = {
    "source": "263fb88f1095f62d360cf89ded8cfc24fa2c5d111c1b16e7aaa9ccbfc4b37d78",
    "original_protocol": "d449343c4ef45190dd5b86cd3e5644e6193176180a21dfe2480a4107964ed729",
    "original_script": "b3edcc0ea9e4552196fa2271897fbd34e9aff1ca8ae454224924a2b52cfd55ab",
    "original_tests": "885a8fe47483eb5addc2435a6d4df166bfe1230efc1347f103710733680d8d6f",
    "original_freeze": "0ed06c00ed938acaa126076b555960d31a2abee3011042bfc43cc35665d0acd7",
    "selection_model": "36d1cdc178666a5e463fbc7252c1fd0c890bd9e76c8682a49affd9e5d8163034",
    "selection_metrics": "f4a999bfc25734d4ba05d01edde92596c04a343f03214b9c5e248d07addb19ef",
    "validation_predictions": "ea8d53f99b64c745bb02f2d68e28011adbb9585e5fd3cd177290c207efe06ecb",
    "posthoc_audit_script": "9f8566c67ac5f6bca05f1a7f13ed2952229ffee00a436f14cc4705bb81aaefa8",
    "posthoc_audit_json": "3ee60ebb3c731d251dfa3719af9ad3283b2284e7eefebe6c56a985bdc24cd5c5",
}
EXPECTED_VERSIONS = {
    "numpy": "2.0.2", "pandas": "2.3.0+4.g1dfc98e16a",
    "sklearn": "1.6.1", "joblib": "1.5.1",
}
TRAIN_END = datetime.fromisoformat("2024-11-29 00:00:00")
TEST_START = datetime.fromisoformat("2024-12-04 00:00:00")
TEST_END = datetime.fromisoformat("2024-12-09 00:00:00")
TEST_DAYS = tuple(f"2024-12-{day:02d}" for day in range(4, 9))
TRAIN_ALL_FAILED_MODELS = ("M0013", "M0055", "M0061")
EPS = 1e-6
METHODS = ("logistic", "model_type", "beta1_model")
FEATURE_COLUMNS = (
    "predict_type", "checkpoint_model_version_id", "prompt_length",
    "negative_prompt_length", "negative_prompt_missing", "num_images_per_prompt",
    "num_inference_steps", "num_lora",
)
NUMERIC_INPUTS = (
    "prompt_length", "negative_prompt_length", "num_images_per_prompt",
    "num_inference_steps", "num_lora",
)
SOURCE_REQUIRED = (
    "gmt_create", "predict_status", "groupId", "predict_type",
    "checkpoint_model_version_id", *NUMERIC_INPUTS,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def versions() -> dict[str, str]:
    return {
        "numpy": np.__version__, "pandas": pd.__version__,
        "sklearn": sklearn.__version__, "joblib": joblib.__version__,
    }


def verify_fixed_inputs() -> None:
    for name, path in FIXED_FILES.items():
        actual = sha256(path)
        if actual != EXPECTED_SHA256[name]:
            raise ValueError(f"{name} SHA-256 changed: {actual}")
    if versions() != EXPECTED_VERSIONS:
        raise ValueError(f"library versions changed: {versions()}")
    original_freeze = json.loads(ORIGINAL_FREEZE.read_text())
    if (original_freeze["versions"] != EXPECTED_VERSIONS
            or original_freeze["test_scoring_available"] is not False):
        raise ValueError("original freeze environment or test restriction changed")
    selected = json.loads(SELECTION_METRICS.read_text())
    if (selected["selected_baseline"] != "model_type"
            or selected["test_score_opened"] is not False
            or selected["support"]["train"]["requests"] != 13_208
            or selected["support"]["train"]["failed"] != 231):
        raise ValueError("original selection or train support changed")
    audit = json.loads(AUDIT_JSON.read_text())
    if (audit["test_score_opened"] is not False
            or tuple(audit["all_failed_model_exclusion"]["model_ids"]) != TRAIN_ALL_FAILED_MODELS):
        raise ValueError("post-hoc audit or all-failed model set changed")


def manifest_payload() -> dict:
    """Hash bytes only; never parse test status or compute test predictions."""
    verify_fixed_inputs()
    return {
        "study": "retrospective_same_service_temporal_sensitivity_not_blind",
        "source_sha256": EXPECTED_SHA256["source"],
        "fixed_inputs_sha256": EXPECTED_SHA256,
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(SCRIPT),
        "tests_sha256": sha256(TESTS),
        "versions": EXPECTED_VERSIONS,
        "train_end_exclusive": TRAIN_END.isoformat(),
        "test_start_inclusive": TEST_START.isoformat(),
        "test_end_exclusive": TEST_END.isoformat(),
        "no_refit": True,
        "test_score_opened_at_freeze": False,
    }


def freeze(output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    payload = manifest_payload()
    output.mkdir(parents=True, exist_ok=False)
    with (output / "freeze_manifest.json").open("x") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"stage": "freeze", "manifest": str(output / "freeze_manifest.json")}, sort_keys=True))


def verify_manifest(output: Path) -> dict:
    manifest = output / "freeze_manifest.json"
    frozen = json.loads(manifest.read_text())
    if frozen != manifest_payload():
        raise ValueError("source, frozen artifacts, protocol, script, tests, or environment changed")
    return frozen


def load_original_module():
    """Call only after all original hashes and library versions are verified."""
    spec = importlib.util.spec_from_file_location("frozen_gentd26_request_failure", ORIGINAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import original frozen evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _required_nonnegative(value: str, name: str, source_row: int, *, allow_missing: bool = False) -> float:
    if value == "" and allow_missing:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name} at source row {source_row}: {value!r}") from exc
    if not np.isfinite(parsed) or parsed < 0:
        raise ValueError(f"invalid {name} at source row {source_row}: {value!r}")
    return parsed


def load_test(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return distinct feature and outcome frames; never copy outcome into features."""
    feature_rows: list[dict] = []
    label_rows: list[dict] = []
    excluded = {"PENDING": 0, "PROCESSING": 0}
    previous: datetime | None = None
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        missing = set(SOURCE_REQUIRED) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"missing source columns: {sorted(missing)}")
        for source_row, row in enumerate(reader, start=2):
            try:
                created = datetime.fromisoformat(row["gmt_create"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid gmt_create at source row {source_row}") from exc
            if previous is not None and created < previous:
                raise ValueError("source request timestamps are not monotone")
            previous = created
            if created < TEST_START:
                continue  # Skip train and validation outcomes entirely.
            if created >= TEST_END:
                raise ValueError(f"request outside frozen test dates at source row {source_row}")
            status = row["predict_status"]
            if status in excluded:
                excluded[status] += 1
                continue
            if status not in ("SUCCEED", "FAILED"):
                raise ValueError(f"unexpected test status at source row {source_row}")
            for name in ("predict_type", "checkpoint_model_version_id", "groupId"):
                if not row[name]:
                    raise ValueError(f"missing {name} at source row {source_row}")
            numeric = {
                name: _required_nonnegative(
                    row[name], name, source_row, allow_missing=name == "negative_prompt_length"
                )
                for name in NUMERIC_INPUTS
            }
            feature_rows.append({
                "predict_type": row["predict_type"],
                "checkpoint_model_version_id": row["checkpoint_model_version_id"],
                "prompt_length": numeric["prompt_length"],
                "negative_prompt_length": numeric["negative_prompt_length"],
                "negative_prompt_missing": int(row["negative_prompt_length"] == ""),
                "num_images_per_prompt": numeric["num_images_per_prompt"],
                "num_inference_steps": numeric["num_inference_steps"],
                "num_lora": numeric["num_lora"],
            })
            label_rows.append({
                "source_row": source_row,
                "day": created.date().isoformat(),
                "target": int(status == "FAILED"),
                "groupId": row["groupId"],
            })
    features = pd.DataFrame(feature_rows, columns=FEATURE_COLUMNS)
    labels = pd.DataFrame(label_rows, columns=("source_row", "day", "target", "groupId"))
    if features.empty or len(features) != len(labels):
        raise ValueError("empty or misaligned finalized test requests")
    return features, labels, excluded


def train_all_failed_models(train: pd.DataFrame) -> tuple[str, ...]:
    aggregate = train.groupby("checkpoint_model_version_id").target.agg(["sum", "count"])
    ids = tuple(sorted(aggregate.index[aggregate["count"].ge(10) & aggregate["sum"].eq(aggregate["count"])]))
    if ids != TRAIN_ALL_FAILED_MODELS:
        raise ValueError(f"train-only all-failed model IDs differ from frozen audit: {ids}")
    return ids


def support_gate(features: pd.DataFrame, labels: pd.DataFrame, all_failed_models: tuple[str, ...]) -> dict:
    if tuple(features.columns) != FEATURE_COLUMNS or len(features) != len(labels):
        raise ValueError("test feature/label frame mismatch")
    if tuple(sorted(labels.day.unique())) != TEST_DAYS:
        raise ValueError("test requests must cover exactly five frozen dates")
    failed = labels.loc[labels.target.eq(1)]
    groups = failed.groupId.value_counts()
    remainder = ~features.checkpoint_model_version_id.isin(all_failed_models).to_numpy(bool)
    remainder_failed = int(labels.target.to_numpy(int)[remainder].sum())
    result = {
        "requests": int(len(labels)),
        "failed": int(len(failed)),
        "failed_days": int(failed.day.nunique()),
        "failed_groups": int(groups.size),
        "largest_group_failed": int(groups.max()) if len(groups) else 0,
        "largest_group_failed_share": float(groups.max() / len(failed)) if len(failed) else 0.0,
        "failed_after_all_failed_model_exclusion": remainder_failed,
        "excluded_train_all_failed_models": list(all_failed_models),
        "day_counts": {
            day: {"requests": int(len(part)), "failed": int(part.target.sum())}
            for day, part in labels.groupby("day", sort=True)
        },
    }
    checks = {
        "requests_at_least_5000": result["requests"] >= 5_000,
        "failed_at_least_50": result["failed"] >= 50,
        "failed_days_at_least_3": result["failed_days"] >= 3,
        "failed_groups_at_least_15": result["failed_groups"] >= 15,
        "largest_group_share_at_most_one_third": result["largest_group_failed_share"] <= 1 / 3,
        "remainder_failed_at_least_25": remainder_failed >= 25,
    }
    result["checks"] = {key: bool(value) for key, value in checks.items()}
    result["supported"] = bool(all(checks.values()))
    if not result["supported"]:
        raise ValueError(f"test support gate failed before prediction: {result}")
    return result


def beta1_model_probabilities(train: pd.DataFrame, features: pd.DataFrame) -> np.ndarray:
    global_rate = (float(train.target.sum()) + 1.0) / (len(train) + 2.0)
    by_model = train.groupby("checkpoint_model_version_id").target.agg(["sum", "count"])
    model_rate = (by_model["sum"] + global_rate) / (by_model["count"] + 1.0)
    probabilities = features.checkpoint_model_version_id.map(model_rate).fillna(global_rate).to_numpy(float)
    if not np.isfinite(probabilities).all():
        raise ValueError("non-finite beta1 probability")
    return np.clip(probabilities, EPS, 1.0 - EPS)


def predict_three(train: pd.DataFrame, features: pd.DataFrame, model, original_module) -> dict[str, np.ndarray]:
    if tuple(features.columns) != FEATURE_COLUMNS:
        raise ValueError("model input must contain only the frozen request feature columns")
    transformed = original_module.feature_frame(features)
    if any(name in transformed for name in ("target", "predict_status", "exec_time_seconds", "groupId", "day")):
        raise ValueError("outcome or diagnostic column entered feature engineering")
    candidate = original_module.clipped(model.predict_proba(transformed)[:, 1])
    model_type = original_module.baseline_predictions(train, features)["model_type"]
    beta1 = beta1_model_probabilities(train, features)
    result = {"logistic": candidate, "model_type": model_type, "beta1_model": beta1}
    if any(len(values) != len(features) for values in result.values()):
        raise ValueError("prediction length mismatch")
    return result


def _cohort_scores(original_module, labels: pd.DataFrame, predictions: dict[str, np.ndarray], mask: np.ndarray) -> dict:
    mask = np.asarray(mask, dtype=bool)
    if len(mask) != len(labels):
        raise ValueError("cohort mask length mismatch")
    cohort = labels.iloc[np.flatnonzero(mask)]
    return {
        "requests": int(len(cohort)),
        "failed": int(cohort.target.sum()),
        "groups": int(cohort.groupId.nunique()),
        "scores": {
            name: original_module.score(cohort, values[mask]) if len(cohort) else None
            for name, values in predictions.items()
        },
    }


def evaluate(train: pd.DataFrame, features: pd.DataFrame, labels: pd.DataFrame,
             predictions: dict[str, np.ndarray], original_module,
             all_failed_models: tuple[str, ...]) -> dict:
    every = np.ones(len(labels), dtype=bool)
    known_model = features.checkpoint_model_version_id.isin(set(train.checkpoint_model_version_id)).to_numpy(bool)
    known_group = labels.groupId.isin(set(train.groupId)).to_numpy(bool)
    all_failed = features.checkpoint_model_version_id.isin(all_failed_models).to_numpy(bool)
    cohorts = {
        "overall": every,
        "seen_model": known_model,
        "new_model": ~known_model,
        "seen_group": known_group,
        "new_group": ~known_group,
        "train_all_failed_models": all_failed,
        "excluding_train_all_failed_models": ~all_failed,
    }
    scored = {
        name: _cohort_scores(original_module, labels, predictions, mask)
        for name, mask in cohorts.items()
    }
    primary = scored["overall"]["scores"]
    beta = primary["beta1_model"]
    comparisons = {}
    for reference_name in ("logistic", "model_type"):
        reference = primary[reference_name]
        reference_brier = reference["day_equal_brier"]
        beta_brier = beta["day_equal_brier"]
        comparisons[f"beta1_model_vs_{reference_name}"] = {
            "beta1_minus_reference_day_equal_brier": float(beta_brier - reference_brier),
            "relative_day_equal_brier_reduction": (
                float((reference_brier - beta_brier) / reference_brier) if reference_brier > 0 else None
            ),
            "days_with_strictly_lower_beta1_brier": int(sum(
                beta["days"][day]["brier"] < reference["days"][day]["brier"]
                for day in TEST_DAYS
            )),
        }
    return {"cohorts": scored, "comparisons": comparisons}


def score(output: Path) -> None:
    destination = output / "score"
    if destination.exists():
        raise FileExistsError(destination)
    verify_manifest(output)
    original = load_original_module()
    train = original.load_pretest(SOURCE, valid_end=TRAIN_END)
    original_train = json.loads(SELECTION_METRICS.read_text())["support"]["train"]
    if len(train) != original_train["requests"] or int(train.target.sum()) != original_train["failed"]:
        raise ValueError("train cohort differs from frozen selection")
    all_failed_models = train_all_failed_models(train)
    features, labels, excluded = load_test(SOURCE)
    support = support_gate(features, labels, all_failed_models)
    # No unpickling, model prediction, or result scoring happens before the support gate.
    model = joblib.load(MODEL)
    predictions = predict_three(train, features, model, original)
    evaluated = evaluate(train, features, labels, predictions, original, all_failed_models)
    metrics = {
        "study": "retrospective_same_service_temporal_sensitivity_not_blind",
        "source_sha256": EXPECTED_SHA256["source"],
        "freeze_manifest_sha256": sha256(output / "freeze_manifest.json"),
        "train_requests": int(len(train)),
        "train_failed": int(train.target.sum()),
        "support": support,
        "excluded_unfinalized_test_status": excluded,
        "methods": list(METHODS),
        "primary_metric": "equal mean of five daily Brier scores",
        "test_score_opened": True,
        "no_refit": True,
        **evaluated,
    }
    rows = labels.loc[:, ["source_row", "day", "target", "groupId"]].copy()
    rows["checkpoint_model_version_id"] = features.checkpoint_model_version_id.to_numpy()
    for name in METHODS:
        rows[f"p_{name}"] = predictions[name]
    staging = Path(tempfile.mkdtemp(prefix="temporal_score_tmp_", dir=output))
    try:
        rows.to_csv(staging / "test_predictions.csv", index=False)
        with (staging / "test_metrics.json").open("x") as stream:
            json.dump(metrics, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps({
        "stage": "score", "output": str(destination), "support_passed": True,
        "primary": {
            name: evaluated["cohorts"]["overall"]["scores"][name]["day_equal_brier"]
            for name in METHODS
        },
    }, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "score"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.stage == "freeze":
        freeze(args.output)
    else:
        score(args.output)


if __name__ == "__main__":
    main()
