"""Research-only 30-minute GPU-slot high-utilization risk from frozen Philly data.

The caller declares three complete, same-slot, five-minute mean windows. The
saved model was tested on the 2017 Microsoft Philly GPU trace; this function
does not infer a VM shape, recommend a deployment, or authorize scaling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

import evaluate_philly_machine_holdout as holdout


ROOT = Path(__file__).resolve().parents[1]
MODEL_SPEC = ROOT / "artifacts/philly_machine_holdout_model_v1/model_spec.json"
NORMALIZED_SOURCE = ROOT / "data/external/msr_philly_2017/gpu_util_normalized_first_width.parquet"
MODEL_SPEC_SHA256 = "861005a57cbf9f370a8f36682ca9004a3301af01f45e9c797949bf6319ad76a3"
SOURCE_DOMAIN = "msr_philly_2017_gpu_trace"
MACHINE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ROLES = ("older", "middle", "current")
OFFSETS = (-1200, -900, -600)
CODE_PATHS = {
    "candidate_builder": ROOT / "scripts/prepare_philly_gpu_risk_candidates.py",
    "holdout_scorer": ROOT / "scripts/evaluate_philly_machine_holdout.py",
    "original_scorer": ROOT / "scripts/evaluate_philly_gpu_risk.py",
    "risk_metrics": ROOT / "scripts/philly_risk_metrics.py",
    "risk_models": ROOT / "scripts/philly_risk_models.py",
}
REQUEST_FIELDS = {"source_domain", "machine_hash", "gpu_index", "expected_gpu_slots",
                  "decision_utc_s", "windows"}
WINDOW_FIELDS = {"role", "machine_hash", "gpu_index", "window_start_utc_s",
                 "available_at_utc_s", "valid_minutes", "mean_util_percent"}
EVENT = "any_next_six_5min_gpu_slot_mean_windows_at_least_80_percent"
SCOPE = {
    "kind": "same_trace_single_gpu_slot_risk_estimate",
    "evaluated_population": "Microsoft Philly 2017 GPU trace, complete/below_80",
    "future_label_requires_all_six_windows_observed": True,
    "deployment_authority": False,
}


def canonical_request_sha256(request: Any) -> str:
    """Digest one JSON request using stable key order and finite JSON values."""
    try:
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("request_must_be_finite_json") from exc
    return hashlib.sha256(encoded).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_verified_model(*, spec_path: Path = MODEL_SPEC,
                        source_path: Path = NORMALIZED_SOURCE,
                        code_paths: dict[str, Path] | None = None
                        ) -> tuple[holdout.FrozenOriginalModel, dict]:
    """Hash fixed source, spec, and its frozen code before any prediction."""
    if sha256(spec_path) != MODEL_SPEC_SHA256:
        raise ValueError("model_spec_sha256_mismatch")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    source_digest = spec.get("original_normalized_source_sha256")
    if not isinstance(source_digest, str) or sha256(source_path) != source_digest:
        raise ValueError("normalized_source_sha256_mismatch")
    paths = CODE_PATHS if code_paths is None else code_paths
    expected_codes = spec.get("code_sha256")
    if not isinstance(expected_codes, dict) or set(paths) != set(expected_codes):
        raise ValueError("frozen_code_inventory_mismatch")
    for name, path in paths.items():
        if sha256(path) != expected_codes[name]:
            raise ValueError(name + "_sha256_mismatch")
    model = holdout.load_frozen_model_spec(spec)
    provenance = {
        "model_spec_sha256": MODEL_SPEC_SHA256,
        "normalized_source_sha256": source_digest,
        "frozen_code_sha256": dict(expected_codes),
        "api_code_sha256": sha256(Path(__file__)),
        "artifact_hashes_verified_at_load": True,
        "window_metadata_provenance": "caller_declared",
    }
    return model, provenance


def _positive_aligned_second(value: Any) -> bool:
    return type(value) is int and value > 0 and value % 300 == 0


def _valid_util(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 100
    except OverflowError:
        return False


def _request_errors(request: Any) -> list[str]:
    if not isinstance(request, dict):
        return ["request_must_be_object"]
    errors = []
    if set(request) != REQUEST_FIELDS:
        errors.append("request_fields_mismatch")
    if request.get("source_domain") != SOURCE_DOMAIN:
        errors.append("unsupported_source_domain")
    machine = request.get("machine_hash")
    if not isinstance(machine, str) or not MACHINE_HASH_RE.fullmatch(machine):
        errors.append("invalid_machine_hash")
    slots = request.get("expected_gpu_slots")
    gpu = request.get("gpu_index")
    if type(slots) is not int or slots not in (2, 4, 8):
        errors.append("unsupported_expected_gpu_slots")
    if type(gpu) is not int or gpu < 0 or (type(slots) is int and gpu >= slots):
        errors.append("invalid_gpu_index")
    decision = request.get("decision_utc_s")
    if not _positive_aligned_second(decision):
        errors.append("invalid_decision_utc_s")
    windows = request.get("windows")
    if not isinstance(windows, list) or len(windows) != 3:
        errors.append("expected_three_windows")
        return errors
    for index, (window, role, offset) in enumerate(zip(windows, ROLES, OFFSETS)):
        if not isinstance(window, dict) or set(window) != WINDOW_FIELDS:
            errors.append(f"window_{index}_fields_mismatch")
            continue
        if window["role"] != role:
            errors.append(f"window_{index}_role_or_order_mismatch")
        if window["machine_hash"] != machine or type(window["gpu_index"]) is not int or window["gpu_index"] != gpu:
            errors.append(f"window_{index}_different_gpu_slot")
        start = window["window_start_utc_s"]
        if (not _positive_aligned_second(start) or type(decision) is not int
                or start != decision + offset):
            errors.append(f"window_{index}_wrong_start_time")
        available = window["available_at_utc_s"]
        if (type(available) is not int or type(start) is not int
                or type(decision) is not int or available < start + 300
                or available > decision):
            errors.append(f"window_{index}_unavailable_at_decision")
        if type(window["valid_minutes"]) is not int or window["valid_minutes"] != 5:
            errors.append(f"window_{index}_incomplete")
        if not _valid_util(window["mean_util_percent"]):
            errors.append(f"window_{index}_invalid_utilization")
    if (isinstance(windows[2], dict) and _valid_util(windows[2].get("mean_util_percent"))
            and windows[2]["mean_util_percent"] >= 80):
        errors.append("current_utilization_at_least_80")
    return errors


def assess_philly_gpu_risk(request: Any, *, spec_path: Path = MODEL_SPEC,
                           source_path: Path = NORMALIZED_SOURCE,
                           code_paths: dict[str, Path] | None = None) -> dict:
    """Return two frozen research probabilities or ABSTAIN; no action output."""
    model, provenance = load_verified_model(
        spec_path=spec_path, source_path=source_path, code_paths=code_paths)
    request_digest = canonical_request_sha256(request)
    base = {
        "event": EVENT,
        "horizon_seconds": 1800,
        "source_domain": SOURCE_DOMAIN,
        "model_name": "pooled_quality_logistic",
        "baseline_name": "state_recent_beta",
        "input_observation_verified": False,
        "assessed_request_sha256": request_digest,
        "scope": dict(SCOPE),
        "provenance": provenance,
    }
    errors = _request_errors(request)
    if errors:
        return {**base, "status": "ABSTAIN", "reasons": errors,
                "pooled_probability": None, "selected_beta_probability": None}
    older, middle, current = [float(w["mean_util_percent"]) for w in request["windows"]]
    frame = pd.DataFrame([{
        "older_util": older, "middle_util": middle, "current_util": current,
        "older_valid": True, "middle_valid": True, "past_gap_s": 300,
        "current_high": False,
        "any_recent_high": older >= 80 or middle >= 80,
    }])
    pooled = float(model.predict_pooled(frame)[0])
    beta = float(model.predict_beta(frame)[0])
    if not (math.isfinite(pooled) and 0 <= pooled <= 1
            and math.isfinite(beta) and 0 <= beta <= 1):
        raise ValueError("nonfinite_or_out_of_range_frozen_probability")
    return {
        **base,
        "status": "RESEARCH_ESTIMATE",
        "decision_utc_s": request["decision_utc_s"],
        "machine_hash": request["machine_hash"],
        "gpu_index": request["gpu_index"],
        "pooled_probability": pooled,
        "selected_beta_probability": beta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
        result = assess_philly_gpu_risk(request)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"philly_gpu_risk_failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
