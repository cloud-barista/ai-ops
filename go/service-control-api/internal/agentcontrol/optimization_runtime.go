package agentcontrol

import (
	"context"
	"strings"
)

type OperationOptimizationRuntime interface {
	Optimize(context.Context, OperationOptimizationRequest) (OperationOptimizationResult, error)
}

type OperationOptimizationRequest struct {
	RunID          string
	RequestedAgent string
	CorrelationID  string
	TraceID        string
	Flow           Flow
}

type OperationOptimizationResult struct {
	AgentName    string          `json:"agent_name"`
	Source       string          `json:"source"`
	Status       string          `json:"status"`
	LatencyMS    int64           `json:"latency_ms"`
	RequestGuard GuardResult     `json:"request_guard"`
	ResultGuard  GuardResult     `json:"result_guard"`
	ScalingGuard GuardResult     `json:"scaling_guard"`
	Decision     ScalingDecision `json:"decision"`
	Message      string          `json:"message,omitempty"`
}

func validateOperationOptimizationResult(flow Flow, result OperationOptimizationResult) GuardResult {
	result = CanonicalizeOperationOptimizationResult(result)
	decision := result.Decision
	current, minimum, maximum := scalingReplicaBounds(flow)

	checks := []GuardCheck{
		{
			Name:   "operation_agent_status",
			Passed: result.Status == "completed",
			Reason: "Operation Agent must complete before its recommendation is accepted.",
		},
		{
			Name:   "agent_request_guard",
			Passed: result.RequestGuard.Status == GuardApproved,
			Reason: "Operation Agent request must be approved before recommendation validation.",
		},
		{
			Name:   "agent_result_guard",
			Passed: result.ResultGuard.Status == GuardApproved,
			Reason: "Operation Agent result must be approved before recommendation validation.",
		},
		{
			Name: "runtime_evidence",
			Passed: flow.DeploymentStatus != nil &&
				flow.DeploymentStatus.Data.DeploymentStatus.State == DeploymentStateRunning &&
				flow.OptimizationFeedback != nil,
			Reason: "A RUNNING deployment status and optimization feedback are required.",
		},
		{
			Name:   "scaling_action",
			Passed: isSupportedScalingAction(decision.Action),
			Reason: "Scaling action must be KEEP, SCALE_OUT, or SCALE_IN.",
		},
		{
			Name:   "current_replicas",
			Passed: decision.CurrentReplicas == current,
			Reason: "Scaling recommendation must use the observed replica count.",
		},
		{
			Name:   "replica_step",
			Passed: scalingReplicaStepIsBounded(decision),
			Reason: "Scaling recommendation may change replicas by at most one.",
		},
		{
			Name:   "replica_bounds",
			Passed: decision.DesiredReplicas >= minimum && decision.DesiredReplicas <= maximum,
			Reason: "Desired replicas must remain within the Application Profile bounds.",
		},
	}

	if flow.OptimizationFeedback != nil && isSupportedScalingAction(decision.Action) {
		checks = append(checks, operationEvidenceChecks(flow, decision, current, maximum)...)
	}

	status := GuardApproved
	for _, check := range checks {
		if !check.Passed {
			status = GuardRejected
			break
		}
	}
	return GuardResult{Status: status, Checks: checks}
}

func CanonicalizeOperationOptimizationResult(result OperationOptimizationResult) OperationOptimizationResult {
	result.Decision.Action = NormalizeScalingAction(result.Decision.Action)
	return result
}

func scalingReplicaStepIsBounded(decision ScalingDecision) bool {
	switch decision.Action {
	case ScalingActionKeep:
		return decision.DesiredReplicas == decision.CurrentReplicas
	case ScalingActionScaleOut:
		return decision.DesiredReplicas == decision.CurrentReplicas+1
	case ScalingActionScaleIn:
		return decision.DesiredReplicas == decision.CurrentReplicas-1
	default:
		return true
	}
}

func isSupportedScalingAction(action string) bool {
	return action == ScalingActionKeep || action == ScalingActionScaleOut || action == ScalingActionScaleIn
}

func operationEvidenceChecks(
	flow Flow,
	decision ScalingDecision,
	current int,
	maximum int,
) []GuardCheck {
	feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
	violations := scalingSLOViolations(flow, feedback)

	switch decision.Action {
	case ScalingActionScaleOut:
		return []GuardCheck{
			{
				Name:   "slo_evidence",
				Passed: len(violations) > 0 && len(decision.Evidence) > 0,
				Reason: "SCALE_OUT requires SLO evidence from optimization feedback.",
			},
			{
				Name:   "evidence_integrity",
				Passed: allEvidenceTrusted(decision.Evidence, violations),
				Reason: "SCALE_OUT evidence must contain only observed SLO violations.",
			},
		}
	case ScalingActionScaleIn:
		return []GuardCheck{
			{
				Name: "utilization_evidence",
				Passed: len(violations) == 0 &&
					feedback.Metrics.Resource.CPUAveragePercent < scalingLowUtilizationPercent &&
					feedback.Metrics.Resource.AcceleratorAveragePercent < scalingLowUtilizationPercent,
				Reason: "SCALE_IN requires healthy SLO and low trusted resource utilization.",
			},
			{
				Name:   "evidence_integrity",
				Passed: sameEvidence(decision.Evidence, scalingLowUtilizationEvidence(feedback.Metrics.Resource)),
				Reason: "SCALE_IN evidence must match the observed low-utilization metrics.",
			},
		}
	default:
		trustedEvidence := []string(nil)
		if current == maximum && len(violations) > 0 {
			trustedEvidence = violations
		}
		return []GuardCheck{{
			Name:   "evidence_integrity",
			Passed: allEvidenceTrusted(decision.Evidence, trustedEvidence),
			Reason: "KEEP evidence is allowed only for observed SLO violations at the replica maximum.",
		}}
	}
}

func allEvidenceTrusted(evidence []string, trusted []string) bool {
	for _, value := range evidence {
		if !containsString(trusted, strings.TrimSpace(value)) {
			return false
		}
	}
	return true
}

func sameEvidence(actual []string, expected []string) bool {
	if len(actual) != len(expected) {
		return false
	}
	for index, value := range actual {
		if strings.TrimSpace(value) != expected[index] {
			return false
		}
	}
	return true
}
