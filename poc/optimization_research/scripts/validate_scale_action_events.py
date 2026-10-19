"""Validate a proposed research action-event JSONL stream without platform side effects.

The JSON Schema checks each record. Cross-record checks below establish only
observational linkage. No field in this draft establishes a causal effect.
"""
from __future__ import annotations

import argparse
from calendar import timegm
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts/scale_action_events_v0.1.0.schema.json"
SCHEMA_VERSION = "0.1.0"
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,9}))?(?:Z|[+-](?:0\d|1\d|2[0-3]):[0-5]\d)$")
_NANOS_PER_SECOND = 1_000_000_000


class ContractError(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ContractError(f"nonfinite_json_number:{value}")


def _timestamp(value: str) -> int:
    """Return exact UTC nanoseconds; datetime alone truncates RFC3339Nano."""
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ContractError("timestamp_must_be_rfc3339_with_timezone")
    try:
        # Python releases before 3.11 parse at most six fractional digits.
        # Validate the wall-clock second separately, then add all nine digits.
        normalized = value
        if match.group(1):
            normalized = value[:match.start(1) - 1] + value[match.end(1):]
        result = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("invalid_timestamp") from error
    if result.tzinfo is None:
        raise ContractError("timestamp_timezone_required")
    seconds = timegm(result.astimezone(timezone.utc).replace(microsecond=0).timetuple())
    fraction = match.group(1) or ""
    return seconds * _NANOS_PER_SECOND + int(fraction.ljust(9, "0")) if fraction else seconds * _NANOS_PER_SECOND


def _schema_errors(value: Any, rule: dict, schema: dict, path: str = "$") -> list[str]:
    """Implement the finite Draft-7 keyword subset used by this contract."""
    if "$ref" in rule:
        prefix = "#/definitions/"
        ref = rule["$ref"]
        if not ref.startswith(prefix):
            raise ContractError("unsupported_schema_reference")
        return _schema_errors(value, schema["definitions"][ref[len(prefix):]], schema, path)

    errors: list[str] = []
    expected = rule.get("type")
    type_ok = {
        "object": lambda x: isinstance(x, dict),
        "string": lambda x: isinstance(x, str),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
        "number": lambda x: (isinstance(x, int) and not isinstance(x, bool)) or
        (isinstance(x, float) and math.isfinite(x)),
        "boolean": lambda x: isinstance(x, bool),
    }
    if expected is not None and not type_ok[expected](value):
        return [f"{path}:expected_{expected}"]
    if "const" in rule and value != rule["const"]:
        errors.append(f"{path}:unexpected_value")
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{path}:unsupported_value")
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0):
            errors.append(f"{path}:empty_string")
        if "pattern" in rule and re.search(rule["pattern"], value) is None:
            errors.append(f"{path}:pattern_mismatch")
        if rule.get("format") == "date-time":
            try:
                _timestamp(value)
            except ContractError:
                errors.append(f"{path}:invalid_timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{path}:below_minimum")
        if "maximum" in rule and value > rule["maximum"]:
            errors.append(f"{path}:above_maximum")
    if isinstance(value, dict):
        for name in rule.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}:required")
        fields = rule.get("properties", {})
        for name, item in value.items():
            if name in fields:
                errors.extend(_schema_errors(item, fields[name], schema, f"{path}.{name}"))
            elif rule.get("additionalProperties") is False:
                errors.append(f"{path}.{name}:unknown_field")
        for name, required in rule.get("dependencies", {}).items():
            if name in value:
                for dependency in required:
                    if dependency not in value:
                        errors.append(f"{path}.{dependency}:required_with_{name}")
    if "oneOf" in rule:
        branch_errors = [_schema_errors(value, branch, schema, path) for branch in rule["oneOf"]]
        passed = sum(not problems for problems in branch_errors)
        if passed != 1:
            if passed == 0:
                errors.extend(min(branch_errors, key=len)[:3])
            errors.append(f"{path}:one_of_{passed}_matched")
    if "anyOf" in rule and not any(
        not _schema_errors(value, branch, schema, path) for branch in rule["anyOf"]
    ):
        errors.append(f"{path}:no_alternative_matched")
    if "not" in rule and not _schema_errors(value, rule["not"], schema, path):
        errors.append(f"{path}:forbidden_combination")
    return errors


