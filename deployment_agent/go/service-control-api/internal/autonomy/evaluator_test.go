package autonomy

import (
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestEvaluateSLOBoundariesAndViolations(t *testing.T) {
	now := time.Date(2026, 7, 21, 3, 10, 0, 0, time.UTC)
	policy := SLOPolicy{MaxLatencyMS: 500, MinThroughputRPS: 1, MaxErrorRate: 0.05}
	tests := []struct {
		name       string
		metric     appdeploy.InferenceMetricRecord
		wantStatus EvaluationStatus
		wantCode   string
	}{
		{name: "equal boundaries are healthy", metric: metricAt(now, 500, 1, 100, 5), wantStatus: EvaluationHealthy},
		{name: "latency above max", metric: metricAt(now, 500.1, 1, 100, 5), wantStatus: EvaluationViolated, wantCode: "latency_slo_exceeded"},
		{name: "throughput below min", metric: metricAt(now, 500, 0.99, 100, 5), wantStatus: EvaluationViolated, wantCode: "throughput_slo_breached"},
		{name: "error rate above max", metric: metricAt(now, 500, 1, 100, 6), wantStatus: EvaluationViolated, wantCode: "error_rate_slo_exceeded"},
		{name: "zero requests avoid division", metric: metricAt(now, 500, 1, 0, 4), wantStatus: EvaluationHealthy},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := Evaluate(EvaluationInput{Now: now, Metric: &test.metric, DeploymentStatus: "RUNNING", MonitoringStatus: "ok", RuntimeHealth: "ok", Policy: policy, MaxMetricAge: time.Minute, PreviousConsecutive: 2})
			if got.Status != test.wantStatus {
				t.Fatalf("status=%s want=%s evaluation=%#v", got.Status, test.wantStatus, got)
			}
			if test.wantCode != "" && !hasViolation(got.Violations, test.wantCode) {
				t.Fatalf("missing violation %q: %#v", test.wantCode, got.Violations)
			}
			if test.wantStatus == EvaluationHealthy && got.ConsecutiveViolations != 0 {
				t.Fatalf("healthy evaluation must reset consecutive count: %#v", got)
			}
		})
	}
}

func TestEvaluateEvidenceFreshnessAndFailureState(t *testing.T) {
	now := time.Date(2026, 7, 21, 3, 10, 0, 0, time.UTC)
	stale := metricAt(now.Add(-61*time.Second), 900, 0.1, 100, 20)
	got := Evaluate(EvaluationInput{Now: now, Metric: &stale, DeploymentStatus: "RUNNING", MonitoringStatus: "ok", RuntimeHealth: "ok", Policy: SLOPolicy{MaxLatencyMS: 500}, MaxMetricAge: time.Minute, PreviousConsecutive: 2})
	if got.Status != EvaluationInsufficientEvidence || got.EvidenceFresh {
		t.Fatalf("stale metric must be insufficient evidence: %#v", got)
	}

	failed := Evaluate(EvaluationInput{Now: now, DeploymentStatus: "FAILED", MonitoringStatus: "degraded", RuntimeHealth: "unavailable", Policy: SLOPolicy{MaxLatencyMS: 500}, MaxMetricAge: time.Minute, PreviousConsecutive: 1})
	if failed.Status != EvaluationViolated || !failed.FailureEvidence || failed.ConsecutiveViolations != 2 {
		t.Fatalf("failed deployment must be usable failure evidence: %#v", failed)
	}
}

func TestEvaluateRejectsMetricReusedAfterAction(t *testing.T) {
	now := time.Date(2026, 7, 21, 3, 10, 0, 0, time.UTC)
	metric := metricAt(now.Add(-time.Second), 900, 0.5, 100, 10)
	got := Evaluate(EvaluationInput{
		Now: now, Metric: &metric, DeploymentStatus: "RUNNING", MonitoringStatus: "ok", RuntimeHealth: "ok",
		Policy: SLOPolicy{MaxLatencyMS: 500, MinThroughputRPS: 1, MaxErrorRate: 0.05}, MaxMetricAge: time.Minute,
		EvidenceNotBefore: now,
	})
	if got.Status != EvaluationInsufficientEvidence || got.EvidenceFresh {
		t.Fatalf("pre-Action metric must not be reused: %#v", got)
	}
}

func TestEvaluateRejectsReusedFailureStateAfterAction(t *testing.T) {
	now := time.Date(2026, 7, 21, 3, 10, 0, 0, time.UTC)
	got := Evaluate(EvaluationInput{
		Now: now, DeploymentStatus: "FAILED", MonitoringStatus: "degraded", RuntimeHealth: "unavailable",
		Policy: SLOPolicy{MaxLatencyMS: 500}, MaxMetricAge: time.Minute, EvidenceNotBefore: now,
		FailureEvidenceFresh: false,
	})
	if got.Status != EvaluationInsufficientEvidence || got.FailureEvidence {
		t.Fatalf("unchanged failure state must not be reused after an Action: %#v", got)
	}
}

func TestConfigValidate(t *testing.T) {
	valid := DefaultConfig()
	valid.DeploymentID = "dep-1"
	if err := valid.Validate(); err != nil {
		t.Fatalf("default config should validate: %v", err)
	}

	invalid := valid
	invalid.Mode = Mode("unsafe")
	if err := invalid.Validate(); err == nil {
		t.Fatal("expected invalid mode error")
	}
	invalid = valid
	invalid.MaxMetricAgeSeconds = invalid.PollIntervalSeconds - 1
	if err := invalid.Validate(); err == nil {
		t.Fatal("expected metric age below poll interval error")
	}
	invalid = valid
	invalid.SLO.MaxErrorRate = 1.01
	if err := invalid.Validate(); err == nil {
		t.Fatal("expected invalid error rate")
	}
}

func metricAt(at time.Time, latency float64, throughput float64, requests int, errors int) appdeploy.InferenceMetricRecord {
	return appdeploy.InferenceMetricRecord{MetricID: "metric-1", DeploymentID: "dep-1", Timestamp: at, LatencyMS: latency, ThroughputRPS: throughput, RequestCount: requests, ErrorCount: errors}
}

func hasViolation(violations []Violation, code string) bool {
	for _, violation := range violations {
		if violation.Code == code {
			return true
		}
	}
	return false
}
