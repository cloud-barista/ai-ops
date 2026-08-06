package agentcontrol

import (
	"context"
	"testing"
)

type localFaultComparisonCase struct {
	name               string
	errorCode          string
	alternativeCost    float64
	blindRetrySucceeds bool
}

type localFaultComparisonMetrics struct {
	cost          float64
	retries       int
	wastedRetries int
	sloViolations int
	successes     int
}

// TestLocalFaultOptimizationComparison compares a blind same-resource retry
// with the fault-specific RepairDecision. Costs are synthetic attempt costs.
func TestLocalFaultOptimizationComparison(t *testing.T) {
	cases := []localFaultComparisonCase{
		{name: "gpu_oom", errorCode: ErrorCodeGPUOOM, alternativeCost: 1.0},
		{name: "cuda_mismatch", errorCode: ErrorCodeCUDAMismatch, alternativeCost: 1.0},
		{name: "resource_unavailable", errorCode: ErrorCodeResourceUnavailable, alternativeCost: 0.8},
		{name: "transient_deployment", errorCode: ErrorCodeTransientDeployment, alternativeCost: 2.0, blindRetrySucceeds: true},
		{name: "unknown_failure", errorCode: "UNKNOWN_FAILURE"},
	}

	var baseline, optimized localFaultComparisonMetrics
	t.Log("fault | blind retry | optimizer")
	for _, testCase := range cases {
		blind := localBlindFaultMetrics(testCase)
		optimizedMetrics, action := localOptimizedFaultMetrics(t, testCase)
		baseline = addLocalFaultComparisonMetrics(baseline, blind)
		optimized = addLocalFaultComparisonMetrics(optimized, optimizedMetrics)
		t.Logf(
			"%s | cost=%.1f retries=%d wasted=%d slo=%d successes=%d | cost=%.1f retries=%d wasted=%d slo=%d successes=%d action=%s",
			testCase.name,
			blind.cost, blind.retries, blind.wastedRetries, blind.sloViolations, blind.successes,
			optimizedMetrics.cost, optimizedMetrics.retries, optimizedMetrics.wastedRetries, optimizedMetrics.sloViolations, optimizedMetrics.successes,
			action,
		)
	}

	if optimized.cost >= baseline.cost || optimized.retries >= baseline.retries ||
		optimized.wastedRetries >= baseline.wastedRetries || optimized.sloViolations >= baseline.sloViolations ||
		optimized.successes <= baseline.successes {
		t.Fatalf("optimizer did not improve fault outcomes: baseline=%#v optimizer=%#v", baseline, optimized)
	}
	t.Logf(
		"TOTAL | blind cost=%.1f retries=%d wasted=%d slo=%d successes=%d | optimizer cost=%.1f retries=%d wasted=%d slo=%d successes=%d",
		baseline.cost, baseline.retries, baseline.wastedRetries, baseline.sloViolations, baseline.successes,
		optimized.cost, optimized.retries, optimized.wastedRetries, optimized.sloViolations, optimized.successes,
	)
}

