package agentcontrol

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestOperationOptimizationResultOmitsUnavailableTerminalEvidence(t *testing.T) {
	encoded, err := json.Marshal(OperationOptimizationResult{
		RunID:   "run-operation-001",
		Status:  "failed",
		Message: "Operation Agent execution failed.",
	})
	if err != nil {
		t.Fatalf("marshal terminal operation evidence: %v", err)
	}
	var evidence map[string]any
	if err := json.Unmarshal(encoded, &evidence); err != nil {
		t.Fatalf("unmarshal terminal operation evidence: %v", err)
	}
	for _, field := range []string{
		"agent_name", "source", "latency_ms", "request_guard", "result_guard", "scaling_guard", "decision",
	} {
		if _, ok := evidence[field]; ok {
			t.Fatalf("terminal operation evidence unexpectedly includes %q: %s", field, encoded)
		}
	}
	if evidence["status"] != "failed" || evidence["message"] != "Operation Agent execution failed." {
		t.Fatalf("terminal operation evidence=%s", encoded)
	}
	if evidence["run_id"] != "run-operation-001" {
		t.Fatalf("terminal operation evidence is missing run_id: %s", encoded)
	}
}

func TestOperationOptimizationResultIncludesCompletedEvidence(t *testing.T) {
	result := approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
	})
	result.ScalingGuard = approvedDecisionGuard("scaling approved")
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal completed operation evidence: %v", err)
	}
	var evidence map[string]any
	if err := json.Unmarshal(encoded, &evidence); err != nil {
		t.Fatalf("unmarshal completed operation evidence: %v", err)
	}
	for _, field := range []string{
		"agent_name", "source", "request_guard", "result_guard", "scaling_guard", "decision",
	} {
		if _, ok := evidence[field]; !ok {
			t.Fatalf("completed operation evidence is missing %q: %s", field, encoded)
		}
	}
}

func TestValidateOperationOptimizationResultRejectsReplicaJump(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 3,
		Evidence: []string{"latency_p95_ms"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "replica_step")
}

func TestValidateOperationOptimizationResultRejectsInvalidDecisionContract(t *testing.T) {
	tests := []struct {
		name        string
		mutate      func(*ScalingDecision)
		failedCheck string
	}{
		{
			name: "missing reason",
			mutate: func(decision *ScalingDecision) {
				decision.Reason = ""
			},
			failedCheck: "decision_reason",
		},
		{
			name: "reason exceeds repository text policy",
			mutate: func(decision *ScalingDecision) {
				decision.Reason = strings.Repeat("a", 8001)
			},
			failedCheck: "decision_reason",
		},
		{
			name: "missing created at",
			mutate: func(decision *ScalingDecision) {
				decision.CreatedAt = ""
			},
			failedCheck: "decision_created_at",
		},
		{
			name: "invalid created at",
			mutate: func(decision *ScalingDecision) {
				decision.CreatedAt = "2026-08-05 08:00:00"
			},
			failedCheck: "decision_created_at",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := approvedOptimizationResult(ScalingDecision{
				Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
			})
			test.mutate(&result.Decision)

			guard := validateOperationOptimizationResult(healthyKeepFlow(), result)
			assertOnlyGuardCheckFailed(t, guard, test.failedCheck)
		})
	}
}

func TestValidateOperationOptimizationResultRejectsMissingExecutionRunID(t *testing.T) {
	result := approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
	})
	result.RunID = ""

	guard := validateOperationOptimizationResult(healthyKeepFlow(), result)
	assertOnlyGuardCheckFailed(t, guard, "operation_execution_run_id")
}

func TestValidateOperationOptimizationResultApprovesCompleteDecisionContract(t *testing.T) {
	guard := validateOperationOptimizationResult(healthyKeepFlow(), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
	}))
	if guard.Status != GuardApproved ||
		!hasOptimizationGuardCheck(guard, "decision_reason", true) ||
		!hasOptimizationGuardCheck(guard, "decision_created_at", true) {
		t.Fatalf("complete decision contract guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsMissingRuntimeEvidence(t *testing.T) {
	flow := healthyKeepFlow()
	flow.OptimizationFeedback = nil

	guard := validateOperationOptimizationResult(flow, approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
	}))
	assertOnlyGuardCheckFailed(t, guard, "runtime_evidence")
}

func TestValidateOperationOptimizationResultRejectsUnsupportedAction(t *testing.T) {
	guard := validateOperationOptimizationResult(healthyKeepFlow(), approvedOptimizationResult(ScalingDecision{
		Action: "EXECUTE", CurrentReplicas: 1, DesiredReplicas: 1,
	}))
	assertOnlyGuardCheckFailed(t, guard, "scaling_action")
}

