"""Check source-declared, observational links for one-VM SCALE_OUT events.

The v0.1.0 validator is deliberately separate. This script never turns a Go
proposal, a POST attempt, or a RUNNING status into an executed scale action.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft7Validator

from validate_scale_action_events import (
    ContractError, _reject_json_constant, _timestamp, _unique_json_object,
)


SCHEMA_VERSION = "0.2.0"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts/scale_action_events_v0.2.0.schema.json"
SECOND = 1_000_000_000


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ContractError("nonfinite_json_number")
    return value


def _entity_key(event: dict) -> tuple:
    kind = event["event_type"]
    identifier = {
        "DECISION": "decision_id",
        "ACTION_ISSUED": "action_id",
        "ACTION_STATUS": "action_id",
        "ACTION_RESULT": "action_id",
        "TRAFFIC_HANDOFF": "action_id",
        "REQUEST_RESULT": "request_id",
    }[kind]
    return (kind, event["logical_service_id"], event["app_id"],
            event["app_version"], event.get("correlation_id"),
            event["data"][identifier])


def _check_event_times(event: dict) -> None:
    event_id = event["event_id"]
    observed = _timestamp(event["observed_at"])
    available = _timestamp(event["available_at"])
    if observed > available:
        raise ContractError(f"{event_id}:event_available_before_observed")
    data = event["data"]
    if event["event_type"] == "DECISION":
        if _timestamp(data["inputs_max_available_at"]) > observed:
            raise ContractError(f"{event_id}:decision_uses_unavailable_input")
        if data["desired_replicas"] != data["current_replicas"] + 1:
            raise ContractError(f"{event_id}:scale_out_must_add_one_replica")
        if len(data["active_deployment_ids_before"]) != data["current_replicas"]:
            raise ContractError(f"{event_id}:one_deployment_per_replica_required")
    if event["event_type"] == "REQUEST_RESULT":
        created = _timestamp(data["created_at"])
        completed = _timestamp(data["completed_at"])
        if created > completed or completed != observed:
            raise ContractError(f"{event_id}:request_time_order_invalid")
        if not math.isclose(data["latency_ms"], (completed - created) / 1_000_000,
                            rel_tol=0, abs_tol=1.0):
            raise ContractError(f"{event_id}:request_latency_inconsistent_with_timestamps")


def read_events(path: Path, *, as_of: str | None = None) -> list[dict]:
    """Validate rows, collapse exact retransmissions, and apply corrections."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema)
    cutoff = _timestamp(as_of) if as_of is not None else None
    ordered: list[dict] = []
    by_id: dict[str, dict] = {}
    root_of: dict[str, str] = {}
    correction_version: dict[str, int] = {}
    corrected_parent: set[str] = set()
    with path.open(encoding="utf-8") as file:
        for line_no, raw in enumerate(file, start=1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw, object_pairs_hook=_unique_json_object,
                                   parse_constant=_reject_json_constant, parse_float=_finite_float)
            except ContractError as error:
                raise ContractError(f"line_{line_no}:{error}") from error
            except (json.JSONDecodeError, ValueError) as error:
                raise ContractError(f"line_{line_no}:invalid_json") from error
            errors = sorted(validator.iter_errors(event), key=lambda e: (list(map(str, e.path)), e.message))
            if errors:
                error = errors[0]
                location = ".".join(map(str, error.path)) or "$"
                raise ContractError(f"line_{line_no}:schema:{location}:{error.message}")
            _check_event_times(event)
            event_id = event["event_id"]
            if event_id in by_id:
                if by_id[event_id] == event:
                    continue
                raise ContractError(f"line_{line_no}:conflicting_event_id:{event_id}")
            parent_id = event.get("correction_of")
            if parent_id is None:
                root_of[event_id] = event_id
                correction_version[event_id] = 0
            else:
                parent = by_id.get(parent_id)
                if parent is None:
                    raise ContractError(f"line_{line_no}:missing_correction_parent:{parent_id}")
                if parent_id in corrected_parent:
                    raise ContractError(f"line_{line_no}:forked_correction:{parent_id}")
                if event["correction_version"] != correction_version[parent_id] + 1:
                    raise ContractError(f"line_{line_no}:correction_version_gap")
                if (_entity_key(event) != _entity_key(parent) or
                        event["source_system"] != parent["source_system"]):
                    raise ContractError(f"line_{line_no}:correction_changes_identity")
                if _timestamp(event["available_at"]) < _timestamp(parent["available_at"]):
                    raise ContractError(f"line_{line_no}:correction_available_before_parent")
                corrected_parent.add(parent_id)
                root_of[event_id] = root_of[parent_id]
                correction_version[event_id] = event["correction_version"]
            ordered.append(event)
            by_id[event_id] = event
    latest: dict[str, dict] = {}
    for event in ordered:
        if cutoff is None or _timestamp(event["available_at"]) <= cutoff:
            latest[root_of[event["event_id"]]] = event
    return list(latest.values())