// TestLocalFaultOptimizationBurstComparison stresses the policy with a burst
// of repeated resource faults plus transient and unknown failures.
func TestLocalFaultOptimizationBurstComparison(t *testing.T) {
	events := []localFaultComparisonCase{
		{name: "gpu_oom_1", errorCode: ErrorCodeGPUOOM, alternativeCost: 1.0},
		{name: "gpu_oom_2", errorCode: ErrorCodeGPUOOM, alternativeCost: 1.0},
		{name: "resource_unavailable_1", errorCode: ErrorCodeResourceUnavailable, alternativeCost: 0.8},
		{name: "cuda_mismatch_1", errorCode: ErrorCodeCUDAMismatch, alternativeCost: 1.0},
		{name: "transient_1", errorCode: ErrorCodeTransientDeployment, alternativeCost: 2.0, blindRetrySucceeds: true},
		{name: "resource_unavailable_2", errorCode: ErrorCodeResourceUnavailable, alternativeCost: 0.8},
		{name: "gpu_oom_3", errorCode: ErrorCodeGPUOOM, alternativeCost: 1.0},
		{name: "unknown_1", errorCode: "UNKNOWN_FAILURE"},
		{name: "transient_2", errorCode: ErrorCodeTransientDeployment, alternativeCost: 2.0, blindRetrySucceeds: true},
		{name: "cuda_mismatch_2", errorCode: ErrorCodeCUDAMismatch, alternativeCost: 1.0},
		{name: "resource_unavailable_3", errorCode: ErrorCodeResourceUnavailable, alternativeCost: 0.8},
		{name: "transient_3", errorCode: ErrorCodeTransientDeployment, alternativeCost: 2.0, blindRetrySucceeds: true},
	}

	var baseline, optimized localFaultComparisonMetrics
	for _, event := range events {
		baseline = addLocalFaultComparisonMetrics(baseline, localBlindFaultMetrics(event))
		metrics, _ := localOptimizedFaultMetrics(t, event)
		optimized = addLocalFaultComparisonMetrics(optimized, metrics)
	}

	if optimized.cost >= baseline.cost || optimized.retries >= baseline.retries ||
		optimized.wastedRetries >= baseline.wastedRetries || optimized.sloViolations >= baseline.sloViolations ||
		optimized.successes <= baseline.successes {
		t.Fatalf("optimizer did not survive fault burst: baseline=%#v optimizer=%#v", baseline, optimized)
	}
	t.Logf(
		"BURST %d events | blind cost=%.1f retries=%d wasted=%d slo=%d successes=%d | optimizer cost=%.1f retries=%d wasted=%d slo=%d successes=%d",
		len(events),
		baseline.cost, baseline.retries, baseline.wastedRetries, baseline.sloViolations, baseline.successes,
		optimized.cost, optimized.retries, optimized.wastedRetries, optimized.sloViolations, optimized.successes,
	)
}

func localBlindFaultMetrics(testCase localFaultComparisonCase) localFaultComparisonMetrics {
	metrics := localFaultComparisonMetrics{cost: 2.0, retries: 1}
	metrics.cost += 2.0
	if testCase.blindRetrySucceeds {
		metrics.successes = 1
		return metrics
	}
	metrics.wastedRetries = 1
	metrics.sloViolations = 1
	return metrics
}

func localOptimizedFaultMetrics(t *testing.T, testCase localFaultComparisonCase) (localFaultComparisonMetrics, string) {
	t.Helper()
	action := localFaultRepairAction(t, testCase.errorCode)
	metrics := localFaultComparisonMetrics{cost: 2.0}
	switch action {
	case ActionAdjustResourceRequirement, ActionRequestAlternativeResource:
		metrics.cost += testCase.alternativeCost
		metrics.retries = 1
		metrics.successes = 1
	case ActionRetryDeployment:
		metrics.cost += 2.0
		metrics.retries = 1
		metrics.successes = 1
	default:
		metrics.sloViolations = 1
	}
	return metrics, action
}

func localFaultRepairAction(t *testing.T, errorCode string) string {
	t.Helper()
	service := NewService()
	approved := createApprovedFlow(t, service)

	status := validDeploymentStatusEnvelope()
	status.Data.DeploymentStatus.State = DeploymentStateFailed
	status.Data.DeploymentStatus.ErrorCode = errorCode
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive failed status for %s: %v", errorCode, err)
	}

	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.Outcome = FeedbackOutcomeFailed
	feedback.Data.OptimizationFeedback.ErrorCode = errorCode
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive failed feedback for %s: %v", errorCode, err)
	}
	if flow.RepairDecision == nil || flow.Decision == nil || flow.Decision.DecisionID != approved.Decision.DecisionID {
		t.Fatalf("repair decision for %s = %#v", errorCode, flow.RepairDecision)
	}
	return flow.RepairDecision.Action
}

func addLocalFaultComparisonMetrics(left, right localFaultComparisonMetrics) localFaultComparisonMetrics {
	return localFaultComparisonMetrics{
		cost:          left.cost + right.cost,
		retries:       left.retries + right.retries,
		wastedRetries: left.wastedRetries + right.wastedRetries,
		sloViolations: left.sloViolations + right.sloViolations,
		successes:     left.successes + right.successes,
	}
}
