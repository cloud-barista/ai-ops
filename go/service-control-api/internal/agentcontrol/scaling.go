package agentcontrol

import (
	"fmt"
	"time"
)

const scalingLowUtilizationPercent = 30.0

func evaluateScalingDecision(flow Flow, now time.Time) *ScalingDecision {
	if flow.DeploymentStatus == nil {
		return nil
	}

	current, minimum, maximum := scalingReplicaBounds(flow)
	decision := &ScalingDecision{
		Action:          ScalingActionNoAction,
		CurrentReplicas: current,
		DesiredReplicas: current,
		CreatedAt:       now.UTC().Format(time.RFC3339Nano),
	}

	status := flow.DeploymentStatus.Data.DeploymentStatus
	if status.State != DeploymentStateRunning {
		decision.Reason = "Deployment is not RUNNING; scaling is not evaluated."
		decision.Evidence = []string{"deployment_state=" + status.State}
		return decision
	}
	if flow.OptimizationFeedback == nil {
		decision.Reason = "Runtime metrics are required before scaling evaluation."
		decision.Evidence = []string{"optimization_feedback=missing"}
		return decision
	}

	feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
	violations := scalingSLOViolations(flow, feedback)
	if len(violations) > 0 {
		decision.Evidence = violations
		if current < maximum {
			decision.Action = ScalingActionScaleOut
			decision.DesiredReplicas = current + 1
			decision.Reason = "SLO evidence requires one bounded replica increase."
		} else {
			decision.Reason = "SLO is violated, but replicas are already at the configured maximum."
		}
		return decision
	}

	resource := feedback.Metrics.Resource
	if current > minimum &&
		resource.CPUAveragePercent < scalingLowUtilizationPercent &&
		resource.AcceleratorAveragePercent < scalingLowUtilizationPercent {
		decision.Action = ScalingActionScaleIn
		decision.DesiredReplicas = current - 1
		decision.Reason = "Healthy SLO and sustained low utilization allow one bounded replica decrease."
		decision.Evidence = []string{
			fmt.Sprintf("cpu_average_percent=%.2f", resource.CPUAveragePercent),
			fmt.Sprintf(
				"accelerator_average_percent=%.2f",
				resource.AcceleratorAveragePercent,
			),
		}
		return decision
	}

	decision.Reason = "SLO and utilization remain within the configured operating range."
	return decision
}

func scalingReplicaBounds(flow Flow) (current int, minimum int, maximum int) {
	current = 1
	if flow.DeploymentPlan != nil && flow.DeploymentPlan.InferenceConfiguration.Replicas > 0 {
		current = flow.DeploymentPlan.InferenceConfiguration.Replicas
	}

	minimum = 1
	maximum = current
	if flow.ApplicationContext != nil {
		requirements := flow.ApplicationContext.Data.ApplicationProfile.Requirements.Deployment
		if requirements.ReplicasMin > 0 {
			minimum = requirements.ReplicasMin
		}
		if requirements.ReplicasMax > 0 {
			maximum = requirements.ReplicasMax
		}
	}
	if maximum < minimum {
		maximum = minimum
	}
	if current < minimum {
		current = minimum
	}
	if current > maximum {
		current = maximum
	}
	return current, minimum, maximum
}

func scalingSLOViolations(flow Flow, feedback OptimizationFeedback) []string {
	violations := append([]string(nil), feedback.SLOViolations...)
	if flow.ApplicationContext == nil {
		return violations
	}

	slo := flow.ApplicationContext.Data.ApplicationProfile.Requirements.SLO
	if slo.LatencyP95MSMax > 0 &&
		feedback.Metrics.Inference.LatencyP95MS > slo.LatencyP95MSMax &&
		!containsString(violations, "latency_p95_ms") {
		violations = append(violations, "latency_p95_ms")
	}
	if slo.ThroughputRPSMin > 0 &&
		feedback.Metrics.Inference.ThroughputRPS < slo.ThroughputRPSMin &&
		!containsString(violations, "throughput_rps") {
		violations = append(violations, "throughput_rps")
	}
	return violations
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
