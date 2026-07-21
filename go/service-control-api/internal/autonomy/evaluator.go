package autonomy

import (
	"fmt"
	"strings"
)

func Evaluate(input EvaluationInput) Evaluation {
	result := Evaluation{Metric: input.Metric, Violations: []Violation{}}
	deploymentFailed := failedStatus(input.DeploymentStatus)
	monitoringFailed := unhealthyStatus(input.MonitoringStatus)
	runtimeFailed := unhealthyStatus(input.RuntimeHealth)
	result.FailureEvidence = deploymentFailed || monitoringFailed || runtimeFailed

	if deploymentFailed {
		result.Violations = append(result.Violations, Violation{Code: "deployment_failed", Reason: "AppDeploy reports a failed deployment state"})
	}
	if monitoringFailed {
		result.Violations = append(result.Violations, Violation{Code: "monitoring_degraded", Reason: "AppDeploy monitoring status is degraded or unavailable"})
	}
	if runtimeFailed {
		result.Violations = append(result.Violations, Violation{Code: "runtime_unhealthy", Reason: "target runtime health is degraded or unavailable"})
	}

	if input.Metric != nil && !input.Metric.Timestamp.IsZero() && input.MaxMetricAge >= 0 {
		age := input.Now.Sub(input.Metric.Timestamp)
		result.EvidenceFresh = age >= 0 && age <= input.MaxMetricAge
	}
	if result.EvidenceFresh {
		metric := input.Metric
		if metric.LatencyMS > input.Policy.MaxLatencyMS {
			result.Violations = append(result.Violations, Violation{Code: "latency_slo_exceeded", Observed: metric.LatencyMS, Threshold: input.Policy.MaxLatencyMS, Reason: "latency exceeds the configured maximum"})
		}
		if metric.ThroughputRPS < input.Policy.MinThroughputRPS {
			result.Violations = append(result.Violations, Violation{Code: "throughput_slo_breached", Observed: metric.ThroughputRPS, Threshold: input.Policy.MinThroughputRPS, Reason: "throughput is below the configured minimum"})
		}
		if metric.RequestCount > 0 {
			errorRate := float64(metric.ErrorCount) / float64(metric.RequestCount)
			if errorRate > input.Policy.MaxErrorRate {
				result.Violations = append(result.Violations, Violation{Code: "error_rate_slo_exceeded", Observed: errorRate, Threshold: input.Policy.MaxErrorRate, Reason: "error rate exceeds the configured maximum"})
			}
		}
	}

	if !result.EvidenceFresh && !result.FailureEvidence {
		result.Status = EvaluationInsufficientEvidence
		result.Reason = "no fresh metric or explicit failure evidence is available"
		result.ConsecutiveViolations = input.PreviousConsecutive
		return result
	}
	if len(result.Violations) > 0 {
		result.Status = EvaluationViolated
		result.ConsecutiveViolations = input.PreviousConsecutive + 1
		result.Reason = fmt.Sprintf("%d SLO or runtime violation(s) detected", len(result.Violations))
		return result
	}
	result.Status = EvaluationHealthy
	result.ConsecutiveViolations = 0
	result.Reason = "fresh evidence is within the configured SLO"
	return result
}

func failedStatus(status string) bool {
	switch strings.ToUpper(strings.TrimSpace(status)) {
	case "FAILED", "ERROR", "DEPLOYMENT_FAILED", "STOP_FAILED":
		return true
	default:
		return false
	}
}

func unhealthyStatus(status string) bool {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "degraded", "unavailable", "failed", "error", "unhealthy":
		return true
	default:
		return false
	}
}
