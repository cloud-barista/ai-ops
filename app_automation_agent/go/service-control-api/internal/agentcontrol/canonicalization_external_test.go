package agentcontrol_test

import (
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestCanonicalizeOperationOptimizationResultEmitsKeepForLegacyNoAction(t *testing.T) {
	legacy := agentcontrol.OperationOptimizationResult{
		Decision: agentcontrol.ScalingDecision{Action: agentcontrol.ScalingActionNoAction},
	}

	canonical := agentcontrol.CanonicalizeOperationOptimizationResult(legacy)
	if canonical.Decision.Action != agentcontrol.ScalingActionKeep {
		t.Fatalf("canonical action = %q, want %q", canonical.Decision.Action, agentcontrol.ScalingActionKeep)
	}
	if legacy.Decision.Action != agentcontrol.ScalingActionNoAction {
		t.Fatalf("legacy result was mutated: %#v", legacy)
	}
}