def _scope_matches(left: dict, right: dict) -> bool:
    return all(left.get(field) == right.get(field) for field in
               ("logical_service_id", "app_id", "app_version", "correlation_id"))


def _one_by_key(events: list[dict], field: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for event in events:
        key = event["data"][field]
        if key in indexed:
            raise ContractError(f"ambiguous_{field}:{key}")
        indexed[key] = event
    return indexed


def _credible_terminal_before(action: dict, result: dict | None,
                              handoff: dict | None, pre_start: int,
                              statuses: list[dict]) -> bool:
    """An earlier command is baseline only with a proved terminal state."""
    if (action["time_provenance"] != "SOURCE_EVENT" or result is None or
            not _scope_matches(action, result) or result["time_provenance"] != "SOURCE_EVENT"):
        return False
    issued_at = _timestamp(action["observed_at"])
    terminal_at = _timestamp(result["observed_at"])
    if not issued_at < terminal_at < pre_start:
        return False
    if any(status["data"]["action_id"] == action["data"]["action_id"] and
           _timestamp(status["observed_at"]) > terminal_at for status in statuses):
        return False
    data = result["data"]
    if (action["data"].get("target_deployment_id") and
            action["data"]["target_deployment_id"] != data.get("target_deployment_id")):
        return False
    if data["state"] == "FAILED":
        return handoff is None
    if (data["readiness_signal"] != "SERVICE_HEALTH_AND_REPLICA_COUNT" or
            data["service_health"] != "HEALTHY" or
            data["target_ready_replicas"] != 1 or
            data["ready_service_replicas"] != action["data"]["desired_replicas"] or
            handoff is None or handoff["time_provenance"] != "SOURCE_EVENT" or
            not _scope_matches(action, handoff)):
        return False
    target = data["target_deployment_id"]
    before = set(action["data"]["active_deployment_ids_before"])
    h = handoff["data"]
    return (target not in before and
            action["data"].get("target_deployment_id", target) == target and
            len(before) + 1 == action["data"]["desired_replicas"] and
            h["target_deployment_id"] == target and
            h["from_routing_version"] == action["data"]["routing_version_before"] and
            h["to_routing_version"] != h["from_routing_version"] and
            set(h["active_deployment_ids_before"]) == before and
            set(h["active_deployment_ids_after"]) == before | {target} and
            terminal_at <= _timestamp(handoff["observed_at"]) < pre_start)


def assess_events(events: list[dict], *, as_of: str | None = None) -> dict:
    """Evaluate event linkage; no self-declared stream proves causality."""
    decisions = _one_by_key([e for e in events if e["event_type"] == "DECISION"], "decision_id")
    actions = _one_by_key([e for e in events if e["event_type"] == "ACTION_ISSUED"], "action_id")
    results = _one_by_key([e for e in events if e["event_type"] == "ACTION_RESULT"], "action_id")
    handoffs = _one_by_key([e for e in events if e["event_type"] == "TRAFFIC_HANDOFF"], "action_id")
    requests = [e for e in events if e["event_type"] == "REQUEST_RESULT"]
    statuses = [e for e in events if e["event_type"] == "ACTION_STATUS"]
    execution_status_ids: set[str] = set()
    status_scope_mismatches: list[dict] = []
    status_target_mismatches: list[dict] = []
    status_order_mismatches: list[dict] = []
    for status in statuses:
        action_id = status["data"]["action_id"]
        action = actions.get(action_id)
        if action is not None:
            if _timestamp(status["observed_at"]) < _timestamp(action["observed_at"]):
                status_order_mismatches.append(status)
            if not _scope_matches(action, status):
                status_scope_mismatches.append(status)
            status_target = status["data"].get("target_deployment_id")
            known_targets = (
                action["data"].get("target_deployment_id"),
                results.get(action_id, {}).get("data", {}).get("target_deployment_id"),
                handoffs.get(action_id, {}).get("data", {}).get("target_deployment_id"),
            )
            if status_target is not None and any(
                target is not None and target != status_target for target in known_targets
            ):
                status_target_mismatches.append(status)
        if status["data"]["status"] in {"PROVISIONING", "DEPLOYING", "RUNNING"}:
            execution_status_ids.add(action_id)
    request_keys: set[tuple[str, str, str, str]] = set()
    for request in requests:
        key = (request["logical_service_id"], request["app_id"], request["app_version"],
               request["data"]["request_id"])
        if key in request_keys:
            raise ContractError(f"ambiguous_request_id:{key[-1]}")
        request_keys.add(key)

    assessments: list[dict] = []
    for decision_id, decision in sorted(decisions.items()):
        d = decision["data"]
        decided_at = _timestamp(decision["observed_at"])
        pre_start = decided_at - d["pre_window_seconds"] * SECOND
        before = set(d["active_deployment_ids_before"])
        reasons: list[str] = []
        pre: list[dict] = []
        post: list[dict] = []
        selected: dict | None = None
        ready: dict | None = None
        handoff: dict | None = None
        if decision["time_provenance"] != "SOURCE_EVENT":
            reasons.append("decision_source_time_unverified")
        for mismatches, reason in (
            (status_scope_mismatches, "action_status_scope_mismatch"),
            (status_target_mismatches, "action_status_target_mismatch"),
            (status_order_mismatches, "action_status_before_issue"),
        ):
            if any(
                status["logical_service_id"] == decision["logical_service_id"] or
                actions[status["data"]["action_id"]]["logical_service_id"] == decision["logical_service_id"]
                for status in mismatches
            ):
                reasons.append(reason)
        attempts = sorted((a for a in actions.values() if a["data"]["decision_id"] == decision_id),
                          key=lambda a: a["data"]["attempt_no"])
        if not attempts:
            reasons.append("missing_action_issued")
        if [a["data"]["attempt_no"] for a in attempts] != list(range(1, len(attempts) + 1)):
            reasons.append("attempt_sequence_invalid")
        if len({(a["data"]["revision"], a["data"]["revision_request_id"]) for a in attempts}) > 1:
            reasons.append("revision_changed_across_retries")
        for index, action in enumerate(attempts):
            a = action["data"]
            action_at = _timestamp(action["observed_at"])
            if not _scope_matches(decision, action):
                reasons.append("action_scope_mismatch")
            if (a["desired_replicas"] != d["desired_replicas"] or
                    set(a["active_deployment_ids_before"]) != before or
                    a["routing_version_before"] != d["routing_version_before"]):
                reasons.append("action_differs_from_decision")
            if a.get("target_deployment_id") in before:
                reasons.append("target_reuses_existing_deployment")
            if a["dispatch_state"] == "REJECTED" and a["action_id"] in execution_status_ids:
                reasons.append("rejected_action_has_execution_evidence")
            if action_at <= decided_at:
                reasons.append("action_not_after_decision")
            if action["time_provenance"] != "SOURCE_EVENT":
                reasons.append("action_source_time_unverified")
            result = results.get(a["action_id"])
            candidate_handoff = handoffs.get(a["action_id"])
            for status in statuses:
                if status["data"]["action_id"] != a["action_id"]:
                    continue
                if not _scope_matches(action, status):
                    reasons.append("action_status_scope_mismatch")
                if (a.get("target_deployment_id") and
                        status["data"].get("target_deployment_id", a["target_deployment_id"]) !=
                        a["target_deployment_id"]):
                    reasons.append("action_status_target_mismatch")
                if _timestamp(status["observed_at"]) <= action_at:
                    reasons.append("action_status_not_after_issue")
                if result is not None and _timestamp(status["observed_at"]) > _timestamp(result["observed_at"]):
                    reasons.append("action_status_after_terminal_result")
            if result is not None:
                r = result["data"]
                if not _scope_matches(action, result):
                    reasons.append("action_result_scope_mismatch")
                if result["time_provenance"] != "SOURCE_EVENT":
                    reasons.append("action_result_source_time_unverified")
                if _timestamp(result["observed_at"]) <= action_at:
                    reasons.append("action_result_not_after_issue")
                if a["dispatch_state"] != "ACCEPTED":
                    reasons.append("unaccepted_action_has_result")
                if a.get("target_deployment_id") and r.get("target_deployment_id") != a["target_deployment_id"]:
                    reasons.append("action_result_target_mismatch")
                if r.get("target_deployment_id") in before:
                    reasons.append("target_reuses_existing_deployment")
            if candidate_handoff is not None:
                if not _scope_matches(action, candidate_handoff):
                    reasons.append("handoff_scope_mismatch")
                if candidate_handoff["time_provenance"] != "SOURCE_EVENT":
                    reasons.append("handoff_source_time_unverified")
                if a["dispatch_state"] != "ACCEPTED" or result is None or result["data"]["state"] != "READY":
                    reasons.append("handoff_without_accepted_ready_action")
            if index == 0:
                if "retry_of_action_id" in a:
                    reasons.append("first_attempt_marked_retry")
            else:
                previous = attempts[index - 1]
                p = previous["data"]
                prior_result = results.get(p["action_id"])
                if action_at <= _timestamp(previous["observed_at"]):
                    reasons.append("retry_not_after_previous_issue")
                if a.get("retry_of_action_id") != p["action_id"]:
                    reasons.append("retry_parent_mismatch")
                prior_failed = (prior_result is not None and prior_result["data"]["state"] == "FAILED" and
                                prior_result["time_provenance"] == "SOURCE_EVENT" and
                                _scope_matches(previous, prior_result) and
                                _timestamp(previous["observed_at"]) < _timestamp(prior_result["observed_at"]) <= action_at and
                                p.get("target_deployment_id", prior_result["data"].get("target_deployment_id")) ==
                                prior_result["data"].get("target_deployment_id"))
                if p["dispatch_state"] != "REJECTED" and not prior_failed:
                    reasons.append("unreconciled_retry")
        ready_attempts = [a for a in attempts if a["data"]["dispatch_state"] == "ACCEPTED" and
                          a["data"]["action_id"] in results and
                          results[a["data"]["action_id"]]["data"]["state"] == "READY"]
        if len(ready_attempts) > 1:
            reasons.append("multiple_ready_actions")
        elif not ready_attempts:
            reasons.append("missing_confirmed_ready_action")
        else:
            selected = ready_attempts[0]
            a = selected["data"]
            ready = results[a["action_id"]]
            r = ready["data"]
            target = r["target_deployment_id"]
            if r["readiness_signal"] != "SERVICE_HEALTH_AND_REPLICA_COUNT" or r["service_health"] != "HEALTHY":
                reasons.append("ready_service_health_evidence_missing")
            if r["ready_service_replicas"] != d["desired_replicas"] or r["target_ready_replicas"] != 1:
                reasons.append("ready_replicas_differ_from_requested")
            if target in before:
                reasons.append("target_reuses_existing_deployment")
            for other in attempts:
                if other is selected or other["data"]["dispatch_state"] != "ACCEPTED":
                    continue
                prior = results.get(other["data"]["action_id"])
                if not (prior and prior["data"]["state"] == "FAILED" and
                        prior["time_provenance"] == "SOURCE_EVENT" and
                        _scope_matches(other, prior) and
                        _timestamp(other["observed_at"]) < _timestamp(prior["observed_at"]) <=
                        _timestamp(selected["observed_at"])):
                    reasons.append("unresolved_accepted_attempt")
            handoff = handoffs.get(a["action_id"])
            if handoff is None:
                reasons.append("missing_source_timed_handoff")
            else:
                h = handoff["data"]
                handoff_at = _timestamp(handoff["observed_at"])
                if handoff_at < _timestamp(ready["observed_at"]):
                    reasons.append("handoff_before_ready")
                if (h["target_deployment_id"] != target or
                        a.get("target_deployment_id", target) != target):
                    reasons.append("handoff_target_mismatch")
                if (h["from_routing_version"] != d["routing_version_before"] or
                        h["to_routing_version"] == h["from_routing_version"] or
                        set(h["active_deployment_ids_before"]) != before):
                    reasons.append("handoff_route_mismatch")
                after = set(h["active_deployment_ids_after"])
                if after != before | {target} or len(after) != d["desired_replicas"]:
                    reasons.append("handoff_active_set_mismatch")

                scoped_requests = [request for request in requests if
                                   request["logical_service_id"] == decision["logical_service_id"] and
                                   request["app_id"] == decision["app_id"] and
                                   request["app_version"] == decision["app_version"] and
                                   request["time_provenance"] == "SOURCE_EVENT"]
                pre_candidates = [request for request in scoped_requests if
                                  pre_start <= _timestamp(request["data"]["created_at"]) and
                                  _timestamp(request["data"]["completed_at"]) < decided_at]
                for request in pre_candidates:
                    q = request["data"]
                    if q["routing_version"] != d["routing_version_before"]:
                        reasons.append("pre_request_route_mismatch")
                    if q["served_deployment_id"] not in before:
                        reasons.append("pre_request_deployment_mismatch")
                    if (_timestamp(request["available_at"]) <= decided_at and
                            q["routing_version"] == d["routing_version_before"] and
                            q["served_deployment_id"] in before):
                        pre.append(request)
                if not pre:
                    reasons.append("missing_pre_action_request_results")
                    if any(_timestamp(request["available_at"]) > decided_at for request in pre_candidates):
                        reasons.append("pre_request_unavailable_at_decision")

                post_end = handoff_at + d["post_window_seconds"] * SECOND
                if as_of is not None and _timestamp(as_of) < post_end:
                    reasons.append("post_window_not_closed_as_of")
                post_candidates = [request for request in scoped_requests if
                                   _timestamp(request["data"]["created_at"]) > handoff_at and
                                   _timestamp(request["data"]["completed_at"]) <= post_end]
                for request in post_candidates:
                    q = request["data"]
                    if q["routing_version"] != h["to_routing_version"]:
                        reasons.append("post_request_route_mismatch")
                    if q["served_deployment_id"] not in after:
                        reasons.append("post_request_deployment_mismatch")
                    if q["routing_version"] == h["to_routing_version"] and q["served_deployment_id"] in after:
                        post.append(request)
                if not post:
                    reasons.append("missing_post_handoff_request_results")
                if pre and post:
                    before_classes = {request["data"]["workload_class"] for request in pre}
                    target_classes = {request["data"]["workload_class"] for request in post if
                                      request["data"]["served_deployment_id"] == target}
                    if not before_classes.intersection(target_classes):
                        reasons.append("missing_comparable_target_request")
                if post:
                    for other in actions.values():
                        if other["data"]["decision_id"] == decision_id or other["logical_service_id"] != decision["logical_service_id"]:
                            continue
                        issued_at = _timestamp(other["observed_at"])
                        other_id = other["data"]["action_id"]
                        if other["data"]["dispatch_state"] == "REJECTED":
                            if issued_at <= post_end and (other_id in results or other_id in handoffs or
                                                          other_id in execution_status_ids):
                                reasons.append("rejected_action_has_execution_evidence")
                            continue
                        if issued_at <= post_end and (
                            issued_at >= pre_start or not _credible_terminal_before(
                                other, results.get(other_id), handoffs.get(other_id),
                                pre_start, statuses)):
                            reasons.append("overlapping_accepted_or_unknown_action")
                    for other in handoffs.values():
                        if other is handoff or other["logical_service_id"] != decision["logical_service_id"]:
                            continue
                        if pre_start <= _timestamp(other["observed_at"]) <= post_end:
                            reasons.append("overlapping_traffic_handoff")
                    for result in results.values():
                        if (result["logical_service_id"] == decision["logical_service_id"] and
                                result["data"]["action_id"] not in actions and
                                pre_start <= _timestamp(result["observed_at"]) <= post_end):
                            reasons.append("unattributed_action_result_in_window")
                    for status in statuses:
                        if (status["logical_service_id"] == decision["logical_service_id"] and
                                status["data"]["action_id"] not in actions and
                                pre_start <= _timestamp(status["observed_at"]) <= post_end):
                            reasons.append("unattributed_action_status_in_window")
                    # An app-version change on the same logical service is a
                    # different workload, even if it has no visible action row.
                    for request in requests:
                        if (request["logical_service_id"] == decision["logical_service_id"] and
                                (request["app_id"], request["app_version"]) !=
                                (decision["app_id"], decision["app_version"]) and
                                pre_start <= _timestamp(request["data"]["created_at"]) <= post_end):
                            reasons.append("service_app_version_changed_in_window")

        unique_reasons = sorted(set(reasons))
        causal_reasons = ["comparison_assignment_not_in_contract", "concurrent_demand_context_not_in_contract"]
        if unique_reasons:
            causal_reasons.insert(0, "linked_outcome_ineligible")
        assessments.append({
            "decision_id": decision_id,
            "logical_service_id": decision["logical_service_id"],
            "action": "SCALE_OUT",
            "selected_action_id": selected["data"]["action_id"] if selected else None,
            "target_deployment_id": ready["data"]["target_deployment_id"] if ready else None,
            "pre_request_count": len(pre),
            "post_request_count": len(post),
            "linked_outcome_eligible": not unique_reasons,
            "linked_outcome_reasons": unique_reasons,
            "source_authenticity_verified": False,
            "event_stream_completeness_verified": False,
            "causal_effect_eligible": False,
            "causal_effect_reasons": causal_reasons,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "scope": "source_declared_observational_linkage_only",
        "decision_count": len(assessments),
        "orphan_action_ids": sorted(aid for aid, action in actions.items() if action["data"]["decision_id"] not in decisions),
        "orphan_result_action_ids": sorted(aid for aid in results if aid not in actions),
        "orphan_handoff_action_ids": sorted(aid for aid in handoffs if aid not in actions),
        "orphan_status_action_ids": sorted({s["data"]["action_id"] for s in statuses if s["data"]["action_id"] not in actions}),
        "decisions": assessments,
    }


def validate_file(path: Path, *, as_of: str | None = None) -> dict:
    return assess_events(read_events(path, as_of=as_of), as_of=as_of)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="v0.2.0 research event JSONL")
    parser.add_argument("--as-of", help="RFC3339 availability cutoff for retrospective replay")
    parser.add_argument("--output", type=Path, help="write assessment JSON instead of stdout")
    args = parser.parse_args()
    try:
        report = validate_file(args.input, as_of=args.as_of)
    except (ContractError, OSError) as error:
        print(f"scale_action_events_v0_2_invalid: {error}", file=sys.stderr)
        return 2
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
