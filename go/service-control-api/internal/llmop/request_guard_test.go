package llmop

import (
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

func TestValidateRequestRejectsSensitiveParameterKeysIndependentOfValue(t *testing.T) {
	tests := []struct {
		name       string
		parameters map[string]any
	}{
		{name: "authorization", parameters: map[string]any{"authorization": "public"}},
		{name: "auth nested in array", parameters: map[string]any{"items": []any{map[string]any{"auth": "public"}}}},
		{name: "apikey", parameters: map[string]any{"apikey": "public"}},
		{name: "camel case api key", parameters: map[string]any{"apiKey": "public"}},
		{name: "access key", parameters: map[string]any{"accesskey": "public"}},
		{name: "token", parameters: map[string]any{"service_token": "public"}},
		{name: "password", parameters: map[string]any{"dbPassword": "public"}},
		{name: "secret", parameters: map[string]any{"client_secret": "public"}},
		{name: "credential", parameters: map[string]any{"credential": "public"}},
		{name: "private key", parameters: map[string]any{"privatekey": "public"}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := guardedRequest()
			request.Application.Parameters = test.parameters
			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())

			if decision.Valid {
				t.Fatal("expected a sensitive parameter key to be rejected")
			}
			assertFailedGuardCheck(t, decision, "sensitive_parameter_keys")
		})
	}
}

func TestValidateRequestAllowsNonCredentialParameterKeys(t *testing.T) {
	request := guardedRequest()
	request.Application.Parameters = map[string]any{
		"author":              "operator",
		"authentication_mode": "workload_identity",
		"resource_limits": map[string]any{
			"gpu_count": 1,
		},
	}

	decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
	if !decision.Valid {
		t.Fatalf("expected non-credential parameter keys to pass: %s", decision.Reason)
	}
}

func TestValidateRequestRejectsUnboundedIdentifiers(t *testing.T) {
	request := guardedRequest()
	request.TraceID = "trace with spaces"

	decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
	if decision.Valid {
		t.Fatal("expected an unbounded identifier to be rejected")
	}
	assertFailedGuardCheck(t, decision, "bounded_identifiers")
}

func TestValidateRequestRejectsInvalidPlanningConstraints(t *testing.T) {
	valid := PlanningConstraints{
		SourceProfileID:        "profile-guard",
		SourceRecommendationID: "recommendation-guard",
		RecommendationFeasible: true,
		CPUCoresMin:            4,
		MemoryMiBMin:           16 * 1024,
		GPUCountMin:            1,
		StorageGiBMin:          20,
		Accelerator:            "nvidia",
	}
	tests := []struct {
		name   string
		mutate func(*PlanningConstraints)
	}{
		{name: "missing source profile", mutate: func(value *PlanningConstraints) { value.SourceProfileID = "" }},
		{name: "missing source recommendation", mutate: func(value *PlanningConstraints) { value.SourceRecommendationID = "" }},
		{name: "infeasible recommendation", mutate: func(value *PlanningConstraints) { value.RecommendationFeasible = false }},
		{name: "zero cpu", mutate: func(value *PlanningConstraints) { value.CPUCoresMin = 0 }},
		{name: "cpu over ceiling", mutate: func(value *PlanningConstraints) { value.CPUCoresMin = maxCPUCount + 1 }},
		{name: "zero memory", mutate: func(value *PlanningConstraints) { value.MemoryMiBMin = 0 }},
		{name: "memory over ceiling", mutate: func(value *PlanningConstraints) { value.MemoryMiBMin = maxMemoryMi + 1 }},
		{name: "gpu over ceiling", mutate: func(value *PlanningConstraints) { value.GPUCountMin = maxGPUCount + 1 }},
		{name: "zero storage", mutate: func(value *PlanningConstraints) { value.StorageGiBMin = 0 }},
		{name: "storage over ceiling", mutate: func(value *PlanningConstraints) { value.StorageGiBMin = maxStorageMi/1024 + 1 }},
		{name: "gpu without nvidia accelerator", mutate: func(value *PlanningConstraints) { value.Accelerator = "none" }},
		{name: "cpu only with accelerator", mutate: func(value *PlanningConstraints) { value.GPUCountMin = 0 }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			constraints := valid
			test.mutate(&constraints)
			request := guardedRequest()
			request.Application.PlanningConstraints = &constraints

			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
			if decision.Valid {
				t.Fatal("expected invalid planning constraints to be rejected")
			}
			assertFailedGuardCheck(t, decision, "planning_constraints")
		})
	}
}

