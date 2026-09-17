package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestFocusedServerHealthAndOpenAPI(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	health := performJSONRequest(t, server, http.MethodGet, "/healthz", "")
	if health.Code != http.StatusOK || !strings.Contains(health.Body.String(), `"service":"deployment-agent"`) {
		t.Fatalf("focused health: code=%d body=%s", health.Code, health.Body.String())
	}
	request := httptest.NewRequest(http.MethodGet, "/openapi.yaml", nil)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("focused OpenAPI status = %d: %s", response.Code, response.Body.String())
	}
	var document map[string]any
	if err := yaml.Unmarshal(response.Body.Bytes(), &document); err != nil {
		t.Fatalf("parse focused OpenAPI: %v", err)
	}
	paths := document["paths"].(map[string]any)
	if _, ok := paths[pathDeploymentPlans]; !ok {
		t.Fatalf("focused OpenAPI is missing %s", pathDeploymentPlans)
	}
}

func TestFocusedServerPlansFromLLMOpAndResourceOpsResults(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if flow.State != agentcontrol.StateDecisionApproved || flow.Decision == nil ||
		flow.Decision.Action != agentcontrol.ActionDeploy || flow.DeploymentPlan == nil {
		t.Fatalf("unexpected focused flow: %#v", flow)
	}
	if flow.ProfileID != "profile-focused-001" || flow.DeploymentPlan.SelectedCandidateID != "node-a" {
		t.Fatalf("identity or candidate was not preserved: %#v", flow.DeploymentPlan)
	}
	if flow.DeploymentPlan.DesiredInfrastructure.CPUCoresPerNode != 8 ||
		flow.DeploymentPlan.DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice != 12000 {
		t.Fatalf("deployment requirements were not preserved: %#v", flow.DeploymentPlan.DesiredInfrastructure)
	}
}

func TestFocusedServerReturnsRetryWhenResourceOpsHasNoCandidate(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	request.ResourceRecommendation.Recommendations = []ResourceOpsRankedResource{}
	request.ResourceRecommendation.Summary.Returned = 0
	request.ResourceRecommendation.Summary.Feasible = 0
	request.ResourceRecommendation.Summary.Excluded = 1
	request.ResourceRecommendation.Warnings = []string{"NO_FEASIBLE_RESOURCE"}
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if flow.State != agentcontrol.StateRetryRequired || flow.Decision == nil ||
		flow.Decision.Action != agentcontrol.ActionRetry || flow.Decision.CorrectionRequest == nil {
		t.Fatalf("no-candidate result did not produce RETRY: %#v", flow)
	}
}

func TestFocusedServerRejectsMismatchedCorrelation(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	request.CorrelationID = "different-flow"
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want 422: %s", response.Code, response.Body.String())
	}
}

func TestFocusedServerDoesNotExposeLegacyAnalysisAndRecommendation(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	for _, path := range []string{
		"/api/v1/ops-llm/select",
		"/api/v1/apps/vm-suitability",
		"/api/v1/agent-control/application-analysis-requests",
		"/api/v1/agent-control/application-contexts",
		"/api/v1/agent-control/resource-recommendations",
		"/api/v1/agent-control/trusted-automation-runs",
	} {
		response := performJSONRequest(t, server, http.MethodPost, path, `{}`)
		if response.Code != http.StatusNotFound {
			t.Fatalf("legacy path %s status = %d, want 404", path, response.Code)
		}
	}
}

func focusedPlanningRequest() DeploymentPlanningRequest {
	cpu := 85.0
	memory := 90.0
	storage := 80.0
	accelerator := 92.0
	return DeploymentPlanningRequest{
		SchemaVersion: focusedContractVersion,
		CorrelationID: "flow-focused-001",
		TraceID:       "trace-focused-001",
		ApplicationProfile: agentcontrol.ApplicationProfile{
			ProfileID:  "profile-focused-001",
			AppID:      "chat-service",
			AppVersion: "1.0.0",
			Workload: agentcontrol.WorkloadProfile{
				TaskType: "LLM_INFERENCE", RequestPattern: "ONLINE", ExpectedRPS: 5,
			},
			Requirements: agentcontrol.ApplicationRequirements{
				Compute: agentcontrol.ComputeRequirements{CPUCoresMin: 8, MemoryMiBMin: 32768, StorageGiBMin: 20},
				Accelerator: agentcontrol.AcceleratorRequirements{
					Required: true, Type: "GPU", CountMin: 1, MemoryMiBMinPerDevice: 12000,
				},
				Deployment: agentcontrol.DeploymentRequirements{ReplicasMin: 1, ReplicasMax: 2, Isolation: "ONE_MAJOR_APP_PER_VM"},
			},
			Analysis: agentcontrol.AnalysisSummary{Confidence: 0.9},
		},
		ResourceRecommendation: ResourceOpsRecommendation{
			SchemaVersion: focusedContractVersion,
			RequestID:     "flow-focused-001",
			ProfileID:     "profile-focused-001",
			Source:        "prometheus:http://prometheus.example",
			ObservedAt:    "2026-08-27T06:00:00Z",
			Query:         json.RawMessage(`{"profile_id":"profile-focused-001"}`),
			Recommendations: []ResourceOpsRankedResource{{
				Rank: 1, Score: 88,
				Resource: ResourceOpsProfile{
					ResourceID: "node-a", Address: "10.0.0.1", Healthy: true,
					ObservedAt: "2026-08-27T06:00:00Z", Warnings: []string{},
				},
				ScoreBreakdown: ResourceOpsScoreBreakdown{
					CPU: &cpu, Memory: &memory, Storage: &storage, Accelerator: &accelerator,
					Health: 100, Total: 88,
				},
				Reasons: []ResourceOpsReason{{Code: "FEASIBLE", Message: "Resource satisfies the profile."}},
			}},
			ExcludedResources: []ResourceOpsExcludedResource{},
			Summary:           ResourceOpsSummary{Discovered: 1, Feasible: 1, Returned: 1},
			Warnings:          []string{},
		},
	}
}