def _entity_key(event: dict) -> tuple:
    kind = event["event_type"]
    identifier = {
        "DECISION": "decision_id",
        "ACTION_ISSUED": "action_id",
        "ACTION_RESULT": "action_id",
        "REQUEST_RESULT": "request_id",
    }[kind]
    return (
        kind, event["app_id"], event["app_version"],
        event.get("correlation_id") if kind != "REQUEST_RESULT" else None,
        event["data"][identifier],
    )


def _check_event_times(event: dict) -> None:
    event_id = event["event_id"]
    observed = _timestamp(event["observed_at"])
    available = _timestamp(event["available_at"])
    if observed > available:
        raise ContractError(f"{event_id}:event_available_before_observed")
    data = event["data"]
    kind = event["event_type"]
    if kind == "DECISION":
        if _timestamp(data["inputs_max_available_at"]) > observed:
            raise ContractError(f"{event_id}:decision_uses_unavailable_input")
        if data["action"] in ("DEPLOY", "UPDATE", "SCALE_OUT", "SCALE_IN") and "desired_replicas" not in data:
            raise ContractError(f"{event_id}:desired_replicas_required")
        if data["action"] in ("UPDATE", "SCALE_OUT", "SCALE_IN") and (
            "deployment_id" not in data or "current_replicas" not in data
        ):
            raise ContractError(f"{event_id}:existing_deployment_and_replicas_required")
        if data["action"] == "DEPLOY" and data["desired_replicas"] < 1:
            raise ContractError(f"{event_id}:deploy_requires_positive_replicas")
        if data["action"] == "SCALE_OUT" and data["desired_replicas"] <= data["current_replicas"]:
            raise ContractError(f"{event_id}:scale_out_must_increase_replicas")
        if data["action"] == "SCALE_IN" and data["desired_replicas"] >= data["current_replicas"]:
            raise ContractError(f"{event_id}:scale_in_must_decrease_replicas")
    if kind == "REQUEST_RESULT":
        created = _timestamp(data["created_at"])
        completed = _timestamp(data["completed_at"])
        if created > completed or completed != observed:
            raise ContractError(f"{event_id}:request_time_order_invalid")
        elapsed_ms = (completed - created) / 1_000_000
        if not math.isclose(data["latency_ms"], elapsed_ms, rel_tol=0, abs_tol=1.0):
            raise ContractError(f"{event_id}:request_latency_inconsistent_with_timestamps")