func TestValidateRequestRejectsInvalidRecommendedResources(t *testing.T) {
	valid := PlanningConstraints{
		SourceProfileID:        "profile-guard",
		SourceRecommendationID: "recommendation-guard",
		RecommendationFeasible: true,
		CPUCoresMin:            2,
		MemoryMiBMin:           4 * 1024,
		GPUCountMin:            0,
		StorageGiBMin:          20,
		Accelerator:            "none",
		RecommendedResources: &RecommendedResources{
			CPUCores:    4,
			MemoryMiB:   8 * 1024,
			GPUCount:    0,
			StorageGiB:  100,
			Accelerator: "none",
		},
	}
	tests := []struct {
		name   string
		mutate func(*RecommendedResources)
	}{
		{name: "zero cpu", mutate: func(value *RecommendedResources) {
			value.CPUCores = 0
		}},
		{name: "cpu below profile minimum", mutate: func(value *RecommendedResources) {
			value.CPUCores = 1
		}},
		{name: "memory below profile minimum", mutate: func(value *RecommendedResources) {
			value.MemoryMiB = 1
		}},
		{name: "storage below profile minimum", mutate: func(value *RecommendedResources) {
			value.StorageGiB = 1
		}},
		{name: "CPU profile with GPU recommendation", mutate: func(value *RecommendedResources) {
			value.GPUCount = 1
			value.Accelerator = "nvidia"
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			constraints := valid
			recommended := *valid.RecommendedResources
			test.mutate(&recommended)
			constraints.RecommendedResources = &recommended
			request := guardedRequest()
			request.Application.PlanningConstraints = &constraints

			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
			if decision.Valid {
				t.Fatal("expected invalid recommended resources to be rejected")
			}
			assertFailedGuardCheck(t, decision, "planning_constraints")
		})
	}
}

func TestValidateRequestRequiresDeploymentScopeOnMetricsAndLogs(t *testing.T) {
	tests := []struct {
		name    string
		context OperationContext
	}{
		{
			name: "metrics missing deployment id",
			context: OperationContext{MetricsSummary: &MetricsObservation{}},
		},
		{
			name: "metrics deployment mismatch",
			context: OperationContext{MetricsSummary: &MetricsObservation{DeploymentID: "dep-other"}},
		},
		{
			name: "log missing deployment id",
			context: OperationContext{DeploymentLogs: &LogObservation{Items: []appdeploy.DeploymentLog{{Message: "scoped log"}}}},
		},
		{
			name: "log deployment mismatch",
			context: OperationContext{DeploymentLogs: &LogObservation{Items: []appdeploy.DeploymentLog{{DeploymentID: "dep-other"}}}},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := guardedRequest()
			request.OperationContext = test.context
			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())

			if decision.Valid {
				t.Fatal("expected unscoped or mismatched observation to be rejected")
			}
			assertFailedGuardCheck(t, decision, "observation_scope")
		})
	}
}

func TestValidateRequestRequiresDeploymentScopeOnMonitoringAlarms(t *testing.T) {
	for _, deploymentID := range []string{"", "dep-other"} {
		request := guardedRequest()
		request.OperationContext.MonitoringSummary = scopedMonitoringSummary(deploymentID)

		decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
		if decision.Valid {
			t.Fatalf("expected alarm deployment_id %q to be rejected", deploymentID)
		}
		assertFailedGuardCheck(t, decision, "observation_scope")
	}
}

func TestValidateRequestAllowsGlobalMonitoringAggregateWhenAlarmsAreScoped(t *testing.T) {
	request := guardedRequest()
	request.OperationContext.MonitoringSummary = scopedMonitoringSummary("dep-trusted")
	request.OperationContext.MonitoringSummary.Summary.Deployments.Total = 2
	request.OperationContext.MonitoringSummary.Summary.Deployments.ByStatus = map[string]int{
		"RUNNING": 2,
	}
	request.OperationContext.MonitoringSummary.Summary.RuntimeHealth = []appdeploy.RuntimeHealthSnapshot{{
		TargetProfileID: "target-global",
	}}

	decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
	if !decision.Valid {
		t.Fatalf("global aggregate is projected out by the scoped prompt: %s", decision.Reason)
	}
}

func TestValidateRequestAcceptsFullyScopedObservations(t *testing.T) {
	request := guardedRequest()
	request.OperationContext = OperationContext{
		MonitoringSummary: scopedMonitoringSummary("dep-trusted"),
		DeploymentLogs: &LogObservation{Items: []appdeploy.DeploymentLog{{DeploymentID: "dep-trusted"}}},
		MetricsSummary: &MetricsObservation{DeploymentID: "dep-trusted"},
	}

	decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
	if !decision.Valid {
		t.Fatalf("expected fully scoped observations to pass: %s", decision.Reason)
	}
}

func guardedRequest() Request {
	return Request{
		APIVersion:    APIVersion,
		RequestID:     "req-guard",
		CorrelationID: "corr-guard",
		CandidateID:   "qwen-guard",
		RequestedBy:   "guard-tester",
		Application: Application{
			AppVersionID: "app-version-guard",
			DeploymentID: "dep-trusted",
			UserRequest:  "prepare a deployment manifest",
		},
		Policy: RequestPolicy{Mode: ModePrepareOnly},
	}
}

func guardPolicyWithoutSensitiveKeys() plannerguard.Policy {
	return plannerguard.Policy{
		Version:           "test",
		MaxRequestLength:  1000,
		AllowedRequesters: []string{"guard-tester"},
	}
}

func scopedMonitoringSummary(deploymentID string) *MonitoringObservation {
	return &MonitoringObservation{
		Summary: appdeploy.MonitoringSummaryResponse{
			Status: "degraded",
			Alarms: []appdeploy.DeploymentAlarmSummary{{
				LatestDeploymentID: deploymentID,
				ErrorCode:          "LATENCY_SLO",
			}},
		},
	}
}

func assertFailedGuardCheck(t *testing.T, decision plannerguard.Decision, name string) {
	t.Helper()
	for _, check := range decision.Checks {
		if check.Name == name {
			if check.Passed {
				t.Fatalf("expected guard check %s to fail", name)
			}
			return
		}
	}
	t.Fatalf("guard check %s was not recorded", name)
}
