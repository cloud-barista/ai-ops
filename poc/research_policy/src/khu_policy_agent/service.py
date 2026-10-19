"""Dependency-light HTTP service for guarded KHU policy inference."""
from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from time import perf_counter
from typing import Any, Mapping

from .contracts import (
    POLICY_SCHEMA_VERSION,
    application_view,
    candidate_feasible,
    candidate_view,
    feature_row,
    mapping,
    operational_view,
)
from .learning import PolicyModel, load_policy


EVALUATE_PATH = "/api/v1/khu-policy/evaluate"
HEALTH_PATH = "/healthz"


class PolicyInputError(ValueError):
    pass


def evaluate_request(model: PolicyModel, request: Mapping[str, Any]) -> dict[str, Any]:
    profile = mapping(request.get("application_profile"))
    if not profile:
        raise PolicyInputError("application_profile is required")
    application = application_view(profile)
    candidates = _candidates(request)
    if not candidates:
        raise PolicyInputError("resource_recommendation must contain at least one candidate")
    phase = str(request.get("phase") or "planning").lower()
    if phase not in {"planning", "operation"}:
        raise PolicyInputError("phase must be planning or operation")
    deployment_context = mapping(request.get("deployment_context"))
    operation_input = {
        **deployment_context,
        "optimization_feedback": request.get("optimization_feedback", deployment_context.get("optimization_feedback", {})),
    }
    operation = operational_view(operation_input)
    actions = _allowed_actions(application, operation, phase, deployment_context)
    evaluated: list[dict[str, Any]] = []
    for candidate in candidates:
        feasible = candidate_feasible(application, candidate)
        for action, desired_replicas in actions:
            features = feature_row(application, candidate, operation, action, desired_replicas)
            if feasible:
                prediction = model.predict(features)
            else:
                prediction = {
                    "expected_utility": 0.0,
                    "ensemble_stddev": 0.0,
                    "confidence": 0.0,
                    "ood_ratio": 1.0,
                    "warnings": ["hard_constraint_infeasible"],
                }
            evaluated.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "action": action,
                    "desired_replicas": desired_replicas,
                    "feasible": feasible,
                    **prediction,
                }
            )
    viable = [item for item in evaluated if item["feasible"]]
    viable.sort(key=lambda item: (-float(item["expected_utility"]), -float(item["confidence"]), item["candidate_id"], item["action"]))
    top = viable[0] if viable else None
    guard = mapping(model.manifest.get("guard"))
    min_confidence = float(guard.get("minimum_confidence", 0.70))
    max_ood = float(guard.get("maximum_ood_ratio", 0.20))
    abstain_reason = ""
    if top is None:
        abstain_reason = "no_hard_feasible_candidate"
    elif float(top["confidence"]) < min_confidence:
        abstain_reason = "confidence_below_threshold"
    elif float(top["ood_ratio"]) > max_ood:
        abstain_reason = "out_of_distribution"
    proposal = {
        "action": "ABSTAIN" if abstain_reason else top["action"],
        "selected_candidate_id": "" if top is None else top["candidate_id"],
        "desired_replicas": None if top is None else top["desired_replicas"],
        "expected_utility": 0.0 if top is None else top["expected_utility"],
        "confidence": 0.0 if top is None else top["confidence"],
        "ood_ratio": 1.0 if top is None else top["ood_ratio"],
        "abstain_reason": abstain_reason,
    }
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "mode": "shadow",
        "phase": phase,
        "model_version": model.manifest.get("model_version"),
        "dataset_sha256": model.manifest.get("dataset_sha256"),
        "artifact_sha256": model.manifest.get("artifact_sha256"),
        "proposal": proposal,
        "candidate_evaluations": evaluated,
        "warnings": ([] if not abstain_reason else [abstain_reason]),
    }


def _candidates(request: Mapping[str, Any]) -> list[Any]:
    recommendation = mapping(request.get("resource_recommendation"))
    raw = recommendation.get("recommendations") or recommendation.get("candidates") or request.get("resource_candidates") or []
    if not isinstance(raw, list):
        return []
    return [candidate_view(mapping(item), index) for index, item in enumerate(raw) if isinstance(item, Mapping)]


def _allowed_actions(application: Any, operation: Mapping[str, float | str], phase: str, context: Mapping[str, Any]) -> list[tuple[str, int]]:
    current = int(float(operation["current_replicas"]))
    minimum = int(application.replicas_min)
    maximum = max(minimum, int(application.replicas_max))
    if phase == "planning":
        has_deployment = bool(context.get("deployment_ref") or context.get("current_spec_digest"))
        if not has_deployment:
            return [("DEPLOY", minimum)]
        if bool(operation["spec_drift"]):
            return [("UPDATE", max(minimum, current)), ("KEEP", max(minimum, current))]
        return [("KEEP", max(minimum, current))]
    latency_violation = float(operation["latency_p95_ms"]) > application.latency_slo_ms
    throughput_violation = 0 < float(operation["throughput_rps"]) < application.throughput_slo_rps
    error_violation = float(operation["error_rate_percent"]) >= 5.0
    if not bool(operation["cooldown_active"]) and (latency_violation or throughput_violation or error_violation) and current < maximum:
        return [("SCALE_OUT", max(minimum, current + 1)), ("KEEP", max(minimum, current))]
    low_cpu = float(operation["cpu_average_percent"]) < 30.0
    low_gpu = float(operation["gpu_average_percent"]) < 30.0
    if not bool(operation["cooldown_active"]) and current > minimum and low_cpu and low_gpu:
        return [("SCALE_IN", current - 1), ("KEEP", current)]
    return [("KEEP", max(minimum, current))]


def serve(*, host: str, port: int, artifact_dir: str) -> None:
    model = load_policy(artifact_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != HEALTH_PATH:
                self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._write(HTTPStatus.OK, {"status": "ok", "model_version": model.manifest.get("model_version")})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != EVALUATE_PATH:
                self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4 * 1024 * 1024:
                    raise PolicyInputError("request body must be between 1 byte and 4 MiB")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise PolicyInputError("request must be a JSON object")
                started = perf_counter()
                result = evaluate_request(model, payload)
                result["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                self._write(HTTPStatus.OK, result)
            except (UnicodeDecodeError, json.JSONDecodeError, PolicyInputError, ValueError) as exc:
                self._write(HTTPStatus.BAD_REQUEST, {"schema_version": POLICY_SCHEMA_VERSION, "error": "invalid_request", "message": str(exc)})
            except Exception as exc:  # keep model errors inside the service boundary
                self._write(HTTPStatus.INTERNAL_SERVER_ERROR, {"schema_version": POLICY_SCHEMA_VERSION, "error": "policy_unavailable", "message": type(exc).__name__})

        def _write(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
