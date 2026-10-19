"""Bind one verified Philly risk assessment to versioned research evidence.

This transformer checks the request/result pair. Only the one-step generator
can establish that the assessor verified its artifacts in the same process.
The evidence never represents a VM shape or a scaling action.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

import assess_philly_gpu_risk as risk


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts/philly_gpu_risk_evidence_v0.1.0.schema.json"
EVIDENCE_TYPE = "khu.research.philly_gpu_risk_evidence"
SCHEMA_VERSION = "0.1.0"
EXPECTED_NORMALIZED_SOURCE_SHA256 = (
    "15f1af4669acb9823d0e6d96291e799cf33f8a9c1813d4faf51b0b9eb618ad58"
)
PROVENANCE_FIELDS = {
    "model_spec_sha256", "normalized_source_sha256", "frozen_code_sha256",
    "api_code_sha256", "artifact_hashes_verified_at_load",
    "window_metadata_provenance",
}
BASE_RESULT_FIELDS = {
    "event", "horizon_seconds", "source_domain", "model_name",
    "baseline_name", "input_observation_verified", "scope", "provenance",
    "status", "pooled_probability", "selected_beta_probability",
    "assessed_request_sha256",
}
ESTIMATE_RESULT_FIELDS = BASE_RESULT_FIELDS | {
    "decision_utc_s", "machine_hash", "gpu_index",
}
ABSTAIN_RESULT_FIELDS = BASE_RESULT_FIELDS | {"reasons"}
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("non_json_or_nonfinite_input") from exc


def _same_json_value(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (actual.keys() == expected.keys()
                and all(_same_json_value(actual[key], value)
                        for key, value in expected.items()))
    if type(expected) is list:
        return (len(actual) == len(expected)
                and all(_same_json_value(a, b) for a, b in zip(actual, expected)))
    return actual == expected


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise ValueError("generated_at_must_be_rfc3339_utc_z")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("generated_at_must_be_rfc3339_utc_z") from exc
    return value


def _checked_provenance(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != PROVENANCE_FIELDS:
        raise ValueError("invalid_assessor_provenance_fields")
    if (value["model_spec_sha256"] != risk.MODEL_SPEC_SHA256
            or value["normalized_source_sha256"] != EXPECTED_NORMALIZED_SOURCE_SHA256
            or value["artifact_hashes_verified_at_load"] is not True
            or value["window_metadata_provenance"] != "caller_declared"
            or value["api_code_sha256"] != risk.sha256(Path(risk.__file__))):
        raise ValueError("unexpected_assessor_provenance")
    if risk.sha256(risk.MODEL_SPEC) != risk.MODEL_SPEC_SHA256:
        raise ValueError("model_spec_sha256_mismatch")
    spec = json.loads(risk.MODEL_SPEC.read_text(encoding="utf-8"))
    if (spec.get("original_normalized_source_sha256")
            != EXPECTED_NORMALIZED_SOURCE_SHA256
            or not _same_json_value(value["frozen_code_sha256"],
                                    spec.get("code_sha256"))):
        raise ValueError("frozen_code_provenance_mismatch")
    return copy.deepcopy(value)


def _checked_probability(value: Any) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid_risk_probability")
    try:
        valid = math.isfinite(value) and 0 <= value <= 1
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("invalid_risk_probability")
    return float(value)


def _valid_decision_context(request: Any) -> dict | None:
    """Preserve only well-formed declared identity, including on ABSTAIN."""
    if not isinstance(request, dict) or request.get("source_domain") != risk.SOURCE_DOMAIN:
        return None
    machine = request.get("machine_hash")
    slots = request.get("expected_gpu_slots")
    gpu = request.get("gpu_index")
    decision = request.get("decision_utc_s")
    if (not isinstance(machine, str) or not risk.MACHINE_HASH_RE.fullmatch(machine)
            or type(slots) is not int or slots not in (2, 4, 8)
            or type(gpu) is not int or not 0 <= gpu < slots
            or not risk._positive_aligned_second(decision)):
        return None
    return {
        "decision_utc_s": decision,
        "machine_hash": machine,
        "gpu_index": gpu,
        "expected_gpu_slots": slots,
    }


def build_evidence(request: Any, result: Any, *,
                   generated_at: str | None = None) -> dict:
    """Validate the raw assessor pair and preserve its research-only meaning."""
    request_sha = hashlib.sha256(_canonical_bytes(request)).hexdigest()
    _canonical_bytes(result)
    if not isinstance(result, dict):
        raise ValueError("assessor_result_must_be_object")
    if (type(result.get("assessed_request_sha256")) is not str
            or result["assessed_request_sha256"] != request_sha):
        raise ValueError("assessor_request_digest_mismatch")
    if (result.get("status") not in ("RESEARCH_ESTIMATE", "ABSTAIN")
            or result.get("event") != risk.EVENT
            or type(result.get("horizon_seconds")) is not int
            or result["horizon_seconds"] != 1800
            or result.get("source_domain") != risk.SOURCE_DOMAIN
            or result.get("model_name") != "pooled_quality_logistic"
            or result.get("baseline_name") != "state_recent_beta"
            or result.get("input_observation_verified") is not False
            or not _same_json_value(result.get("scope"), risk.SCOPE)):
        raise ValueError("unsupported_assessor_result")
    provenance = _checked_provenance(result.get("provenance"))
    at = _generated_at(generated_at)
    evidence = {
        "evidence_type": EVIDENCE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "evidence_id": "sha256:" + hashlib.sha256(_canonical_bytes({
            "request_sha256": request_sha, "result": result,
        })).hexdigest(),
        "request_sha256": request_sha,
        "generated_at": at,
        "status": result["status"],
        "event": result["event"],
        "horizon_seconds": result["horizon_seconds"],
        "source_domain": result["source_domain"],
        "model_name": result["model_name"],
        "baseline_name": result["baseline_name"],
        "input_observation_verified": False,
        "scope": copy.deepcopy(result["scope"]),
        "provenance": provenance,
    }
    errors = risk._request_errors(request)
    decision_context = _valid_decision_context(request)
    if decision_context is not None:
        generated_second = int(datetime.fromisoformat(
            at.replace("Z", "+00:00")).timestamp())
        if decision_context["decision_utc_s"] > generated_second:
            raise ValueError("evidence_generated_before_decision")
    if result["status"] == "RESEARCH_ESTIMATE":
        if set(result) != ESTIMATE_RESULT_FIELDS or errors or decision_context is None:
            raise ValueError("estimate_result_or_request_mismatch")
        for name in ("decision_utc_s", "machine_hash", "gpu_index"):
            if not _same_json_value(result[name], request[name]):
                raise ValueError("assessor_decision_identity_mismatch")
        evidence["decision_context"] = decision_context
        evidence["pooled_probability"] = _checked_probability(
            result["pooled_probability"])
        evidence["selected_beta_probability"] = _checked_probability(
            result["selected_beta_probability"])
    else:
        if (set(result) != ABSTAIN_RESULT_FIELDS or not errors
                or not _same_json_value(result.get("reasons"), errors)
                or result.get("pooled_probability") is not None
                or result.get("selected_beta_probability") is not None):
            raise ValueError("abstain_result_or_request_mismatch")
        evidence["reasons"] = list(errors)
        evidence["pooled_probability"] = None
        evidence["selected_beta_probability"] = None
        if decision_context is not None:
            evidence["decision_context"] = decision_context
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    violations = list(Draft7Validator(
        schema, format_checker=FormatChecker()).iter_errors(evidence))
    if violations:
        raise ValueError("evidence_schema_validation_failed:" + violations[0].message)
    return evidence
