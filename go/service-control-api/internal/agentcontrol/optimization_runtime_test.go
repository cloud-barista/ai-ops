package agentcontrol

import "testing"

func TestValidateOperationOptimizationResultRejectsReplicaJump(t *testing.T) {
	guard := validateOperationOptimizationResult(scaleOutReadyFlow(1, 3), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 3,
		Evidence: []string{"latency_p95_ms"},
	}))
	assertOnlyGuardCheckFailed(t, guard, "replica_step")
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
	return OperationOptimizationResult{
		AgentName:    OperationOptimizationAgentName,
		Source:       "internal",
		Status:       "completed",
		RequestGuard: approvedDecisionGuard("request approved"),
		ResultGuard:  approvedDecisionGuard("result approved"),
		Decision:     decision,
	}
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