func TestValidateOperationOptimizationResultRejectsScaleOutWithoutSLOEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 2,
	}))
	assertOnlyGuardCheckFailed(t, guard, "slo_evidence")
}

func TestValidateOperationOptimizationResultRejectsMixedScaleOutEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 2,
		Evidence: []string{"latency_p95_ms", "invented_metric"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "evidence_integrity")
}

func TestValidateOperationOptimizationResultApprovesTrustedScaleOutEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 2,
		Evidence: []string{"latency_p95_ms"},
	}))
	if guard.Status != GuardApproved {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsScaleInBelowMinimum(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleInReadyFlow(2, 2, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleIn, CurrentReplicas: 2, DesiredReplicas: 1,
		Evidence: []string{"cpu_average_percent=18.00", "accelerator_average_percent=22.00"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "replica_bounds")
}

func TestValidateOperationOptimizationResultRejectsScaleOutAboveMaximum(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(2, 2), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 2, DesiredReplicas: 3,
		Evidence: []string{"latency_p95_ms"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "replica_bounds")
}

func TestValidateOperationOptimizationResultRejectsMixedScaleInEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleInReadyFlow(2, 1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleIn, CurrentReplicas: 2, DesiredReplicas: 1,
		Evidence: []string{
			"cpu_average_percent=18.00",
			"accelerator_average_percent=22.00",
			"invented_metric",
		},
	}))
	assertOnlyGuardCheckFailed(t, guard, "evidence_integrity")
}

func TestValidateOperationOptimizationResultApprovesCanonicalScaleInEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleInReadyFlow(2, 1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleIn, CurrentReplicas: 2, DesiredReplicas: 1,
		Evidence: []string{"cpu_average_percent=18.00", "accelerator_average_percent=22.00"},
	}))
	if guard.Status != GuardApproved {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsInventedKeepEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(healthyKeepFlow(), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
		Evidence: []string{"invented_metric"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "evidence_integrity")
}

func TestValidateOperationOptimizationResultApprovesMaxBoundSLOKeep(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(3, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 3, DesiredReplicas: 3,
		Evidence: []string{"latency_p95_ms"},
	}))
	if guard.Status != GuardApproved {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsMixedMaxBoundKeepEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(3, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 3, DesiredReplicas: 3,
		Evidence: []string{"latency_p95_ms", "invented_metric"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "evidence_integrity")
}

func TestValidateOperationOptimizationResultApprovesLegacyNoAction(t *testing.T) {
	guard := validateOperationOptimizationResult(healthyKeepFlow(), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionNoAction, CurrentReplicas: 1, DesiredReplicas: 1,
	}))
	if guard.Status != GuardApproved {
		t.Fatalf("guard = %#v", guard)
	}
}

func scaleOutReadyFlow(current int, maximum int) Flow {
	return scalingTestFlow(current, 1, maximum, 55, 61, []string{"latency_p95_ms"})
}

func scaleInReadyFlow(current int, minimum int, maximum int) Flow {
	return scalingTestFlow(current, minimum, maximum, 18, 22, nil)
}

func healthyKeepFlow() Flow {
	return scalingTestFlow(1, 1, 3, 55, 61, nil)
}

func approvedOptimizationResult(decision ScalingDecision) OperationOptimizationResult {
	if decision.Reason == "" {
		decision.Reason = "The bounded scaling recommendation is supported by trusted operation evidence."
	}
	if decision.CreatedAt == "" {
		decision.CreatedAt = "2026-08-05T08:00:00Z"
	}
	return OperationOptimizationResult{
		RunID:        "run-operation-001",
		AgentName:    OperationOptimizationAgentName,
		Source:       "internal",
		Status:       "completed",
		RequestGuard: approvedDecisionGuard("request approved"),
		ResultGuard:  approvedDecisionGuard("result approved"),
		Decision:     decision,
	}
}

func hasOptimizationGuardCheck(guard GuardResult, name string, passed bool) bool {
	for _, check := range guard.Checks {
		if check.Name == name && check.Passed == passed {
			return true
		}
	}
	return false
}

func assertOnlyGuardCheckFailed(t *testing.T, guard GuardResult, wantName string) {
	t.Helper()
	if guard.Status != GuardRejected {
		t.Fatalf("guard status = %q, want %q; guard = %#v", guard.Status, GuardRejected, guard)
	}

	failed := make([]string, 0)
	for _, check := range guard.Checks {
		if !check.Passed {
			failed = append(failed, check.Name)
		}
	}
	if len(failed) != 1 || failed[0] != wantName {
		t.Fatalf("failed checks = %#v, want only %q; guard = %#v", failed, wantName, guard)
	}
}
