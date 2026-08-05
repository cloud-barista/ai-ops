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
	Decision     ScalingDecision `json:"decision"`
	Message      string          `json:"message,omitempty"`
}

func validateOperationOptimizationResult(flow Flow, result OperationOptimizationResult) GuardResult {
	decision := result.Decision
	decision.Action = normalizeScalingAction(decision.Action)
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
			Passed: decision.Action == ScalingActionKeep || decision.Action == ScalingActionScaleOut || decision.Action == ScalingActionScaleIn,
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

	if flow.OptimizationFeedback != nil {
		checks = append(checks, operationEvidenceCheck(flow, decision))
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

func scalingReplicaStepIsBounded(decision ScalingDecision) bool {
	switch decision.Action {
	case ScalingActionKeep:
		return decision.DesiredReplicas == decision.CurrentReplicas
	case ScalingActionScaleOut:
		return decision.DesiredReplicas == decision.CurrentReplicas+1
	case ScalingActionScaleIn:
		return decision.DesiredReplicas == decision.CurrentReplicas-1
	default:
		return false
	}
}

func operationEvidenceCheck(flow Flow, decision ScalingDecision) GuardCheck {
	feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
	violations := scalingSLOViolations(flow, feedback)

	switch decision.Action {
	case ScalingActionScaleOut:
		return GuardCheck{
			Name:   "slo_evidence",
			Passed: hasTrustedSLOEvidence(violations, decision.Evidence),
			Reason: "SCALE_OUT requires matching SLO evidence from optimization feedback.",
		}
	case ScalingActionScaleIn:
		return GuardCheck{
			Name: "utilization_evidence",
			Passed: len(violations) == 0 &&
				feedback.Metrics.Resource.CPUAveragePercent < scalingLowUtilizationPercent &&
				feedback.Metrics.Resource.AcceleratorAveragePercent < scalingLowUtilizationPercent,
			Reason: "SCALE_IN requires healthy SLO and low trusted resource utilization.",
		}
	default:
		return GuardCheck{Name: "scaling_evidence", Passed: true, Reason: "KEEP preserves the observed replica count."}
	}
}

func hasTrustedSLOEvidence(violations []string, evidence []string) bool {
	if len(violations) == 0 {
		return false
	}
	for _, value := range evidence {
		if containsString(violations, strings.TrimSpace(value)) {
			return true
		}
	}
	return false
}