def read_events(path: Path, *, as_of: str | None = None) -> list[dict]:
    """Read, deduplicate exact retransmissions, and apply append-only corrections."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
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
                                   parse_constant=_reject_json_constant)
            except ContractError as error:
                raise ContractError(f"line_{line_no}:{error}") from error
            except (json.JSONDecodeError, ValueError) as error:
                raise ContractError(f"line_{line_no}:invalid_json") from error
            errors = _schema_errors(event, schema, schema)
            if errors:
                raise ContractError(f"line_{line_no}:{';'.join(errors[:4])}")
            _check_event_times(event)
            event_id = event["event_id"]
            if event_id in by_id:
                if event == by_id[event_id]:
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
                if _entity_key(event) != _entity_key(parent) or event["source_system"] != parent["source_system"]:
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
    return all(left.get(key) == right.get(key) for key in
               ("app_id", "app_version", "correlation_id"))


def _one_by_key(events: list[dict], field: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for event in events:
        key = event["data"][field]
        if key in result:
            raise ContractError(f"ambiguous_{field}:{key}")
        result[key] = event
    return result


def _other_action_overlaps_window(
    action: dict, result: dict | None, *, pre_start: int, last_post: int
) -> bool:
    """Treat an accepted command as active until a credible terminal result.

    A command with a credible terminal result before the declared pre window
    is treated as part of the baseline state. A missing, local-time, or
    inconsistent result does not establish that it finished before the window.
    """
    issued_at = _timestamp(action["observed_at"])
    if issued_at > last_post:
        return False
    if (result is None or result["time_provenance"] != "SOURCE_EVENT" or
            not _scope_matches(action, result) or
            (action["data"].get("deployment_id") and
             result["data"]["deployment_id"] != action["data"]["deployment_id"])):
        return True
    # A source-timed RUNNING/status observation is not proof that an accepted
    # command finished. A READY result resolves the action only with the same
    # service-health and applied-replica evidence required for the selected
    # action. Otherwise an old status row can falsely clear a concurrent action.
    if (result["data"]["state"] == "READY" and
            (result["data"]["readiness_signal"] != "SERVICE_HEALTH_AND_REPLICA_COUNT" or
             result["data"]["ready_replicas"] != action["data"]["desired_replicas"])):
        return True
    terminal_at = _timestamp(result["observed_at"])
    if terminal_at < issued_at:
        return True
    return terminal_at >= pre_start


def assess_events(events: list[dict], *, as_of: str | None = None) -> dict:
    """Assess source-declared linkage; causal_effect_eligible is fail-closed."""
    decisions = _one_by_key([e for e in events if e["event_type"] == "DECISION"], "decision_id")
    actions = _one_by_key([e for e in events if e["event_type"] == "ACTION_ISSUED"], "action_id")
    results = _one_by_key([e for e in events if e["event_type"] == "ACTION_RESULT"], "action_id")
    requests = [e for e in events if e["event_type"] == "REQUEST_RESULT"]
    request_keys: set[tuple[str, str, str]] = set()
    for event in requests:
        key = (event["app_id"], event["app_version"], event["data"]["request_id"])
        if key in request_keys:
            raise ContractError(f"ambiguous_request_id:{key[2]}")
        request_keys.add(key)

    assessments: list[dict] = []
    for decision_id, decision in sorted(decisions.items()):
        data = decision["data"]
        action_kind = data["action"]
        decided_at = _timestamp(decision["observed_at"])
        reasons: list[str] = []
        selected: dict | None = None
        ready: dict | None = None
        pre: list[dict] = []
        post: list[dict] = []
        attempts = sorted((a for a in actions.values() if a["data"]["decision_id"] == decision_id),
                          key=lambda a: a["data"]["attempt_no"])
        if action_kind in ("KEEP", "ABSTAIN"):
            reasons.append("no_action_by_design")
            if attempts:
                reasons.append("action_for_nonexecuting_decision")
        else:
            if decision["time_provenance"] != "SOURCE_EVENT":
                reasons.append("decision_source_time_unverified")
            if not attempts:
                reasons.append("missing_action_issued")
            if [a["data"]["attempt_no"] for a in attempts] != list(range(1, len(attempts) + 1)):
                reasons.append("attempt_sequence_invalid")
            if len({(a["data"]["revision"], a["data"]["revision_request_id"])
                    for a in attempts}) > 1:
                reasons.append("revision_changed_across_retries")
            for index, action in enumerate(attempts):
                issued = action["data"]
                if not _scope_matches(decision, action):
                    reasons.append("action_scope_mismatch")
                if issued["action"] != action_kind or issued["desired_replicas"] != data["desired_replicas"]:
                    reasons.append("action_differs_from_decision")
                if _timestamp(action["observed_at"]) < decided_at:
                    reasons.append("action_issued_before_decision")
                if action["time_provenance"] != "SOURCE_EVENT":
                    reasons.append("action_source_time_unverified")
                if data.get("deployment_id") and issued.get("deployment_id") != data["deployment_id"]:
                    reasons.append("action_deployment_mismatch")
                action_result = results.get(issued["action_id"])
                if action_result is not None:
                    if not _scope_matches(action, action_result):
                        reasons.append("action_result_scope_mismatch")
                    if issued.get("deployment_id") and action_result["data"]["deployment_id"] != issued["deployment_id"]:
                        reasons.append("action_result_deployment_mismatch")
                    if data.get("deployment_id") and action_result["data"]["deployment_id"] != data["deployment_id"]:
                        reasons.append("action_result_deployment_mismatch")
                    if _timestamp(action_result["observed_at"]) < _timestamp(action["observed_at"]):
                        reasons.append("action_result_before_issue")
                    if action_result["time_provenance"] != "SOURCE_EVENT":
                        reasons.append("action_result_source_time_unverified")
                    if issued["dispatch_state"] == "REJECTED":
                        reasons.append("rejected_action_has_result")
                if index == 0:
                    if "retry_of_action_id" in issued:
                        reasons.append("first_attempt_marked_retry")
                else:
                    previous = attempts[index - 1]
                    previous_result = results.get(previous["data"]["action_id"])
                    if _timestamp(action["observed_at"]) <= _timestamp(previous["observed_at"]):
                        reasons.append("retry_not_after_previous_issue")
                    if issued.get("retry_of_action_id") != previous["data"]["action_id"]:
                        reasons.append("retry_parent_mismatch")
                    if not (previous["data"]["dispatch_state"] == "REJECTED" or
                            (previous_result and previous_result["data"]["state"] == "FAILED" and
                             previous_result["time_provenance"] == "SOURCE_EVENT" and
                             _scope_matches(previous, previous_result) and
                             (not previous["data"].get("deployment_id") or previous_result["data"]["deployment_id"] == previous["data"]["deployment_id"]) and
                             _timestamp(previous_result["observed_at"]) >= _timestamp(previous["observed_at"]) and
                             _timestamp(previous_result["observed_at"]) <= _timestamp(action["observed_at"]))):
                        reasons.append("unreconciled_retry")
            ready_attempts = [a for a in attempts if
                              a["data"]["dispatch_state"] == "ACCEPTED" and
                              a["data"]["action_id"] in results and
                              results[a["data"]["action_id"]]["data"]["state"] == "READY"]
            if len(ready_attempts) > 1:
                reasons.append("multiple_ready_actions")
            elif len(ready_attempts) == 0:
                reasons.append("missing_confirmed_ready_action")
            else:
                selected = ready_attempts[0]
                ready = results[selected["data"]["action_id"]]
                issued = selected["data"]
                ready_data = ready["data"]
                # Another accepted attempt is safe to ignore only if it has an
                # explicit terminal failure before this attempt was issued.
                for other_attempt in attempts:
                    if other_attempt["data"]["dispatch_state"] != "ACCEPTED" or other_attempt is selected:
                        continue
                    other_result = results.get(other_attempt["data"]["action_id"])
                    if not (other_result and other_result["data"]["state"] == "FAILED" and
                            _timestamp(other_result["observed_at"]) <= _timestamp(selected["observed_at"])):
                        reasons.append("unresolved_accepted_attempt")
                if not _scope_matches(decision, ready):
                    reasons.append("ready_scope_mismatch")
                if (ready_data["deployment_id"] != issued.get("deployment_id", ready_data["deployment_id"]) or
                        ready_data["deployment_id"] != data.get("deployment_id", ready_data["deployment_id"])):
                    reasons.append("ready_deployment_mismatch")
                if _timestamp(ready["observed_at"]) < _timestamp(selected["observed_at"]):
                    reasons.append("ready_before_action_issued")
                if ready["time_provenance"] != "SOURCE_EVENT":
                    reasons.append("ready_source_time_unverified")
                if ready_data["readiness_signal"] != "SERVICE_HEALTH_AND_REPLICA_COUNT":
                    reasons.append("ready_replica_evidence_missing")
                if ready_data["ready_replicas"] != issued["desired_replicas"]:
                    reasons.append("ready_replicas_differ_from_requested")

                deployment_id = ready_data["deployment_id"]
                scoped = [r for r in requests if
                          r["app_id"] == decision["app_id"] and
                          r["app_version"] == decision["app_version"] and
                          r["data"]["deployment_id"] == deployment_id and
                          r["time_provenance"] == "SOURCE_EVENT"]
                pre_start = decided_at - data["pre_window_seconds"] * _NANOS_PER_SECOND
                post_end = _timestamp(ready["observed_at"]) + data["post_window_seconds"] * _NANOS_PER_SECOND
                pre = [r for r in scoped if
                       pre_start <= _timestamp(r["data"]["created_at"]) and
                       _timestamp(r["data"]["completed_at"]) < decided_at and
                       _timestamp(r["available_at"]) <= decided_at]
                post = [r for r in scoped if
                        _timestamp(r["data"]["created_at"]) >= _timestamp(ready["observed_at"]) and
                        _timestamp(r["data"]["completed_at"]) <= post_end]
                if action_kind != "DEPLOY" and not pre:
                    reasons.append("missing_pre_action_request_results")
                if not post:
                    reasons.append("missing_post_ready_request_results")
                if pre and post:
                    before_classes = {r["data"]["workload_class"] for r in pre}
                    after_classes = {r["data"]["workload_class"] for r in post}
                    if not before_classes.intersection(after_classes):
                        reasons.append("no_common_request_class")
                if post:
                    last_post = max(_timestamp(r["data"]["completed_at"]) for r in post)
                    for other in actions.values():
                        if other["data"]["action_id"] == issued["action_id"]:
                            continue
                        if other["data"]["decision_id"] == decision_id:
                            # Same-decision retries are checked against their
                            # terminal result above.
                            continue
                        if other["app_id"] != decision["app_id"] or other["app_version"] != decision["app_version"]:
                            continue
                        # The contract has no traffic-routing or isolation key:
                        # another deployment of the same app/version may still
                        # change the evaluated request mix or shared resources.
                        if (other["data"]["dispatch_state"] == "ACCEPTED" and
                                _other_action_overlaps_window(
                                    other, results.get(other["data"]["action_id"]),
                                    pre_start=pre_start, last_post=last_post)):
                            reasons.append("overlapping_accepted_action")
                            break

        unique_reasons = sorted(set(reasons))
        linked = not unique_reasons
        causal_reasons = ["comparison_assignment_not_in_contract", "concurrent_demand_context_not_in_contract"]
        if not linked:
            causal_reasons.insert(0, "linked_outcome_ineligible")
        assessments.append({
            "decision_id": decision_id,
            "action": action_kind,
            "selected_action_id": selected["data"]["action_id"] if selected else None,
            "deployment_id": ready["data"]["deployment_id"] if ready else data.get("deployment_id"),
            "pre_request_count": len(pre),
            "post_request_count": len(post),
            "linked_outcome_eligible": linked,
            "linked_outcome_reasons": unique_reasons,
            "source_authenticity_verified": False,
            "causal_effect_eligible": False,
            "causal_effect_reasons": causal_reasons,
        })

    orphan_actions = sorted(aid for aid, action in actions.items() if action["data"]["decision_id"] not in decisions)
    orphan_results = sorted(aid for aid in results if aid not in actions)
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "scope": "source_declared_observational_linkage_only",
        "decision_count": len(assessments),
        "orphan_action_ids": orphan_actions,
        "orphan_result_action_ids": orphan_results,
        "decisions": assessments,
    }


def validate_file(path: Path, *, as_of: str | None = None) -> dict:
    return assess_events(read_events(path, as_of=as_of), as_of=as_of)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="agreed action-event JSONL")
    parser.add_argument("--as-of", help="RFC3339 availability cutoff for retrospective replay")
    parser.add_argument("--output", type=Path, help="write assessment JSON instead of stdout")
    args = parser.parse_args()
    try:
        report = validate_file(args.input, as_of=args.as_of)
    except (ContractError, OSError) as error:
        print(f"scale_action_events_invalid: {error}", file=sys.stderr)
        return 2
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
