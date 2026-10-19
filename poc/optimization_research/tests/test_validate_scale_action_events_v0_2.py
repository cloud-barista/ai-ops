"""Research-only v0.2 scale-out event linkage and fail-closed gates."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH / "scripts"))
from validate_scale_action_events_v0_2 import ContractError, read_events, validate_file


EXAMPLE = RESEARCH / "examples/scale_action_events_v0.2.0.example.jsonl"
SCRIPT = RESEARCH / "scripts/validate_scale_action_events_v0_2.py"


def operational_events() -> list[dict]:
    """A source-declared test scenario, never evidence of a real deployment."""
    events = [json.loads(line) for line in EXAMPLE.read_text(encoding="utf-8").splitlines()]
    for event in events:
        event["time_provenance"] = "SOURCE_EVENT"
        event["source_system"] = {
            "DECISION": "khu-decision-fixture",
            "ACTION_ISSUED": "orchestrator-fixture",
            "ACTION_STATUS": "orchestrator-fixture",
            "ACTION_RESULT": "readiness-fixture",
            "TRAFFIC_HANDOFF": "router-fixture",
            "REQUEST_RESULT": "request-gateway-fixture",
        }[event["event_type"]]
    return events


def event_of(events: list[dict], kind: str, *, last: bool = False) -> dict:
    matches = [event for event in events if event["event_type"] == kind]
    return matches[-1] if last else matches[0]


def check(events: list[dict], *, as_of: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        return validate_file(path, as_of=as_of)


def reasons(events: list[dict]) -> list[str]:
    return check(events)["decisions"][0]["linked_outcome_reasons"]


class ScaleActionEventsV2Tests(unittest.TestCase):
    def test_synthetic_example_and_cli_never_claim_linkage(self):
        result = validate_file(EXAMPLE)
        row = result["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertFalse(row["causal_effect_eligible"])
        self.assertFalse(row["source_authenticity_verified"])
        self.assertIn("handoff_source_time_unverified", row["linked_outcome_reasons"])
        completed = subprocess.run([sys.executable, str(SCRIPT), "--input", str(EXAMPLE)],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["scope"],
                         "source_declared_observational_linkage_only")

    def test_new_deployment_source_declared_link_is_observational_only(self):
        result = check(operational_events())
        row = result["decisions"][0]
        self.assertTrue(row["linked_outcome_eligible"], row["linked_outcome_reasons"])
        self.assertEqual((row["selected_action_id"], row["target_deployment_id"]),
                         ("fixture-action", "fixture-deployment-new"))
        self.assertEqual((row["pre_request_count"], row["post_request_count"]), (1, 1))
        self.assertFalse(row["source_authenticity_verified"])
        self.assertFalse(row["causal_effect_eligible"])
        self.assertIn("comparison_assignment_not_in_contract", row["causal_effect_reasons"])

    def test_missing_handoff_or_ready_or_post_fails_closed(self):
        cases = [
            ("TRAFFIC_HANDOFF", "missing_source_timed_handoff"),
            ("ACTION_RESULT", "missing_confirmed_ready_action"),
        ]
        for kind, expected in cases:
            with self.subTest(kind=kind):
                events = [e for e in operational_events() if e["event_type"] != kind]
                self.assertIn(expected, reasons(events))
        events = [e for e in operational_events() if e["event_id"] != "fixture-v2-request-after"]
        self.assertIn("missing_post_handoff_request_results", reasons(events))

    def test_running_status_is_not_ready(self):
        events = [e for e in operational_events() if e["event_type"] != "ACTION_RESULT"]
        self.assertEqual(event_of(events, "ACTION_STATUS")["data"]["status"], "RUNNING")
        self.assertIn("missing_confirmed_ready_action", reasons(events))
        events = operational_events()
        event_of(events, "ACTION_RESULT")["data"]["readiness_signal"] = "STATUS_ONLY"
        self.assertIn("ready_service_health_evidence_missing", reasons(events))
        events = operational_events()
        event_of(events, "ACTION_STATUS")["logical_service_id"] = "wrong-service"
        self.assertIn("action_status_scope_mismatch", reasons(events))

    def test_ready_count_health_and_target_required(self):
        cases = [
            ("ready_service_replicas", 1, "ready_replicas_differ_from_requested"),
            ("target_ready_replicas", 0, "ready_replicas_differ_from_requested"),
            ("service_health", "UNKNOWN", "ready_service_health_evidence_missing"),
            ("target_deployment_id", "fixture-deployment-old", "target_reuses_existing_deployment"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field):
                events = operational_events()
                event_of(events, "ACTION_RESULT")["data"][field] = value
                self.assertIn(expected, reasons(events))

    def test_ready_must_precede_source_timed_handoff(self):
        events = operational_events()
        handoff = event_of(events, "TRAFFIC_HANDOFF")
        handoff["observed_at"] = "2026-09-29T10:07:30Z"
        handoff["available_at"] = "2026-09-29T10:07:31Z"
        self.assertIn("handoff_before_ready", reasons(events))
        events = operational_events()
        event_of(events, "TRAFFIC_HANDOFF")["time_provenance"] = "LOCAL_OBSERVATION"
        self.assertIn("handoff_source_time_unverified", reasons(events))
        events = operational_events()
        event_of(events, "ACTION_RESULT")["observed_at"] = "2026-09-29T10:06:00Z"
        event_of(events, "ACTION_RESULT")["available_at"] = "2026-09-29T10:08:01Z"
        self.assertIn("action_result_not_after_issue", reasons(events))

    def test_handoff_requires_new_target_exact_sets_and_new_route(self):
        cases = [
            ("target_deployment_id", "wrong-deployment", "handoff_target_mismatch"),
            ("active_deployment_ids_before", ["wrong-deployment"], "handoff_route_mismatch"),
            ("active_deployment_ids_after", ["fixture-deployment-old"], "handoff_active_set_mismatch"),
            ("from_routing_version", "wrong-route", "handoff_route_mismatch"),
            ("to_routing_version", "route-1", "handoff_route_mismatch"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field):
                events = operational_events()
                event_of(events, "TRAFFIC_HANDOFF")["data"][field] = value
                self.assertIn(expected, reasons(events))

    def test_pre_request_must_be_available_at_decision(self):
        events = operational_events()
        event_of(events, "REQUEST_RESULT")["available_at"] = "2026-09-29T10:05:02Z"
        failure = reasons(events)
        self.assertIn("pre_request_unavailable_at_decision", failure)
        self.assertIn("missing_pre_action_request_results", failure)
        result = check(operational_events(), as_of="2026-09-29T10:08:30Z")
        self.assertIn("missing_source_timed_handoff", result["decisions"][0]["linked_outcome_reasons"])

    def test_request_route_deployment_and_version_cannot_be_inferred(self):
        cases = [
            (False, "routing_version", "route-2", "pre_request_route_mismatch"),
            (False, "served_deployment_id", "wrong-deployment", "pre_request_deployment_mismatch"),
            (True, "routing_version", "route-1", "post_request_route_mismatch"),
            (True, "served_deployment_id", "wrong-deployment", "post_request_deployment_mismatch"),
            (True, "served_deployment_id", "fixture-deployment-old", "missing_comparable_target_request"),
        ]
        for is_post, field, value, expected in cases:
            with self.subTest(field=field, value=value, is_post=is_post):
                events = operational_events()
                event_of(events, "REQUEST_RESULT", last=is_post)["data"][field] = value
                self.assertIn(expected, reasons(events))
        events = operational_events()
        event_of(events, "REQUEST_RESULT", last=True)["app_version"] = "2"
        self.assertIn("missing_post_handoff_request_results", reasons(events))
        events = operational_events()
        event_of(events, "REQUEST_RESULT", last=True)["logical_service_id"] = "other-service"
        self.assertIn("missing_post_handoff_request_results", reasons(events))

    def test_post_request_must_start_after_handoff_and_use_target(self):
        events = operational_events()
        post = event_of(events, "REQUEST_RESULT", last=True)
        post["data"]["created_at"] = "2026-09-29T10:09:00Z"
        post["data"]["completed_at"] = "2026-09-29T10:09:00.800Z"
        post["observed_at"] = post["data"]["completed_at"]
        post["data"]["latency_ms"] = 800
        self.assertIn("missing_post_handoff_request_results", reasons(events))
        events = operational_events()
        event_of(events, "REQUEST_RESULT", last=True)["data"]["workload_class"] = "other-kind"
        self.assertIn("missing_comparable_target_request", reasons(events))

    def test_action_result_and_handoff_must_match_decision_scope(self):
        cases = [
            ("ACTION_ISSUED", "logical_service_id", "wrong-service", "action_scope_mismatch"),
            ("ACTION_RESULT", "app_version", "2", "action_result_scope_mismatch"),
            ("TRAFFIC_HANDOFF", "logical_service_id", "wrong-service", "handoff_scope_mismatch"),
        ]
        for kind, field, value, expected in cases:
            with self.subTest(kind=kind):
                events = operational_events()
                event_of(events, kind)[field] = value
                self.assertIn(expected, reasons(events))

    def test_unknown_retry_is_ambiguous_but_failed_then_retry_can_link(self):
        events = operational_events()
        first = event_of(events, "ACTION_ISSUED")
        first["data"]["dispatch_state"] = "UNKNOWN"
        retry = copy.deepcopy(first)
        retry["event_id"] = "retry-issued"
        retry["observed_at"] = "2026-09-29T10:07:00Z"
        retry["available_at"] = "2026-09-29T10:07:01Z"
        retry["data"].update(action_id="retry-action", attempt_no=2,
                             retry_of_action_id="fixture-action", dispatch_state="ACCEPTED")
        event_of(events, "ACTION_RESULT")["data"]["action_id"] = "retry-action"
        event_of(events, "TRAFFIC_HANDOFF")["data"]["action_id"] = "retry-action"
        self.assertIn("unreconciled_retry", reasons(events + [retry]))

        first["data"]["dispatch_state"] = "ACCEPTED"
        failed = copy.deepcopy(event_of(events, "ACTION_RESULT"))
        failed["event_id"] = "failed-first"
        failed["observed_at"] = "2026-09-29T10:06:30Z"
        failed["available_at"] = "2026-09-29T10:06:31Z"
        failed["data"] = {"action_id": "fixture-action", "state": "FAILED",
                          "target_deployment_id": "fixture-deployment-new", "reason": "provisioning-failed"}
        status = event_of(events, "ACTION_STATUS")
        status["data"]["action_id"] = "retry-action"
        status["observed_at"] = "2026-09-29T10:07:30Z"
        status["available_at"] = "2026-09-29T10:07:31Z"
        self.assertTrue(check(events + [retry, failed])["decisions"][0]["linked_outcome_eligible"])

    def test_concurrent_action_or_route_change_blocks_linkage(self):
        events = operational_events()
        other = copy.deepcopy(event_of(events, "ACTION_ISSUED"))
        other["event_id"] = "other-issued"
        other["correlation_id"] = "other-flow"
        other["observed_at"] = "2026-09-29T10:09:30Z"
        other["available_at"] = "2026-09-29T10:09:31Z"
        other["data"].update(action_id="other-action", decision_id="other-decision",
                             revision_request_id="other-revision")
        self.assertIn("overlapping_accepted_or_unknown_action", reasons(events + [other]))
        other["data"]["dispatch_state"] = "UNKNOWN"
        self.assertIn("overlapping_accepted_or_unknown_action", reasons(events + [other]))
        extra_handoff = copy.deepcopy(event_of(events, "TRAFFIC_HANDOFF"))
        extra_handoff["event_id"] = "other-handoff"
        extra_handoff["data"]["action_id"] = "other-action"
        extra_handoff["observed_at"] = "2026-09-29T10:09:30Z"
        extra_handoff["available_at"] = "2026-09-29T10:09:31Z"
        self.assertIn("overlapping_traffic_handoff", reasons(events + [extra_handoff]))

    def test_rejected_first_attempt_with_running_status_blocks_retry_linkage(self):
        events = operational_events()
        first = event_of(events, "ACTION_ISSUED")
        first["data"]["dispatch_state"] = "REJECTED"
        retry = copy.deepcopy(first)
        retry["event_id"] = "retry-issued"
        retry["observed_at"] = "2026-09-29T10:07:00Z"
        retry["available_at"] = "2026-09-29T10:07:01Z"
        retry["data"].update(action_id="retry-action", attempt_no=2,
                             retry_of_action_id="fixture-action", dispatch_state="ACCEPTED")
        event_of(events, "ACTION_RESULT")["data"]["action_id"] = "retry-action"
        event_of(events, "TRAFFIC_HANDOFF")["data"]["action_id"] = "retry-action"
        # The retained RUNNING status contradicts the first attempt's REJECTED receipt.
        self.assertIn("rejected_action_has_execution_evidence", reasons(events + [retry]))

    def test_foreign_action_after_last_request_still_overlaps_fixed_post_window(self):
        events = operational_events()
        other = copy.deepcopy(event_of(events, "ACTION_ISSUED"))
        other["event_id"] = "other-issued-after-last-request"
        other["correlation_id"] = "other-flow"
        other["observed_at"] = "2026-09-29T10:11:00Z"
        other["available_at"] = "2026-09-29T10:11:01Z"
        other["data"].update(action_id="other-action", decision_id="other-decision",
                             revision_request_id="other-revision")
        # The selected request ended at 10:10:00.800, but the predeclared
        # 30-minute post window continues until 10:39.
        self.assertIn("overlapping_accepted_or_unknown_action", reasons(events + [other]))

    def test_as_of_before_fixed_post_window_end_cannot_claim_complete_linkage(self):
        events = operational_events()
        early = check(events, as_of="2026-09-29T10:10:02Z")["decisions"][0]
        self.assertIn("post_window_not_closed_as_of", early["linked_outcome_reasons"])
        self.assertFalse(early["linked_outcome_eligible"])
        later = check(events, as_of="2026-09-29T10:40:00Z")["decisions"][0]
        self.assertTrue(later["linked_outcome_eligible"], later["linked_outcome_reasons"])
        self.assertFalse(later["event_stream_completeness_verified"])

    def test_status_must_match_action_scope_even_when_action_id_exists(self):
        events = operational_events()
        other = copy.deepcopy(event_of(events, "ACTION_ISSUED"))
        other["event_id"] = "foreign-service-issued"
        other["logical_service_id"] = "foreign-service"
        other["correlation_id"] = "foreign-flow"
        other["observed_at"] = "2026-09-29T10:09:30Z"
        other["available_at"] = "2026-09-29T10:09:31Z"
        other["data"].update(action_id="foreign-action", decision_id="foreign-decision",
                             revision_request_id="foreign-revision")
        event_of(events, "ACTION_STATUS")["data"]["action_id"] = "foreign-action"
        self.assertIn("action_status_scope_mismatch", reasons(events + [other]))

    def test_status_target_must_reconcile_with_ready_target(self):
        events = operational_events()
        event_of(events, "ACTION_ISSUED")["data"].pop("target_deployment_id")
        event_of(events, "ACTION_STATUS")["data"]["target_deployment_id"] = "wrong-deployment"
        self.assertIn("action_status_target_mismatch", reasons(events))

    def test_status_before_its_foreign_action_cannot_hide_inside_window(self):
        events = operational_events()
        other = copy.deepcopy(event_of(events, "ACTION_ISSUED"))
        other["event_id"] = "late-foreign-issued"
        other["correlation_id"] = "foreign-flow"
        other["observed_at"] = "2026-09-29T10:40:00Z"
        other["available_at"] = "2026-09-29T10:40:01Z"
        other["data"].update(action_id="foreign-action", decision_id="foreign-decision",
                             revision_request_id="foreign-revision")
        status = copy.deepcopy(event_of(events, "ACTION_STATUS"))
        status["event_id"] = "foreign-running-before-issue"
        status["correlation_id"] = "foreign-flow"
        status["observed_at"] = "2026-09-29T10:09:30Z"
        status["available_at"] = "2026-09-29T10:09:31Z"
        status["data"]["action_id"] = "foreign-action"
        self.assertIn("action_status_before_issue", reasons(events + [other, status]))

    def test_old_terminal_action_needs_source_issue_time_and_rejected_cannot_have_result(self):
        events = operational_events()
        prior = copy.deepcopy(event_of(events, "ACTION_ISSUED"))
        prior["event_id"] = "old-issued"
        prior["correlation_id"] = "old-flow"
        prior["observed_at"] = "2026-09-29T09:00:00Z"
        prior["available_at"] = "2026-09-29T09:00:01Z"
        prior["data"].update(action_id="old-action", decision_id="old-decision",
                             revision_request_id="old-revision")
        failure = copy.deepcopy(event_of(events, "ACTION_RESULT"))
        failure["event_id"] = "old-failed"
        failure["correlation_id"] = "old-flow"
        failure["observed_at"] = "2026-09-29T09:20:00Z"
        failure["available_at"] = "2026-09-29T09:20:01Z"
        failure["data"] = {"action_id": "old-action", "state": "FAILED",
                           "target_deployment_id": "fixture-deployment-new", "reason": "capacity-error"}
        self.assertTrue(check(events + [prior, failure])["decisions"][0]["linked_outcome_eligible"])
        late_status = copy.deepcopy(event_of(events, "ACTION_STATUS"))
        late_status["event_id"] = "old-running-after-terminal"
        late_status["correlation_id"] = "old-flow"
        late_status["observed_at"] = "2026-09-29T10:09:30Z"
        late_status["available_at"] = "2026-09-29T10:09:31Z"
        late_status["data"]["action_id"] = "old-action"
        self.assertIn("overlapping_accepted_or_unknown_action",
                      reasons(events + [prior, failure, late_status]))
        prior["time_provenance"] = "LOCAL_OBSERVATION"
        self.assertIn("overlapping_accepted_or_unknown_action", reasons(events + [prior, failure]))

        events = operational_events()
        rejected = copy.deepcopy(event_of(events, "ACTION_ISSUED"))
        rejected["event_id"] = "rejected-issued"
        rejected["correlation_id"] = "other-flow"
        rejected["observed_at"] = "2026-09-29T10:09:30Z"
        rejected["available_at"] = "2026-09-29T10:09:31Z"
        rejected["data"].update(action_id="rejected-action", decision_id="other-decision",
                                revision_request_id="other-revision", dispatch_state="REJECTED")
        inconsistent = copy.deepcopy(event_of(events, "ACTION_RESULT"))
        inconsistent["event_id"] = "rejected-ready"
        inconsistent["correlation_id"] = "other-flow"
        inconsistent["observed_at"] = "2026-09-29T10:09:40Z"
        inconsistent["available_at"] = "2026-09-29T10:09:41Z"
        inconsistent["data"]["action_id"] = "rejected-action"
        self.assertIn("rejected_action_has_execution_evidence", reasons(events + [rejected, inconsistent]))
        running = copy.deepcopy(event_of(events, "ACTION_STATUS"))
        running["event_id"] = "rejected-running"
        running["correlation_id"] = "other-flow"
        running["observed_at"] = "2026-09-29T10:09:40Z"
        running["available_at"] = "2026-09-29T10:09:41Z"
        running["data"]["action_id"] = "rejected-action"
        self.assertIn("rejected_action_has_execution_evidence", reasons(events + [rejected, running]))

    def test_unattributed_runtime_evidence_in_window_blocks_linkage(self):
        events = operational_events()
        orphan_result = copy.deepcopy(event_of(events, "ACTION_RESULT"))
        orphan_result["event_id"] = "orphan-ready"
        orphan_result["data"]["action_id"] = "unseen-action"
        orphan_result["observed_at"] = "2026-09-29T10:09:30Z"
        orphan_result["available_at"] = "2026-09-29T10:09:31Z"
        self.assertIn("unattributed_action_result_in_window", reasons(events + [orphan_result]))
        orphan_status = copy.deepcopy(event_of(events, "ACTION_STATUS"))
        orphan_status["event_id"] = "orphan-running"
        orphan_status["data"]["action_id"] = "unseen-action"
        orphan_status["observed_at"] = "2026-09-29T10:09:30Z"
        orphan_status["available_at"] = "2026-09-29T10:09:31Z"
        self.assertIn("unattributed_action_status_in_window", reasons(events + [orphan_status]))

    def test_schema_time_and_duplicate_guards(self):
        events = operational_events()
        event_of(events, "DECISION")["data"]["action"] = "SCALE_IN"
        with self.assertRaisesRegex(ContractError, "schema"):
            check(events)
        events = operational_events()
        event_of(events, "REQUEST_RESULT", last=True)["observed_at"] = "2026-09-29T10:10:00.801Z"
        with self.assertRaisesRegex(ContractError, "request_time_order_invalid"):
            check(events)
        events = operational_events()
        event_of(events, "DECISION")["data"]["desired_replicas"] = 3
        with self.assertRaisesRegex(ContractError, "scale_out_must_add_one_replica"):
            check(events)
        events = operational_events()
        duplicate = copy.deepcopy(events[-1])
        duplicate["data"]["success"] = False
        with self.assertRaisesRegex(ContractError, "conflicting_event_id"):
            check(events + [duplicate])

    def test_exact_retransmit_and_as_of_correction(self):
        events = operational_events()
        self.assertTrue(check(events + [copy.deepcopy(events[-1])])["decisions"][0]["linked_outcome_eligible"])
        corrected = copy.deepcopy(events[-1])
        corrected.update(event_id="corrected-post", correction_of=events[-1]["event_id"],
                         correction_version=1, available_at="2026-09-29T10:50:00Z")
        corrected["data"]["routing_version"] = "wrong-route"
        self.assertTrue(check(events + [corrected], as_of="2026-09-29T10:40:00Z")
                        ["decisions"][0]["linked_outcome_eligible"])
        self.assertIn("post_request_route_mismatch", reasons(events + [corrected]))

    def test_v01_record_is_not_a_v02_record(self):
        legacy = RESEARCH / "examples/scale_action_events_v0.1.0.example.jsonl"
        with self.assertRaisesRegex(ContractError, "line_1:schema"):
            read_events(legacy)


if __name__ == "__main__":
    unittest.main()
