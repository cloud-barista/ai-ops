package agentcontrol

import "testing"

func TestValidateOperationOptimizationResultRejectsReplicaJump(t *testing.T) {
	flow := scalingReadyFlow()
	result := approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 3,
	})

	guard := validateOperationOptimizationResult(flow, result)
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsMissingRuntimeEvidence(t *testing.T) {
	flow := scalingReadyFlow()
	flow.OptimizationFeedback = nil

	guard := validateOperationOptimizationResult(flow, approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionKeep, CurrentReplicas: 1, DesiredReplicas: 1,
	}))
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsUnsupportedAction(t *testing.T) {
	guard := validateOperationOptimizationResult(scalingReadyFlow(), approvedOptimizationResult(ScalingDecision{
		Action: "EXECUTE", CurrentReplicas: 1, DesiredReplicas: 1,
	}))
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsScaleOutWithoutSLOEvidence(t *testing.T) {
	guard := validateOperationOptimizationResult(scalingReadyFlow(), approvedOptimizationResult(ScalingDecision{
		Action:          ScalingActionScaleOut,
		CurrentReplicas: 1,
		DesiredReplicas: 2,
	}))
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsInventedSLOEvidence(t *testing.T) {
	flow := scalingReadyFlow()
	flow.OptimizationFeedback.Data.OptimizationFeedback.SLOViolations = nil

	guard := validateOperationOptimizationResult(flow, approvedOptimizationResult(ScalingDecision{
		Action:          ScalingActionScaleOut,
		CurrentReplicas: 1,
		DesiredReplicas: 2,
		Evidence:        []string{"latency_p95_ms"},
	}))
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsScaleInBelowMinimum(t *testing.T) {
	guard := validateOperationOptimizationResult(scalingReadyFlow(), approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleIn, CurrentReplicas: 1, DesiredReplicas: 0,
	}))
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultRejectsScaleOutAboveMaximum(t *testing.T) {
	flow := scalingReadyFlow()
	flow.ApplicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMax = 1

	guard := validateOperationOptimizationResult(flow, approvedOptimizationResult(ScalingDecision{
		Action: ScalingActionScaleOut, CurrentReplicas: 1, DesiredReplicas: 2,
	}))
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func TestValidateOperationOptimizationResultApprovesKeep(t *testing.T) {
	for _, action := range []string{ScalingActionKeep, ScalingActionNoAction} {
		guard := validateOperationOptimizationResult(scalingReadyFlow(), approvedOptimizationResult(ScalingDecision{
			Action: action, CurrentReplicas: 1, DesiredReplicas: 1,
		}))
		if guard.Status != GuardApproved {
			t.Fatalf("action %q guard = %#v", action, guard)
		}
	}
}

func scalingReadyFlow() Flow {
	return scalingTestFlow(1, 1, 3, 55, 61, []string{"latency_p95_ms"})
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
