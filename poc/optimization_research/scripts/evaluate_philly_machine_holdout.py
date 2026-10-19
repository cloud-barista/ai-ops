"""Score the frozen Philly model on disjoint machines after a pinned dry-run.

All fitting and model/baseline selection replay only the original 5% machines'
train and validation periods. The new-machine source is opened for row-level
features and outcomes only after the original choices have been reproduced.
Existing frozen Philly source, model, and metric modules remain unchanged.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.special import expit

import evaluate_philly_gpu_risk as original
import fetch_philly_gpu_sample as source_module
import philly_risk_metrics as risk_metrics
import philly_risk_models as risk_models
import prepare_philly_machine_holdout as holdout_prepare
import prepare_philly_gpu_risk_candidates as builder


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/philly_machine_holdout_protocol_frozen.md"
PROTOCOL_DRAFT = ROOT / "reports/philly_machine_holdout_protocol_draft.md"
ORIGINAL_METRICS = ROOT / "artifacts/philly_gpu_risk_v1/metrics.json"
HOLDOUT_DIR = ROOT / "data/external/msr_philly_2017_holdout"
HOLDOUT_RAW = HOLDOUT_DIR / "gpu_util_machine_hash5to14pct.parquet"
HOLDOUT_RAW_AUDIT = HOLDOUT_DIR / "gpu_util_holdout_audit.json"
HOLDOUT_NORMALIZED = HOLDOUT_DIR / "gpu_util_normalized_first_width.parquet"
HOLDOUT_NORMALIZED_AUDIT = HOLDOUT_DIR / "gpu_util_normalized_first_width_audit.json"
PREPARED = ROOT / "artifacts/philly_machine_holdout_candidates_v1"
OUTPUT = ROOT / "artifacts/philly_machine_holdout_score_v1"
MODEL_SPEC_DIR = ROOT / "artifacts/philly_machine_holdout_model_v1"
MODEL_SPEC = MODEL_SPEC_DIR / "model_spec.json"
ORIGINAL_PREDICTIONS = ROOT / "artifacts/philly_gpu_risk_v1/test_predictions.parquet"
LOWER_RESIDUE = 5
UPPER_RESIDUE = 15
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STRATA = tuple((c, s) for c in builder.COHORTS for s in builder.STATES)
NEW_SCORE_COLUMNS = (*original.SCORE_COLUMNS, "pooled_quality", "selected_beta")


def file_sha256(path: Path) -> str:
    return builder.sha256_file(path)


def require_frozen_holdout_protocol(path: Path, expected_sha256: str) -> str:
    """Refuse any row-level holdout access until the reviewed protocol is frozen."""
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("Expected holdout protocol SHA256 must be lowercase hex")
    if file_sha256(path) != expected_sha256:
        raise ValueError("Holdout protocol differs from explicitly reviewed SHA256")
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^\*\*상태:\s*FROZEN\b", text, flags=re.MULTILINE):
        raise ValueError("Holdout protocol is not FROZEN")
    if re.search(r"\*\*PENDING\b", text):
        raise ValueError("Holdout protocol still has pending items")
    if file_sha256(PROTOCOL_DRAFT) not in text:
        raise ValueError("Holdout protocol does not pin current protocol draft")
    for name, module in (("holdout scorer", Path(__file__)),
                         ("holdout candidate preparer", Path(holdout_prepare.__file__)),
                         ("holdout collector", ROOT / "scripts/fetch_philly_gpu_holdout.py"),
                         ("original source parser", Path(source_module.__file__)),
                         ("GPU slot normalizer", ROOT / "scripts/normalize_philly_gpu_sample.py"),
                         ("original candidate builder", Path(builder.__file__)),
                         ("original risk models", Path(risk_models.__file__)),
                         ("original risk metrics", Path(risk_metrics.__file__))):
        if file_sha256(module) not in text:
            raise ValueError(f"Holdout protocol does not pin current {name} code")
    return text


def verify_original_reference(metrics_path: Path, expected_score_sha256: str,
                              expected_dry_sha256: str,
                              protocol_text: str | None = None) -> tuple[dict, dict, dict]:
    """Verify the original experiment before recovering its train/val rows."""
    if not SHA256_RE.fullmatch(expected_score_sha256):
        raise ValueError("Expected original score SHA256 must be lowercase hex")
    if file_sha256(metrics_path) != expected_score_sha256:
        raise ValueError("Original score differs from explicitly reviewed SHA256")
    if protocol_text is not None and expected_score_sha256 not in protocol_text:
        raise ValueError("Holdout protocol does not pin original score SHA256")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if metrics.get("protocol_status") != "FROZEN" or metrics.get("scoring_performed") is not True:
        raise ValueError("Original Philly score is not a completed frozen result")
    if metrics.get("dry_run_audit_sha256") != expected_dry_sha256:
        raise ValueError("Original score names a different dry-run audit")
    if protocol_text is not None and expected_dry_sha256 not in protocol_text:
        raise ValueError("Holdout protocol does not pin original dry-run SHA256")
    expected_code = {
        "frozen_protocol_sha256": file_sha256(original.FROZEN),
        "draft_protocol_sha256": file_sha256(original.DRAFT),
        "candidate_builder_sha256": file_sha256(Path(builder.__file__)),
        "risk_models_sha256": file_sha256(Path(risk_models.__file__)),
        "risk_metrics_sha256": file_sha256(Path(risk_metrics.__file__)),
        "scorer_sha256": file_sha256(Path(original.__file__)),
    }
    for key, value in expected_code.items():
        if metrics.get(key) != value:
            raise ValueError(f"Original {key} differs from frozen code")
    frozen_text = original.require_frozen_protocol(original.FROZEN, original.DRAFT)
    dry, audited = original.verify_dry_run_manifest(
        original.PREPARED, expected_dry_sha256, frozen_text)
    source_quality = original.source_quality_gate(dry, audited["normalized"])
    if not source_quality["passes"]:
        raise ValueError("Original source quality gate failed")
    statuses = original.stratum_gate_status(dry)
    for cohort, state in STRATA:
        key = f"{cohort}/{state}"
        if metrics.get("strata", {}).get(key, {}).get("gate_pass") is not statuses[(cohort, state)]["passes"]:
            raise ValueError(f"Original score gate status changed for {key}")
    return metrics, dry, statuses


@dataclass
class OriginalModels:
    pooled_quality: risk_models.FittedLogistic
    selected_beta: risk_models.BetaTable
    train_rows: int
    validation_rows: int
    complete_train_rows: int
    primary_validation_rows: int


def _same_float(actual: float, expected: float, name: str) -> None:
    if not math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-10):
        raise ValueError(f"Refitted original {name} differs from frozen score")


def refit_original_train_validation(examples: pd.DataFrame, metrics: dict,
                                    statuses: dict) -> OriginalModels:
    """Reproduce the pooled model and primary Beta using original rows only."""
    if examples.empty or not set(examples.split.unique()).issubset(builder.SPLIT_NAMES):
        raise ValueError("Original replay lacks valid train/validation examples")
    train = examples.loc[examples.split.eq("train")].copy()
    validation = examples.loc[examples.split.eq("validation")].copy()
    if train.empty or validation.empty:
        raise ValueError("Original train or validation split is empty")
    if not statuses[("complete", "below_80")]["passes"]:
        raise ValueError("Original primary cohort/state did not pass frozen gates")
    pooled = risk_models.select_logistic(train, validation, include_quality=True)
    key = "complete/below_80"
    saved = metrics["strata"][key]
    tr = train.loc[train.cohort.eq("complete")]
    va = validation.loc[validation.cohort.eq("complete")
                        & validation.current_state.eq("below_80")]
    beta, beta_scores = risk_models.select_beta_baseline(tr, va)
    if beta.name != "state_recent" or beta.name != saved["selected_beta"]:
        raise ValueError("Refitted original selected Beta is not frozen state_recent")
    for name, score in beta_scores.items():
        _same_float(score, saved["validation_beta_brier"][name],
                    f"{key} Beta {name} validation Brier")
    if pooled.c != 10.0 or pooled.c != saved["selected_model_c"]["pooled_quality"]:
        raise ValueError("Refitted original pooled C differs from frozen score")
    _same_float(pooled.validation_brier,
                saved["validation_model_brier"]["pooled_quality"],
                f"{key} pooled validation Brier")
    medians = [pooled.transform.older_median, pooled.transform.middle_median]
    for index, (actual, expected) in enumerate(zip(
            medians, saved["training_medians"]["pooled_quality"])):
        _same_float(actual, expected, f"{key} pooled median {index}")
    return OriginalModels(pooled, beta, len(train), len(validation), len(tr), len(va))


@dataclass
class FrozenOriginalModel:
    """Pure prediction parameters recovered from the original-only model spec."""

    feature_columns: tuple[str, ...]
    older_median: float
    middle_median: float
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    coefficient: np.ndarray
    intercept: float
    selected_beta: risk_models.BetaTable

    def predict_pooled(self, frame: pd.DataFrame) -> np.ndarray:
        if self.feature_columns != risk_models.FULL_FEATURES:
            raise ValueError("Frozen pooled feature order differs from source code")
        transform = risk_models.FeatureTransform(
            include_quality=True, older_median=self.older_median,
            middle_median=self.middle_median)
        raw = transform._raw_features(frame)
        if (raw.shape[1] != len(self.scaler_mean)
                or raw.shape[1] != len(self.scaler_scale)
                or raw.shape[1] != len(self.coefficient)):
            raise ValueError("Frozen pooled model dimensions differ")
        return expit(((raw - self.scaler_mean) / self.scaler_scale)
                     @ self.coefficient + self.intercept)

    def predict_beta(self, frame: pd.DataFrame) -> np.ndarray:
        return self.selected_beta.predict(frame)


def model_spec_from_refit(models: OriginalModels, *, score_sha256: str,
                          dry_sha256: str, source_sha256: str,
                          prediction_sha256: str) -> dict:
    model = models.pooled_quality
    scaler = model.transform.scaler
    if scaler is None or model.estimator.classes_.tolist() != [0, 1]:
        raise ValueError("Refitted pooled model lacks binary scaler or class order")
    table = models.selected_beta
    return {
        "kind": "philly_original_machines_only_frozen_holdout_model_spec",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "original_score_sha256": score_sha256,
        "original_dry_run_sha256": dry_sha256,
        "original_normalized_source_sha256": source_sha256,
        "original_test_predictions_sha256": prediction_sha256,
        "original_train_rows": models.train_rows,
        "original_validation_rows": models.validation_rows,
        "original_complete_train_rows": models.complete_train_rows,
        "original_primary_validation_rows": models.primary_validation_rows,
        "code_sha256": {
            "candidate_builder": file_sha256(Path(builder.__file__)),
            "risk_models": file_sha256(Path(risk_models.__file__)),
            "risk_metrics": file_sha256(Path(risk_metrics.__file__)),
            "original_scorer": file_sha256(Path(original.__file__)),
            "holdout_scorer": file_sha256(Path(__file__)),
        },
        "pooled_quality": {
            "selected_c": model.c,
            "validation_brier": model.validation_brier,
            "feature_columns": list(model.transform.columns),
            "older_median": model.transform.older_median,
            "middle_median": model.transform.middle_median,
            "scaler_mean": scaler.mean_.astype(float).tolist(),
            "scaler_variance": scaler.var_.astype(float).tolist(),
            "scaler_scale": scaler.scale_.astype(float).tolist(),
            "coefficient": model.estimator.coef_[0].astype(float).tolist(),
            "intercept": float(model.estimator.intercept_[0]),
        },
        "selected_beta": {
            "name": table.name,
            "keys": list(table.keys),
            "probabilities": [
                {"key": [bool(v) for v in key], "probability": float(p)}
                for key, p in sorted(table.probabilities.items())
            ],
        },
    }


def load_frozen_model_spec(spec: dict) -> FrozenOriginalModel:
    if spec.get("kind") != "philly_original_machines_only_frozen_holdout_model_spec":
        raise ValueError("Holdout model spec has an unexpected kind")
    pooled = spec["pooled_quality"]
    beta = spec["selected_beta"]
    if (pooled.get("selected_c") != 10.0
            or tuple(pooled.get("feature_columns", ())) != risk_models.FULL_FEATURES
            or beta.get("name") != "state_recent"
            or tuple(beta.get("keys", ())) != ("current_high", "any_recent_high")):
        raise ValueError("Frozen holdout model choices differ from protocol")
    probabilities = {}
    for row in beta["probabilities"]:
        key = tuple(row["key"])
        if len(key) != 2 or key in probabilities or any(type(v) is not bool for v in key):
            raise ValueError("Frozen Beta table has duplicate or invalid keys")
        probability = float(row["probability"])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Frozen Beta probability is invalid")
        probabilities[key] = probability
    if not probabilities:
        raise ValueError("Frozen Beta table is empty")
    n = len(risk_models.FULL_FEATURES)
    arrays = [np.asarray(pooled[k], dtype=float) for k in
              ("scaler_mean", "scaler_variance", "scaler_scale", "coefficient")]
    if any(x.shape != (n,) or not np.isfinite(x).all() for x in arrays):
        raise ValueError("Frozen pooled model has invalid parameter dimensions")
    mean, variance, scale, coef = arrays
    if (scale <= 0).any() or (variance < 0).any() or not np.isfinite(float(pooled["intercept"])):
        raise ValueError("Frozen pooled scaler or intercept is invalid")
    if not np.allclose(scale, np.where(variance == 0, 1.0, np.sqrt(variance)),
                       rtol=0, atol=1e-10):
        raise ValueError("Frozen pooled scaler variance and scale disagree")
    return FrozenOriginalModel(
        tuple(pooled["feature_columns"]), float(pooled["older_median"]),
        float(pooled["middle_median"]), mean, scale, coef,
        float(pooled["intercept"]),
        risk_models.BetaTable(beta["name"], tuple(beta["keys"]), probabilities),
    )


def verify_original_test_replay(examples: pd.DataFrame, model: FrozenOriginalModel,
                                predictions_path: Path, metrics: dict) -> dict:
    """Require frozen parameters to reproduce original unseen test probabilities."""
    expected_sha = metrics.get("test_predictions_sha256")
    if not isinstance(expected_sha, str) or file_sha256(predictions_path) != expected_sha:
        raise ValueError("Original test predictions differ from frozen score")
    test = examples.loc[examples.split.eq("test")
                        & examples.cohort.eq("complete")
                        & examples.current_state.eq("below_80")].copy()
    saved = pd.read_parquet(predictions_path)
    saved = saved.loc[saved.cohort.eq("complete")
                      & saved.current_state.eq("below_80")].copy()
    if (test.empty or len(test) != len(saved)
            or test.duplicated(list(original.KEYS)).any()
            or saved.duplicated(list(original.KEYS)).any()):
        raise ValueError("Original test replay has missing or duplicate primary keys")
    joined = test.merge(saved[[*original.KEYS, "y", "pooled_quality", "selected_beta"]],
                        on=list(original.KEYS), how="outer", validate="one_to_one",
                        indicator=True, suffixes=("", "_saved"))
    if not joined._merge.eq("both").all() or not joined.y.eq(joined.y_saved).all():
        raise ValueError("Original test replay keys or labels differ from stored predictions")
    pooled = model.predict_pooled(joined)
    beta = model.predict_beta(joined)
    pooled_error = float(np.max(np.abs(pooled - joined.pooled_quality.to_numpy(float))))
    beta_error = float(np.max(np.abs(beta - joined.selected_beta.to_numpy(float))))
    if pooled_error > 1e-10 or beta_error > 1e-10:
        raise ValueError("Frozen original-only model does not replay original test probabilities")
    return {"rows": len(joined), "max_abs_pooled_quality_error": pooled_error,
            "max_abs_selected_beta_error": beta_error}


def freeze_original_only(*, expected_score_sha256: str,
                         expected_dry_sha256: str,
                         metrics_path: Path = ORIGINAL_METRICS,
                         predictions_path: Path = ORIGINAL_PREDICTIONS,
                         output_dir: Path = MODEL_SPEC_DIR) -> dict:
    """Write an immutable model spec; never open a holdout path in this mode."""
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite original-only model spec: {output_dir}")
    metrics, dry, statuses = verify_original_reference(
        metrics_path, expected_score_sha256, expected_dry_sha256)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".philly_original_model_",
                                     dir=output_dir.parent) as temporary:
        root = Path(temporary)
        examples, replay = original.replay_labels(
            builder.SOURCE, dry, original.PREPARED, workdir=root)
        models = refit_original_train_validation(examples, metrics, statuses)
        spec = model_spec_from_refit(
            models, score_sha256=expected_score_sha256,
            dry_sha256=expected_dry_sha256,
            source_sha256=file_sha256(builder.SOURCE),
            prediction_sha256=metrics["test_predictions_sha256"])
        frozen_model = load_frozen_model_spec(spec)
        replay_check = verify_original_test_replay(
            examples, frozen_model, predictions_path, metrics)
        spec["original_test_replay"] = replay_check
        spec["original_replay"] = replay
        final = root / "result"
        final.mkdir()
        (final / "model_spec.json").write_text(
            json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n", encoding="utf-8")
        os.replace(final, output_dir)
    return {"model_spec": str(output_dir / "model_spec.json"),
            "model_spec_sha256": file_sha256(output_dir / "model_spec.json"),
            "original_test_replay": replay_check}


def verify_frozen_model_spec(path: Path, expected_sha256: str,
                             protocol_text: str, old_metrics: dict,
                             expected_old_score_sha256: str,
                             expected_old_dry_sha256: str) -> tuple[dict, FrozenOriginalModel]:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("Expected model spec SHA256 must be lowercase hex")
    if file_sha256(path) != expected_sha256 or expected_sha256 not in protocol_text:
        raise ValueError("Original-only model spec differs from frozen protocol")
    spec = json.loads(path.read_text(encoding="utf-8"))
    expected_fields = {
        "original_score_sha256": expected_old_score_sha256,
        "original_dry_run_sha256": expected_old_dry_sha256,
        "original_normalized_source_sha256": file_sha256(builder.SOURCE),
        "original_test_predictions_sha256": old_metrics["test_predictions_sha256"],
    }
    for name, value in expected_fields.items():
        if spec.get(name) != value:
            raise ValueError(f"Frozen model spec {name} differs from original experiment")
    code = spec.get("code_sha256", {})
    expected_code = {
        "candidate_builder": file_sha256(Path(builder.__file__)),
        "risk_models": file_sha256(Path(risk_models.__file__)),
        "risk_metrics": file_sha256(Path(risk_metrics.__file__)),
        "original_scorer": file_sha256(Path(original.__file__)),
        "holdout_scorer": file_sha256(Path(__file__)),
    }
    if code != expected_code:
        raise ValueError("Frozen model spec code hashes changed")
    if spec.get("original_test_replay", {}).get("rows") != int(
            old_metrics["strata"]["complete/below_80"]["metrics"]["pooled_quality"]["rows"]):
        raise ValueError("Frozen model spec old-test replay row count changed")
    for name in ("max_abs_pooled_quality_error", "max_abs_selected_beta_error"):
        if not 0 <= float(spec["original_test_replay"][name]) <= 1e-10:
            raise ValueError("Frozen model spec old-test replay accuracy changed")
    return spec, load_frozen_model_spec(spec)


def verify_holdout_dry_run(prepared: Path, expected_sha256: str,
                           *, raw_audit_path: Path,
                           normalized_audit_path: Path,
                           normalized_path: Path,
                           raw: dict, normalized: dict,
                           old_dry: dict,
                           expected_protocol_sha256: str,
                           expected_model_spec_sha256: str) -> tuple[dict, dict]:
    """Verify the reviewed score-free aggregate audit and candidate files."""
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("Expected holdout dry-run SHA256 must be lowercase hex")
    audit_path = prepared / "dry_run_audit.json"
    if file_sha256(audit_path) != expected_sha256:
        raise ValueError("Holdout dry-run differs from explicitly reviewed SHA256")
    dry = json.loads(audit_path.read_text(encoding="utf-8"))
    if dry.get("kind") != "holdout_audit_only_no_model_fit_no_row_level_outcome":
        raise ValueError("Holdout dry-run is not a score-free aggregate audit")
    expected_fields = {
        "frozen_protocol_sha256": expected_protocol_sha256,
        "original_model_spec_sha256": expected_model_spec_sha256,
        "source_archive_sha256": source_module.EXPECTED_SHA256,
        "source_audit_path": str(raw_audit_path),
        "source_audit_sha256": file_sha256(raw_audit_path),
        "source_sample_sha256": raw["sample_sha256"],
        "normalization_audit_path": str(normalized_audit_path),
        "normalization_audit_sha256": file_sha256(normalized_audit_path),
        "normalized_sample_path": str(normalized_path),
        "normalized_sample_sha256": normalized["output_sha256"],
        "original_source_audit_sha256": file_sha256(holdout_prepare.OLD_SOURCE_AUDIT),
        "original_dry_run_sha256": file_sha256(holdout_prepare.OLD_DRY_RUN),
        "preparation_script_sha256": file_sha256(Path(holdout_prepare.__file__)),
        "candidate_builder_sha256": file_sha256(Path(builder.__file__)),
    }
    for name, value in expected_fields.items():
        if dry.get(name) != value:
            raise ValueError(f"Holdout dry-run {name} differs from verified lineage")
    if dry.get("test_only_bounds") != holdout_prepare.test_only_bounds(old_dry):
        raise ValueError("Holdout dry-run changed original test-only UTC bounds")
    if dry.get("source_raw_utc_extrema_only_split") != old_dry["source_raw_utc_extrema_only_split"]:
        raise ValueError("Holdout dry-run changed original full UTC split bounds")
    if dry.get("test_span") != old_dry["source_raw_utc_extrema_only_split"]["spans"]["test"]:
        raise ValueError("Holdout dry-run changed original test span")
    selection = dry.get("machine_selection", {})
    if selection != {
            "rule": holdout_prepare.HOLDOUT_RULE,
            "low_inclusive": LOWER_RESIDUE,
            "high_exclusive": UPPER_RESIDUE,
            "selected_machines": len(normalized["machines"]),
            "overlap_with_original": 0}:
        raise ValueError("Holdout dry-run changed machine selection")
    expected_outputs = {
        "candidates.parquet": builder.CANDIDATE_SCHEMA,
        "future_observability.parquet": builder.FUTURE_OBSERVABILITY_SCHEMA,
        "future_observability_summary.parquet": builder.OBSERVABILITY_SCHEMA,
    }
    outputs = dry.get("outputs", {})
    if set(outputs) != set(expected_outputs):
        raise ValueError("Holdout dry-run output inventory changed")
    for name, schema in expected_outputs.items():
        path = prepared / name
        file = pq.ParquetFile(path)
        if (file_sha256(path) != outputs[name].get("sha256")
                or file.metadata.num_rows != outputs[name].get("rows")
                or file.schema_arrow.names != schema.names):
            raise ValueError(f"Holdout saved Parquet differs from dry-run: {name}")
    if outputs["candidates.parquet"]["rows"] != outputs["future_observability.parquet"]["rows"]:
        raise ValueError("Holdout candidate/future row counts differ")
    width_expected = []
    eligible = set()
    for machine, item in sorted(normalized["machines"].items()):
        rows, bad = int(item["rows"]), int(item["invalid_rows"])
        slots = int(item["first_observed_gpu_slots"])
        if rows <= 0 or not 0 <= bad <= rows:
            raise ValueError("Holdout normalized width counts invalid")
        reason = ("unsupported_first_observed_width" if slots not in (2, 4, 8)
                  else "width_mismatch_above_one_percent" if bad / rows > 0.01
                  else None)
        if reason is None:
            eligible.add(machine)
        width_expected.append({
            "machine_hash": builder.machine_hash(machine),
            "expected_gpu_slots": slots, "rows": rows,
            "width_mismatch_rows": bad, "width_mismatch_fraction": bad / rows,
            "retained": reason is None, "exclusion_reason": reason,
        })
    quality = dry.get("source_quality", {})
    if quality.get("per_machine") != width_expected:
        raise ValueError("Holdout source-quality exclusions changed")
    if (quality.get("retained_machines") != len(eligible)
            or quality.get("excluded_machines") != len(width_expected) - len(eligible)
            or quality.get("excluded_rows") != sum(
                item["rows"] for item in width_expected if not item["retained"])
            or quality.get("retained_rows") != sum(
                item["rows"] for item in width_expected if item["retained"])):
        raise ValueError("Holdout source-quality machine or row totals changed")
    split_quality = quality.get("input_quality_by_original_split", {})
    if set(split_quality) != set(builder.SPLIT_NAMES):
        raise ValueError("Holdout source-quality split inventory changed")
    total_slots = sum(item["expected_gpu_slots"]
                      for item in width_expected if item["retained"])
    for name in builder.SPLIT_NAMES:
        item = split_quality[name]
        observed = int(item["observed_window_slots"])
        valid = int(item["valid_window_slots"])
        grid = int(item["fixed_calendar_grid_window_slots"])
        missing = int(item["raw_row_absent_grid_window_slots"])
        fraction = valid / observed if observed else None
        expected_grid = int(old_dry["source_raw_utc_extrema_only_split"]
                            ["spans"][name]["days"]) * 288 * total_slots
        if (not 0 <= valid <= observed <= grid or grid != expected_grid
                or missing != grid - observed
                or item.get("valid_fraction") != fraction):
            raise ValueError(f"Holdout source-quality five-minute counts invalid for {name}")
    test_fraction = split_quality["test"]["valid_fraction"]
    test_passes = test_fraction is not None and test_fraction >= 0.50
    if (quality.get("test_input_passes") is not test_passes
            or quality.get("passes") is not (bool(eligible) and test_passes)):
        raise ValueError("Holdout source-quality pass flag differs from counts")
    statuses = holdout_test_gate_status(
        dry.get("gate_counts_by_split_cohort_state", []), dry["test_only_bounds"])
    half_rows = dry.get("primary_half_gate_counts")
    if not isinstance(half_rows, list) or len(half_rows) != 2:
        raise ValueError("Holdout dry-run lacks two fixed primary half gates")
    span = dry["test_span"]
    start = int(span["start_utc_day"])
    end = int(span["end_utc_day_exclusive"])
    middle = start + (end - start) // 2
    for index, (row, lo, hi) in enumerate(zip(
            half_rows, (start, middle), (middle, end)), start=1):
        pos, neg = int(row["positives"]), int(row["negatives"])
        if (row.get("half") != index or row.get("start_utc_day") != lo
                or row.get("end_utc_day_exclusive") != hi
                or pos < 0 or neg < 0 or row.get("eligible") != pos + neg
                or row.get("passes_both_classes") is not (pos >= 1 and neg >= 1)):
            raise ValueError("Holdout dry-run primary half gate disagrees with counts")
    main_counts = statuses[("complete", "below_80")]["counts"]
    if (sum(row["positives"] for row in half_rows)
            != int(main_counts.get("positive_gate_count", 0))
            or sum(row["negatives"] for row in half_rows)
            != int(main_counts.get("negative_gate_count", 0))):
        raise ValueError("Holdout primary half counts differ from primary stratum")
    return dry, statuses


def replay_holdout_labels(source: Path, prepared: Path, dry: dict,
                          normalized_audit: dict, full_bounds: dict, *, workdir: Path
                          ) -> tuple[pd.DataFrame, dict]:
    """Replay new-machine candidates exactly before retaining any observed y."""
    eligible = {machine for machine, item in normalized_audit["machines"].items()
                if item["first_observed_gpu_slots"] in (2, 4, 8)
                and item["invalid_rows"] / item["rows"] <= .01}
    partition = dry["partitioning"]
    bucket_dir = workdir / "buckets"
    bucket_dir.mkdir()
    span = dry["test_span"]
    buckets, audit = holdout_prepare.partition_holdout(
        source, bucket_dir, eligible,
        int(span["start_utc_s"]), int(span["end_utc_s"]),
        int(partition["machine_hash_buckets"]),
        int(partition["max_partition_rows"]))
    if (len(buckets) != partition["bucket_count_used"]
            or max((n for _, n in buckets.values()), default=0)
            != partition["max_bucket_rows_observed"]):
        raise ValueError("Holdout replay partitioning changed")
    quality = dry["source_quality"]
    for name in ("excluded_rows", "all_test_active_machines",
                 "retained_test_active_machines", "excluded_test_active_machines"):
        if audit[name] != quality[name]:
            raise ValueError(f"Holdout replay partition audit changed: {name}")
    candidate_reader = original.ParquetSliceReader(
        prepared / "candidates.parquet", builder.CANDIDATE_SCHEMA.names)
    future_reader = original.ParquetSliceReader(
        prepared / "future_observability.parquet",
        builder.FUTURE_OBSERVABILITY_SCHEMA.names)
    summary_reader = original.ParquetSliceReader(
        prepared / "future_observability_summary.parquet",
        builder.OBSERVABILITY_SCHEMA.names)
    gates = holdout_prepare.HoldoutGateAccumulator(dry["test_span"])
    totals: Counter[str] = Counter()
    input_quality = {name: Counter() for name in builder.SPLIT_NAMES}
    seen_machines: set[str] = set()
    observed_frames = []
    for bucket, (path, count) in buckets.items():
        frame = pd.read_parquet(path)
        if len(frame) != count or len(frame) > int(partition["max_partition_rows"]):
            raise ValueError(f"Holdout replay bucket {bucket} row count changed")
        windows, _, summary = builder.minute_to_windows(frame)
        totals.update(summary)
        hashes = set(windows.machine_hash.unique()) if not windows.empty else set()
        if seen_machines & hashes:
            raise ValueError("One holdout machine appeared in multiple replay buckets")
        seen_machines.update(hashes)
        quality_frame = builder._window_quality(windows)
        full_counts = builder.split_input_window_counts(quality_frame, full_bounds)
        for name in builder.SPLIT_NAMES:
            input_quality[name].update(full_counts[name])
        candidates, futures, labels, candidate_summary = builder.construct_candidates(
            windows, dry["test_only_bounds"], None,
            include_scoring_labels=True)
        totals.update(candidate_summary)
        if labels is None or not (len(candidates) == len(futures) == len(labels)):
            raise ValueError("Holdout replay did not return aligned labels")
        for candidate, future, label in zip(candidates, futures, labels):
            original._compare_saved(candidate_reader, candidate,
                                    builder.CANDIDATE_SCHEMA.names, "holdout candidate")
            original._compare_saved(future_reader, future,
                                    builder.FUTURE_OBSERVABILITY_SCHEMA.names,
                                    "holdout future observability")
            summary = builder._observability(candidate, future)
            original._compare_saved(summary_reader, summary,
                                    builder.OBSERVABILITY_SCHEMA.names,
                                    "holdout observability summary")
            observed = original.validate_replayed_chunk(
                candidate, future, label, dry["test_only_bounds"])
            if not candidate.empty:
                gates.add(candidate, future.future_complete.to_numpy(bool),
                          label.y.fillna(0).to_numpy(dtype=bool))
            if not observed.empty:
                observed_frames.append(observed)
    candidate_reader.finish()
    future_reader.finish()
    summary_reader.finish()
    if holdout_prepare.test_gate_rows(gates) != dry["gate_counts_by_split_cohort_state"]:
        raise ValueError("Holdout replay changed pre-score aggregate labels or cohorts")
    if gates.half_gate() != dry["primary_half_gate_counts"]:
        raise ValueError("Holdout replay changed pre-score primary half class counts")
    for name in ("raw_rows", "candidate_rows", "future_complete_rows"):
        if totals[name] != int(dry["totals"].get(name, -1)):
            raise ValueError(f"Holdout replay changed total {name}")
    for name in builder.SPLIT_NAMES:
        saved = dry["source_quality"]["input_quality_by_original_split"][name]
        for key in ("observed_window_slots", "valid_window_slots"):
            if int(input_quality[name][key]) != int(saved[key]):
                raise ValueError(f"Holdout replay changed five-minute quality {name} {key}")
    examples = pd.concat(observed_frames, ignore_index=True) if observed_frames else pd.DataFrame()
    if not examples.empty and (
            not examples.split.eq("test").all()
            or examples.duplicated(list(original.KEYS)).any()):
        raise ValueError("Holdout replay has non-test or duplicate observed decisions")
    return examples, {"replayed_bucket_count": len(buckets),
                      "candidate_rows": int(totals["candidate_rows"]),
                      "observed_labeled_rows": len(examples)}


def holdout_test_gate_status(rows: list[dict], bounds: dict) -> dict[tuple[str, str], dict]:
    """Apply the frozen numeric gates to test rows only, before probabilities."""
    index = {}
    for row in rows:
        key = (row.get("split"), row.get("cohort"), row.get("current_state"))
        if key in index or key[0] != "test" or key[1:] not in STRATA:
            raise ValueError("Duplicate, non-test, or unknown holdout gate row")
        index[key] = row
    if set(index) != {("test", cohort, state) for cohort, state in STRATA}:
        raise ValueError("Holdout dry-run must enumerate all four test strata")
    result = {}
    for cohort, state in STRATA:
        row = index.get(("test", cohort, state), {})
        candidates = int(row.get("candidates", 0))
        observed = int(row.get("future_complete", 0))
        positive = int(row.get("positive_gate_count", 0))
        negative = int(row.get("negative_gate_count", 0))
        machines = int(row.get("eligible_machine_count", 0))
        positive_machines = int(row.get("positive_machine_count", 0))
        negative_machines = int(row.get("negative_machine_count", 0))
        positive_days = int(row.get("positive_utc_day_count", 0))
        if (candidates < 0 or not 0 <= observed <= candidates
                or positive < 0 or negative < 0 or positive + negative != observed
                or not 0 <= positive_machines <= machines
                or not 0 <= negative_machines <= machines):
            raise ValueError("Inconsistent holdout gate counts")
        if row:
            expected_fraction = observed / candidates if candidates else None
            saved_fraction = row.get("future_observation_fraction")
            if ((saved_fraction is None) != (expected_fraction is None)
                    or (saved_fraction is not None and not math.isclose(
                        float(saved_fraction), expected_fraction, rel_tol=0, abs_tol=1e-12))):
                raise ValueError("Holdout future observation fraction differs from counts")
        flags = {
            "future_observation_at_least_half": candidates > 0 and observed / candidates >= .5,
            "eligible_at_least_2000": observed >= 2000,
            "positive_at_least_100": positive >= 100,
            "negative_at_least_100": negative >= 100,
            "eligible_machines_at_least_10": machines >= 10,
            "positive_machines_at_least_5": positive_machines >= 5,
            "negative_machines_at_least_5": negative_machines >= 5,
            "test_positives_on_five_days": positive_days >= 5,
        }
        if row:
            if row.get("gate_checks") != flags or row.get("gate_pass") is not all(flags.values()):
                raise ValueError("Holdout saved gate checks differ from numeric counts")
        result[(cohort, state)] = {"counts": row, "checks": flags,
                                   "passes": bounds["spans"]["test"]["days"] >= 3
                                   and all(flags.values())}
    return result


def paired_group_brier(frame: pd.DataFrame, group: str) -> list[dict]:
    """Report each calendar day/machine with explicit pooled-minus-Beta delta."""
    result = []
    for value, part in frame.groupby(group, sort=True):
        y = part.y.to_numpy(dtype=int)
        pooled = float(np.mean((part.pooled_quality.to_numpy(float) - y) ** 2))
        beta = float(np.mean((part.selected_beta.to_numpy(float) - y) ** 2))
        result.append({group: int(value) if group == "utc_day" else str(value),
                       "rows": len(part), "positives": int(y.sum()),
                       "negatives": int(len(y) - y.sum()),
                       "pooled_quality_brier": pooled,
                       "selected_beta_brier": beta,
                       "pooled_minus_beta_brier": pooled - beta})
    return result


def holdout_primary_criteria(metrics: dict, bootstrap: dict,
                             halves: list[dict], by_machine: list[dict]) -> dict:
    pooled = metrics["pooled_quality"]
    beta = metrics["selected_beta"]
    if beta["brier"] <= 0 or not by_machine:
        raise ValueError("Holdout primary Brier denominator or machines invalid")
    relative = 1 - pooled["brier"] / beta["brier"]
    equal_machine_delta = float(np.mean(
        [row["pooled_minus_beta_brier"] for row in by_machine]))
    halves_evaluable = len(halves) == 2 and all(
        row["positives"] >= 1 and row["negatives"] >= 1 for row in halves)
    flags = {
        "relative_brier_improvement_at_least_5pct": relative >= 0.05,
        "pooled_minus_beta_brier_below_zero": pooled["brier"] < beta["brier"],
        "machine_bootstrap_upper_below_zero": bootstrap["percentile_95"][1] < 0,
        "both_calendar_halves_lower_brier": halves_evaluable and all(
            row["first_brier"] < row["second_brier"] for row in halves),
        "equal_machine_mean_brier_delta_below_zero": equal_machine_delta < 0,
        "average_precision_not_lower": (
            pooled["average_precision"] is not None
            and beta["average_precision"] is not None
            and pooled["average_precision"] >= beta["average_precision"]),
        "daily_top5_recall_not_lower": (
            pooled["daily_top_5pct"]["recall"] is not None
            and beta["daily_top_5pct"]["recall"] is not None
            and pooled["daily_top_5pct"]["recall"]
            >= beta["daily_top_5pct"]["recall"]),
    }
    return {"evaluable": halves_evaluable,
            "reason": None if halves_evaluable else "half_class_support_failed",
            "relative_brier_improvement": relative,
            "equal_machine_mean_brier_delta": equal_machine_delta,
            "calendar_halves": halves, "conditions": flags,
            "passes": halves_evaluable and all(flags.values())}


def score_new_machine_test(examples: pd.DataFrame, models: FrozenOriginalModel,
                           holdout_status: dict, original_status: dict,
                           bounds: dict) -> tuple[pd.DataFrame, dict]:
    """Apply fixed original models to eligible heldout test rows only."""
    if not examples.empty and not examples.split.eq("test").all():
        raise ValueError("New-machine scorer received non-test examples")
    results = {}
    outputs = []
    for cohort, state in STRATA:
        key = f"{cohort}/{state}"
        gate = holdout_status[(cohort, state)]
        original_pass = original_status[(cohort, state)]["passes"]
        eligible = gate["passes"] and original_pass
        results[key] = {"holdout_gate": gate, "original_training_gate_pass": original_pass,
                        "scored": eligible}
        if not eligible:
            continue
        test = examples.loc[examples.cohort.eq(cohort)
                            & examples.current_state.eq(state)].copy()
        if len(test) != int(gate["counts"]["future_complete"]):
            raise ValueError(f"Holdout observed-label count differs from gate: {key}")
        p = test[list(original.SCORE_COLUMNS)].copy()
        p["pooled_quality"] = models.predict_pooled(test)
        p["selected_beta"] = models.predict_beta(test)
        metrics = {name: risk_metrics.binary_metrics(p, name) for name in
                   ("pooled_quality", "selected_beta")}
        bootstrap = risk_metrics.paired_machine_bootstrap(
            p, "pooled_quality", "selected_beta", draws=2000, seed=43)
        results[key].update({
            "selected_beta": models.selected_beta.name,
            "selected_model_c": 10.0,
            "metrics": metrics, "machine_bootstrap": bootstrap,
            "brier_by_utc_day": paired_group_brier(p, "utc_day"),
            "brier_by_machine": paired_group_brier(p, "machine_hash"),
        })
        outputs.append(p)
    predictions = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
    primary = results["complete/below_80"]
    if primary["scored"]:
        p = predictions.loc[predictions.cohort.eq("complete")
                            & predictions.current_state.eq("below_80")]
        halves = original.fixed_half_brier(p, "pooled_quality", "selected_beta",
                                            bounds["spans"]["test"])
        verdict = holdout_primary_criteria(
            primary["metrics"], primary["machine_bootstrap"], halves,
            primary["brier_by_machine"])
    else:
        verdict = {"evaluable": False, "reason": "holdout_or_original_gate_failed"}
    return predictions, {"strata": results, "primary": verdict}


def observed_support_by_stratum(examples: pd.DataFrame) -> dict[str, dict]:
    """Expose machine, calendar-day, and GPU-slot support before model scoring."""
    result = {}
    for cohort, state in STRATA:
        part = examples.loc[examples.cohort.eq(cohort)
                            & examples.current_state.eq(state)] if not examples.empty else pd.DataFrame()
        result[f"{cohort}/{state}"] = {
            "observed_label_rows": len(part),
            "unique_machine_ids": int(part.machine_hash.nunique()) if not part.empty else 0,
            "unique_machine_gpu_slots": int(
                part[["machine_hash", "gpu_index"]].drop_duplicates().shape[0]) if not part.empty else 0,
            "rows_by_utc_day": {str(int(day)): int(n) for day, n in
                                part.groupby("utc_day").size().items()} if not part.empty else {},
            "rows_by_gpu_index": {str(int(slot)): int(n) for slot, n in
                                 part.groupby("gpu_index").size().items()} if not part.empty else {},
        }
    return result


def score(args: argparse.Namespace) -> dict:
    """Verify frozen inputs, replay old model, then replay and score new machines."""
    output: Path = args.output
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite machine-holdout score: {output}")
    protocol_text = require_frozen_holdout_protocol(
        args.holdout_protocol, args.expected_holdout_protocol_sha256)
    old_metrics, old_dry, old_status = verify_original_reference(
        args.original_score, args.expected_original_score_sha256,
        args.expected_original_dry_run_sha256, protocol_text)
    spec, model = verify_frozen_model_spec(
        args.model_spec, args.expected_model_spec_sha256,
        protocol_text, old_metrics,
        args.expected_original_score_sha256,
        args.expected_original_dry_run_sha256)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".philly_machine_holdout_score_",
                                     dir=output.parent) as temporary:
        root = Path(temporary)
        old_workdir = root / "original"
        old_workdir.mkdir()
        old_examples, old_replay = original.replay_labels(
            builder.SOURCE, old_dry, original.PREPARED, workdir=old_workdir)
        old_test_check = verify_original_test_replay(
            old_examples, model, ORIGINAL_PREDICTIONS, old_metrics)
        if old_test_check["rows"] != spec["original_test_replay"]["rows"]:
            raise ValueError("Original-only model test replay row count changed")
        del old_examples

        # The new-machine source is first opened after the original-only model
        # parameters and their original test predictions have been verified.
        raw, normalized, old_dry_from_holdout = holdout_prepare.verify_holdout_lineage(
            args.holdout_normalized, args.holdout_normalized_audit,
            args.holdout_raw_audit, holdout_prepare.OLD_SOURCE_AUDIT,
            holdout_prepare.OLD_DRY_RUN)
        if raw.get("sample_parquet") != str(args.holdout_raw):
            raise ValueError("Holdout raw sample path differs from requested source")
        if old_dry_from_holdout != old_dry:
            raise ValueError("Holdout ingestion refers to a different original dry-run")
        dry, new_status = verify_holdout_dry_run(
            args.prepared, args.expected_holdout_dry_run_sha256,
            raw_audit_path=args.holdout_raw_audit,
            normalized_audit_path=args.holdout_normalized_audit,
            normalized_path=args.holdout_normalized,
            raw=raw, normalized=normalized, old_dry=old_dry,
            expected_protocol_sha256=args.expected_holdout_protocol_sha256,
            expected_model_spec_sha256=args.expected_model_spec_sha256)
        new_workdir = root / "holdout"
        new_workdir.mkdir()
        examples, replay = replay_holdout_labels(
            args.holdout_normalized, args.prepared, dry, normalized,
            old_dry["source_raw_utc_extrema_only_split"], workdir=new_workdir)
        old_hashes = {builder.machine_hash(machine)
                      for machine in json.loads(
                          holdout_prepare.OLD_SOURCE_AUDIT.read_text(
                              encoding="utf-8"))["gpu_csv"]["selected_machine_ids"]}
        if not examples.empty and set(examples.machine_hash) & old_hashes:
            raise ValueError("New-machine decisions overlap original machine hashes")
        support = observed_support_by_stratum(examples)
        for cohort, state in STRATA:
            key = f"{cohort}/{state}"
            if (support[key]["observed_label_rows"]
                    != int(new_status[(cohort, state)]["counts"]["future_complete"])):
                raise ValueError(f"Holdout support rows differ from dry-run gate: {key}")
        source_quality_pass = bool(dry["source_quality"]["passes"])
        if source_quality_pass:
            predictions, scored = score_new_machine_test(
                examples, model, new_status, old_status,
                old_dry["source_raw_utc_extrema_only_split"])
        else:
            predictions = pd.DataFrame()
            scored = {"strata": {
                f"{cohort}/{state}": {
                    "holdout_gate": new_status[(cohort, state)],
                    "original_training_gate_pass": old_status[(cohort, state)]["passes"],
                    "scored": False}
                for cohort, state in STRATA},
                "primary": {"evaluable": False,
                            "reason": "holdout_source_input_quality_failed"}}
        result = {
            "protocol_status": "FROZEN",
            "original_score_sha256": args.expected_original_score_sha256,
            "original_dry_run_sha256": args.expected_original_dry_run_sha256,
            "holdout_protocol_sha256": args.expected_holdout_protocol_sha256,
            "holdout_dry_run_sha256": args.expected_holdout_dry_run_sha256,
            "model_spec_sha256": args.expected_model_spec_sha256,
            "holdout_scorer_sha256": file_sha256(Path(__file__)),
            "holdout_preparer_sha256": file_sha256(Path(holdout_prepare.__file__)),
            "holdout_normalized_sha256": normalized["output_sha256"],
            "original_model_test_replay": old_test_check,
            "original_source_replay": old_replay,
            "holdout_source_quality": dry["source_quality"],
            "holdout_replay": replay,
            "holdout_gate_status": {f"{cohort}/{state}": new_status[(cohort, state)]
                                    for cohort, state in STRATA},
            "observed_support_by_stratum": support,
            "primary_half_gate_counts": dry["primary_half_gate_counts"],
            "strata": scored["strata"], "primary": scored["primary"],
            "scoring_performed": not predictions.empty,
        }
        final = root / "result"
        final.mkdir()
        if not predictions.empty:
            if list(predictions.columns) != list(NEW_SCORE_COLUMNS):
                raise ValueError("Holdout prediction schema changed")
            prediction_path = final / "test_predictions.parquet"
            predictions.to_parquet(prediction_path, index=False)
            result["test_prediction_rows"] = len(predictions)
            result["test_predictions_sha256"] = file_sha256(prediction_path)
        (final / "metrics.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8")
        os.replace(final, output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-original-only", action="store_true",
                        help="Write the original-only model spec before opening new-machine data")
    parser.add_argument("--expected-holdout-protocol-sha256")
    parser.add_argument("--expected-holdout-dry-run-sha256")
    parser.add_argument("--expected-model-spec-sha256")
    parser.add_argument("--expected-original-score-sha256", required=True)
    parser.add_argument("--expected-original-dry-run-sha256", required=True)
    parser.add_argument("--holdout-protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--original-score", type=Path, default=ORIGINAL_METRICS)
    parser.add_argument("--model-spec", type=Path, default=MODEL_SPEC)
    parser.add_argument("--model-spec-dir", type=Path, default=MODEL_SPEC_DIR)
    parser.add_argument("--holdout-raw", type=Path, default=HOLDOUT_RAW)
    parser.add_argument("--holdout-raw-audit", type=Path, default=HOLDOUT_RAW_AUDIT)
    parser.add_argument("--holdout-normalized", type=Path, default=HOLDOUT_NORMALIZED)
    parser.add_argument("--holdout-normalized-audit", type=Path,
                        default=HOLDOUT_NORMALIZED_AUDIT)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.freeze_original_only:
        result = freeze_original_only(
            expected_score_sha256=args.expected_original_score_sha256,
            expected_dry_sha256=args.expected_original_dry_run_sha256,
            metrics_path=args.original_score, output_dir=args.model_spec_dir)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return
    for name in ("expected_holdout_protocol_sha256",
                 "expected_holdout_dry_run_sha256", "expected_model_spec_sha256"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required for heldout scoring")
    result = score(args)
    print(json.dumps({"output": str(args.output),
                      "scoring_performed": result["scoring_performed"],
                      "primary_evaluable": result["primary"]["evaluable"],
                      "primary_passes": result["primary"].get("passes")},
                     ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
