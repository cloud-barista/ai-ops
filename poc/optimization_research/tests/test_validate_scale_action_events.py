"""Cross-event gates for the separate research action-event intake contract."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH / "scripts"))
from validate_scale_action_events import ContractError, read_events, validate_file


EXAMPLE = RESEARCH / "examples/scale_action_events_v0.1.0.example.jsonl"
SCRIPT = RESEARCH / "scripts/validate_scale_action_events.py"


def operational_events() -> list[dict]:
    events = [json.loads(line) for line in EXAMPLE.read_text(encoding="utf-8").splitlines()]
    for event in events:
        event["time_provenance"] = "SOURCE_EVENT"
        event["source_system"] = {
            "DECISION": "khu-decision-service",
            "ACTION_ISSUED": "inno-orchestrator",
            "ACTION_RESULT": "inno-readiness-monitor",
            "REQUEST_RESULT": "inno-request-gateway",
        }[event["event_type"]]
    return events


def check(events: list[dict], *, as_of: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        return validate_file(path, as_of=as_of)


class ScaleActionEventsTests(unittest.TestCase):
    def test_synthetic_example_is_not_promoted(self):
        report = validate_file(EXAMPLE)
        row = report["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertFalse(row["causal_effect_eligible"])
        self.assertIn("ready_source_time_unverified", row["linked_outcome_reasons"])
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(EXAMPLE)],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["scope"],
                         "source_declared_observational_linkage_only")

    def test_complete_source_declared_link_is_observational_only(self):
        row = check(operational_events())["decisions"][0]
        self.assertTrue(row["linked_outcome_eligible"])
        self.assertEqual(row["pre_request_count"], 1)
        self.assertEqual(row["post_request_count"], 1)
        self.assertEqual(row["selected_action_id"], "fixture-action-1")
        self.assertFalse(row["source_authenticity_verified"])
        self.assertFalse(row["causal_effect_eligible"])
        self.assertIn("comparison_assignment_not_in_contract", row["causal_effect_reasons"])

    def test_missing_ready_and_post_request_fail_closed(self):
        events = operational_events()
        without_ready = [e for e in events if e["event_type"] != "ACTION_RESULT"]
        reasons = check(without_ready)["decisions"][0]["linked_outcome_reasons"]
        self.assertIn("missing_confirmed_ready_action", reasons)
        without_post = [e for e in events if e["event_id"] != "fixture-request-after"]
        reasons = check(without_post)["decisions"][0]["linked_outcome_reasons"]
        self.assertIn("missing_post_ready_request_results", reasons)

    def test_status_only_or_wrong_replica_count_is_not_ready_evidence(self):
        for change, expected in [("status_only", "ready_replica_evidence_missing"),
                                 ("replica_mismatch", "ready_replicas_differ_from_requested")]:
            with self.subTest(change=change):
                events = operational_events()
                ready = next(e for e in events if e["event_type"] == "ACTION_RESULT")
                if change == "status_only":
                    ready["data"]["readiness_signal"] = "STATUS_ONLY"
                else:
                    ready["data"]["ready_replicas"] = 1
                row = check(events)["decisions"][0]
                self.assertFalse(row["linked_outcome_eligible"])
                self.assertIn(expected, row["linked_outcome_reasons"])

    def test_earlier_request_unavailable_at_decision_does_not_leak(self):
        events = operational_events()
        before = next(e for e in events if e["event_id"] == "fixture-request-before")
        before["available_at"] = "2026-09-29T10:05:02Z"
        row = check(events)["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("missing_pre_action_request_results", row["linked_outcome_reasons"])
        decision = events[0]
        decision["data"]["inputs_max_available_at"] = "2026-09-29T10:05:02Z"
        with self.assertRaisesRegex(ContractError, "decision_uses_unavailable_input"):
            check(events)

    def test_as_of_replay_excludes_future_available_result(self):
        events = operational_events()
        later = next(e for e in events if e["event_id"] == "fixture-request-after")
        later["available_at"] = "2026-09-29T11:00:00Z"
        cutoff = "2026-09-29T10:30:00Z"
        row = check(events, as_of=cutoff)["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("missing_post_ready_request_results", row["linked_outcome_reasons"])
        self.assertTrue(check(events)["decisions"][0]["linked_outcome_eligible"])

    def test_time_or_identity_contradictions_are_not_linked(self):
        cases = [
            ("ready_before_issue", "ready_before_action_issued"),
            ("wrong_deployment", "ready_deployment_mismatch"),
            ("wrong_scope", "ready_scope_mismatch"),
            ("wrong_action", "missing_confirmed_ready_action"),
        ]
        for change, expected in cases:
            with self.subTest(change=change):
                events = operational_events()
                ready = next(e for e in events if e["event_type"] == "ACTION_RESULT")
                if change == "ready_before_issue":
                    ready["observed_at"] = "2026-09-29T10:05:30Z"
                    ready["available_at"] = "2026-09-29T10:05:31Z"
                elif change == "wrong_deployment":
                    ready["data"]["deployment_id"] = "another-deployment"
                elif change == "wrong_scope":
                    ready["correlation_id"] = "another-flow"
                else:
                    ready["data"]["action_id"] = "another-action"
                row = check(events)["decisions"][0]
                self.assertFalse(row["linked_outcome_eligible"])
                self.assertIn(expected, row["linked_outcome_reasons"])

    def test_request_time_and_schema_are_strict(self):
        events = operational_events()
        request = next(e for e in events if e["event_id"] == "fixture-request-after")
        request["data"]["latency_ms"] = 20
        with self.assertRaisesRegex(ContractError, "request_latency_inconsistent_with_timestamps"):
            check(events)
        events = operational_events()
        request = next(e for e in events if e["event_id"] == "fixture-request-after")
        request["observed_at"] = "2026-09-29T10:10:01Z"
        with self.assertRaisesRegex(ContractError, "request_time_order_invalid"):
            check(events)
        events = operational_events()
        events[0]["unrecognized"] = 3
        with self.assertRaisesRegex(ContractError, "unknown_field"):
            check(events)
        events = operational_events()
        events[0]["observed_at"] = "2026-09-29T10:05:00"
        with self.assertRaisesRegex(ContractError, "invalid_timestamp"):
            check(events)
        events = operational_events()
        events[0]["observed_at"] = "2026-09-29T10:05:00+00:60"
        with self.assertRaisesRegex(ContractError, "invalid_timestamp"):
            check(events)
        events = operational_events()
        events[0]["data"]["pre_window_seconds"] = 2592001
        with self.assertRaisesRegex(ContractError, "above_maximum"):
            check(events)

    def test_nanosecond_order_is_not_truncated_to_microseconds(self):
        events = operational_events()
        after = next(e for e in events if e["event_id"] == "fixture-request-after")
        after["data"]["completed_at"] = "2026-09-29T10:10:00.000000001Z"
        after["observed_at"] = "2026-09-29T10:10:00.000000002Z"
        with self.assertRaisesRegex(ContractError, "request_time_order_invalid"):
            check(events)

    def test_duplicate_json_key_or_nonfinite_number_rejected(self):
        events = operational_events()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            first = json.dumps(events[0])
            duplicate = first.replace('"event_id": "fixture-decision-1",',
                                      '"event_id": "fixture-decision-1", "event_id": "hidden",', 1)
            path.write_text(duplicate + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "duplicate_json_key:event_id"):
                read_events(path)
            nonfinite = first.replace('"pre_window_seconds": 1800',
                                      '"pre_window_seconds": NaN', 1)
            path.write_text(nonfinite + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "nonfinite_json_number"):
                read_events(path)

    def test_retransmit_and_append_only_correction_as_of(self):
        events = operational_events()
        events.append(copy.deepcopy(events[-1]))
        corrected = copy.deepcopy(events[-1])
        corrected["event_id"] = "fixture-request-after-correction"
        corrected["correction_of"] = "fixture-request-after"
        corrected["correction_version"] = 1
        corrected["available_at"] = "2026-09-29T10:20:00Z"
        corrected["data"]["latency_ms"] = 801
        events.append(corrected)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            old = read_events(path, as_of="2026-09-29T10:15:00Z")
            new = read_events(path, as_of="2026-09-29T10:30:00Z")
            old_request = next(e for e in old if e["data"].get("request_id") == "fixture-user-request-after")
            new_request = next(e for e in new if e["data"].get("request_id") == "fixture-user-request-after")
            self.assertEqual(old_request["data"]["latency_ms"], 800)
            self.assertEqual(new_request["data"]["latency_ms"], 801)
            self.assertEqual(len(new), 5)
        self.assertTrue(check(events)["decisions"][0]["linked_outcome_eligible"])

    def test_conflicting_retransmission_and_correction_fork_rejected(self):
        events = operational_events()
        conflict = copy.deepcopy(events[-1])
        conflict["data"]["success"] = False
        with self.assertRaisesRegex(ContractError, "conflicting_event_id"):
            check(events + [conflict])
        correction = copy.deepcopy(events[-1])
        correction.update(event_id="fix-1", correction_of=events[-1]["event_id"],
                          correction_version=1, available_at="2026-09-29T10:20:00Z")
        fork = copy.deepcopy(correction)
        fork["event_id"] = "fix-2"
        with self.assertRaisesRegex(ContractError, "forked_correction"):
            check(events + [correction, fork])

    def test_unknown_retry_and_overlapping_action_fail_closed(self):
        events = operational_events()
        first = next(e for e in events if e["event_type"] == "ACTION_ISSUED")
        first["data"]["dispatch_state"] = "UNKNOWN"
        retry = copy.deepcopy(first)
        retry["event_id"] = "fixture-action-issued-2"
        retry["data"].update(action_id="fixture-action-2", attempt_no=2,
                             retry_of_action_id="fixture-action-1", dispatch_state="ACCEPTED")
        retry["observed_at"] = "2026-09-29T10:07:00Z"
        retry["available_at"] = "2026-09-29T10:07:01Z"
        ready = next(e for e in events if e["event_type"] == "ACTION_RESULT")
        ready["data"]["action_id"] = "fixture-action-2"
        row = check(events + [retry])["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("unreconciled_retry", row["linked_outcome_reasons"])

        events = operational_events()
        other = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_ISSUED"))
        other["event_id"] = "other-action-event"
        other["data"].update(action_id="other-action", decision_id="other-decision")
        other["observed_at"] = "2026-09-29T10:09:30Z"
        other["available_at"] = "2026-09-29T10:09:31Z"
        row = check(events + [other])["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("overlapping_accepted_action", row["linked_outcome_reasons"])

    def test_other_accepted_action_must_finish_before_pre_window(self):
        # The declared pre window starts at 09:35 and the last post request
        # completes at 10:10:00.800. An earlier command can still be active
        # during either half of that window.
        cases = [
            ("ready_during_current_execution", "10:03:00", "10:08:00", "READY", True),
            ("ready_during_pre_window", "09:30:00", "09:40:00", "READY", True),
            ("failed_during_pre_window", "09:30:00", "09:40:00", "FAILED", True),
            ("ready_before_pre_window", "09:00:00", "09:20:00", "READY", False),
            ("failed_before_pre_window", "09:00:00", "09:20:00", "FAILED", False),
            ("status_only_before_pre_window", "09:00:00", "09:20:00", "READY", True),
            ("wrong_ready_count_before_pre_window", "09:00:00", "09:20:00", "READY", True),
            ("unresolved_old_action", "09:00:00", None, None, True),
            ("new_action_after_last_request", "10:11:00", None, None, False),
            ("other_deployment_ready_during_window", "10:03:00", "10:08:00", "READY", True),
            ("other_deployment_finished_before_window", "09:00:00", "09:20:00", "READY", False),
        ]
        for name, issued_time, terminal_time, terminal_state, overlaps in cases:
            with self.subTest(name=name):
                events = operational_events()
                deployment_id = ("other-deployment" if name.startswith("other_deployment")
                                 else "fixture-deployment-1")
                other = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_ISSUED"))
                other["event_id"] = "other-action-issued"
                other["correlation_id"] = "other-flow"
                other["data"].update(
                    action_id="other-action", decision_id="other-decision",
                    revision_request_id="other-revision-request", action="UPDATE",
                    desired_replicas=1, deployment_id=deployment_id,
                )
                other["observed_at"] = f"2026-09-29T{issued_time}Z"
                other["available_at"] = (
                    datetime.fromisoformat(other["observed_at"].replace("Z", "+00:00"))
                    + timedelta(seconds=1)
                ).isoformat().replace("+00:00", "Z")
                extra = [other]
                if terminal_time is not None:
                    terminal = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_RESULT"))
                    terminal["event_id"] = "other-action-result"
                    terminal["correlation_id"] = "other-flow"
                    terminal["observed_at"] = f"2026-09-29T{terminal_time}Z"
                    terminal["available_at"] = (
                        datetime.fromisoformat(terminal["observed_at"].replace("Z", "+00:00"))
                        + timedelta(seconds=1)
                    ).isoformat().replace("+00:00", "Z")
                    if terminal_state == "READY":
                        terminal["data"].update(action_id="other-action", ready_replicas=1,
                                                deployment_id=deployment_id)
                        if name == "status_only_before_pre_window":
                            terminal["data"]["readiness_signal"] = "STATUS_ONLY"
                        if name == "wrong_ready_count_before_pre_window":
                            terminal["data"]["ready_replicas"] = 2
                    else:
                        terminal["data"] = {
                            "action_id": "other-action",
                            "deployment_id": deployment_id,
                            "state": "FAILED",
                            "reason": "operation-failed",
                        }
                    extra.append(terminal)
                row = check(events + extra)["decisions"][0]
                self.assertEqual(row["linked_outcome_eligible"], not overlaps,
                                 row["linked_outcome_reasons"])
                self.assertEqual("overlapping_accepted_action" in row["linked_outcome_reasons"],
                                 overlaps)

    def test_unverified_old_result_does_not_resolve_accepted_action(self):
        events = operational_events()
        other = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_ISSUED"))
        other["event_id"] = "other-action-issued"
        other["correlation_id"] = "other-flow"
        other["observed_at"] = "2026-09-29T09:00:00Z"
        other["available_at"] = "2026-09-29T09:00:01Z"
        other["data"].update(action_id="other-action", decision_id="other-decision",
                             action="UPDATE", desired_replicas=1)
        old_result = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_RESULT"))
        old_result["event_id"] = "other-action-result"
        old_result["correlation_id"] = "other-flow"
        old_result["data"].update(action_id="other-action", ready_replicas=1)
        old_result["observed_at"] = "2026-09-29T09:20:00Z"
        old_result["available_at"] = "2026-09-29T09:20:01Z"
        for mutation in ("local_time", "wrong_scope", "wrong_deployment", "before_issue"):
            with self.subTest(mutation=mutation):
                result = copy.deepcopy(old_result)
                if mutation == "local_time":
                    result["time_provenance"] = "LOCAL_OBSERVATION"
                elif mutation == "wrong_scope":
                    result["correlation_id"] = "different-flow"
                elif mutation == "wrong_deployment":
                    result["data"]["deployment_id"] = "different-deployment"
                else:
                    result["observed_at"] = "2026-09-29T08:59:59Z"
                    result["available_at"] = "2026-09-29T09:00:00Z"
                row = check(events + [other, result])["decisions"][0]
                self.assertFalse(row["linked_outcome_eligible"])
                self.assertIn("overlapping_accepted_action", row["linked_outcome_reasons"])

    def test_accepted_attempt_without_terminal_failure_is_ambiguous_even_with_other_target(self):
        for target in (None, "other-deployment"):
            with self.subTest(target=target):
                events = operational_events()
                extra = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_ISSUED"))
                extra["event_id"] = "extra-issued"
                extra["data"]["action_id"] = "extra-action"
                extra["data"]["attempt_no"] = 2
                extra["data"]["retry_of_action_id"] = "fixture-action-1"
                if target is None:
                    del extra["data"]["deployment_id"]
                else:
                    extra["data"]["deployment_id"] = target
                extra["observed_at"] = "2026-09-29T10:07:00Z"
                extra["available_at"] = "2026-09-29T10:07:01Z"
                row = check(events + [extra])["decisions"][0]
                self.assertFalse(row["linked_outcome_eligible"])
                self.assertIn("unresolved_accepted_attempt", row["linked_outcome_reasons"])

    def test_failed_attempt_then_confirmed_retry_can_link(self):
        events = operational_events()
        first = next(e for e in events if e["event_type"] == "ACTION_ISSUED")
        first_result = {
            **copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_RESULT")),
            "event_id": "first-failed",
            "observed_at": "2026-09-29T10:07:00Z",
            "available_at": "2026-09-29T10:07:01Z",
            "data": {"action_id": "fixture-action-1", "deployment_id": "fixture-deployment-1",
                     "state": "FAILED", "reason": "provisioning-error"},
        }
        retry = copy.deepcopy(first)
        retry["event_id"] = "fixture-action-issued-2"
        retry["data"].update(action_id="fixture-action-2", attempt_no=2,
                             retry_of_action_id="fixture-action-1")
        retry["observed_at"] = "2026-09-29T10:08:00Z"
        retry["available_at"] = "2026-09-29T10:08:01Z"
        ready = next(e for e in events if e["event_type"] == "ACTION_RESULT")
        ready["data"]["action_id"] = "fixture-action-2"
        row = check(events + [first_result, retry])["decisions"][0]
        self.assertTrue(row["linked_outcome_eligible"], row["linked_outcome_reasons"])
        retry["data"]["revision_request_id"] = "different-manifest-request"
        row = check(events + [first_result, retry])["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("revision_changed_across_retries", row["linked_outcome_reasons"])

    def test_failed_result_cannot_precede_its_issued_action_or_change_scope(self):
        events = operational_events()
        first = next(e for e in events if e["event_type"] == "ACTION_ISSUED")
        failure = copy.deepcopy(next(e for e in events if e["event_type"] == "ACTION_RESULT"))
        failure["event_id"] = "first-failed"
        failure["observed_at"] = "2026-09-29T10:05:30Z"
        failure["available_at"] = "2026-09-29T10:05:31Z"
        failure["data"] = {"action_id": "fixture-action-1", "deployment_id": "fixture-deployment-1",
                           "state": "FAILED", "reason": "provisioning-error"}
        retry = copy.deepcopy(first)
        retry["event_id"] = "retry-issued"
        retry["observed_at"] = "2026-09-29T10:08:00Z"
        retry["available_at"] = "2026-09-29T10:08:01Z"
        retry["data"].update(action_id="retry-action", attempt_no=2,
                             retry_of_action_id="fixture-action-1")
        ready = next(e for e in events if e["event_type"] == "ACTION_RESULT")
        ready["data"]["action_id"] = "retry-action"
        row = check(events + [failure, retry])["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("action_result_before_issue", row["linked_outcome_reasons"])
        self.assertIn("unreconciled_retry", row["linked_outcome_reasons"])

        failure["observed_at"] = "2026-09-29T10:07:00Z"
        failure["available_at"] = "2026-09-29T10:07:01Z"
        failure["data"]["deployment_id"] = "wrong-deployment"
        failure["correlation_id"] = "wrong-flow"
        row = check(events + [failure, retry])["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("action_result_scope_mismatch", row["linked_outcome_reasons"])
        self.assertIn("action_result_deployment_mismatch", row["linked_outcome_reasons"])
        self.assertIn("unreconciled_retry", row["linked_outcome_reasons"])

    def test_rejected_attempt_retry_must_follow_previous_issue_time(self):
        events = operational_events()
        first = next(e for e in events if e["event_type"] == "ACTION_ISSUED")
        first["data"]["dispatch_state"] = "REJECTED"
        first["observed_at"] = "2026-09-29T10:08:00Z"
        first["available_at"] = "2026-09-29T10:08:01Z"
        retry = copy.deepcopy(first)
        retry["event_id"] = "retry-issued"
        retry["observed_at"] = "2026-09-29T10:07:00Z"
        retry["available_at"] = "2026-09-29T10:07:01Z"
        retry["data"].update(action_id="retry-action", attempt_no=2,
                             retry_of_action_id="fixture-action-1", dispatch_state="ACCEPTED")
        ready = next(e for e in events if e["event_type"] == "ACTION_RESULT")
        ready["data"]["action_id"] = "retry-action"
        row = check(events + [retry])["decisions"][0]
        self.assertFalse(row["linked_outcome_eligible"])
        self.assertIn("retry_not_after_previous_issue", row["linked_outcome_reasons"])

    def test_go_delivery_receipt_is_not_an_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.jsonl"
            path.write_text(json.dumps({"correlation_id": "flow", "status": "SUBMITTED",
                                        "revision": 1, "deployment": {"status": "RUNNING"}}) + "\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "line_1"):
                validate_file(path)


if __name__ == "__main__":
    unittest.main()
