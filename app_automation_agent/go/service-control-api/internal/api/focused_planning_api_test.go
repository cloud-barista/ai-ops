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

func TestFocusedServerRejectsChangedSameCorrelationResourceSnapshot(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	firstBody, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	first := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(firstBody))
	if first.Code != http.StatusCreated {
		t.Fatalf("first status = %d: %s", first.Code, first.Body.String())
	}
	available := 0.0
	request.ResourceRecommendation.Recommendations[0].Resource.CPU.AvailableCores = &available
	changedBody, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	changed := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(changedBody))
	if changed.Code != http.StatusUnprocessableEntity || strings.Contains(changed.Body.String(), `"action":"DEPLOY"`) {
		t.Fatalf("changed same-correlation snapshot reused stale DEPLOY: code=%d body=%s", changed.Code, changed.Body.String())
	}
}

func TestFocusedServerRejectsAnyChangedSameCorrelationInputBeforeMutation(t *testing.T) {
	for _, test := range []struct {
		name   string
		change func(*DeploymentPlanningRequest)
	}{
		{"still-feasible raw availability", func(request *DeploymentPlanningRequest) {
			available := 11.0
			request.ResourceRecommendation.Recommendations[0].Resource.CPU.AvailableCores = &available
		}},
		{"application profile", func(request *DeploymentPlanningRequest) {
			request.ApplicationProfile.Requirements.Compute.CPUCoresMin = 9
		}},
		{"deployment context", func(request *DeploymentPlanningRequest) {
			request.DeploymentContext = &agentcontrol.DeploymentContext{
				DeploymentRef: "existing-app", SpecDrift: true,
			}
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := NewFocusedServer(NewServerConfig())
			request := focusedPlanningRequest()
			firstBody, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			first := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(firstBody))
			if first.Code != http.StatusCreated {
				t.Fatalf("first status = %d: %s", first.Code, first.Body.String())
			}
			test.change(&request)
			changedBody, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			changed := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(changedBody))
			if changed.Code != http.StatusUnprocessableEntity || strings.Contains(changed.Body.String(), `"action":"DEPLOY"`) {
				t.Fatalf("changed request reused original approval: code=%d body=%s", changed.Code, changed.Body.String())
			}
			stored := performJSONRequest(t, server, http.MethodGet, "/api/v1/agent-control/flows/"+request.CorrelationID, "")
			if stored.Code != http.StatusOK {
				t.Fatalf("stored Flow status = %d: %s", stored.Code, stored.Body.String())
			}
			var flow agentcontrol.Flow
			if err := json.Unmarshal(stored.Body.Bytes(), &flow); err != nil {
				t.Fatal(err)
			}
			if flow.ApplicationContext == nil || flow.ApplicationContext.Data.ApplicationProfile.Requirements.Compute.CPUCoresMin != 8 ||
				flow.DeploymentContext != nil || flow.ResourceRecommendation == nil ||
				flow.ResourceRecommendation.Data.ResourceRecommendation.SelectedCandidateID != "node-a" {
				t.Fatalf("changed request mutated stored input: %#v", flow)
			}
		})
	}
}

func TestFocusedServerExactRetryDoesNotReevaluateSyntheticMessages(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	request.DeploymentContext = &agentcontrol.DeploymentContext{DeploymentRef: "existing-app", SpecDrift: true}
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	first := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body))
	if first.Code != http.StatusCreated {
		t.Fatalf("first status = %d: %s", first.Code, first.Body.String())
	}
	var original agentcontrol.Flow
	if err := json.Unmarshal(first.Body.Bytes(), &original); err != nil {
		t.Fatal(err)
	}
	if original.Decision == nil || original.Decision.Action != agentcontrol.ActionUpdate || original.DeploymentContext == nil {
		t.Fatalf("first request did not atomically apply deployment context: %#v", original)
	}
	second := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body))
	if second.Code != http.StatusOK || second.Header().Get("Idempotent-Replayed") != "true" {
		t.Fatalf("replay status/header = %d/%q: %s", second.Code, second.Header().Get("Idempotent-Replayed"), second.Body.String())
	}
	var replayed agentcontrol.Flow
	if err := json.Unmarshal(second.Body.Bytes(), &replayed); err != nil {
		t.Fatal(err)
	}
	if replayed.Decision == nil || original.Decision == nil || replayed.Decision.CreatedAt != original.Decision.CreatedAt ||
		replayed.Decision.Action != agentcontrol.ActionUpdate || replayed.DeploymentContext == nil ||
		len(replayed.ManifestRevisions) != len(original.ManifestRevisions) ||
		replayed.ApplicationContext.OccurredAt != original.ApplicationContext.OccurredAt ||
		replayed.ResourceRecommendation.OccurredAt != original.ResourceRecommendation.OccurredAt {
		t.Fatalf("exact retry changed planning evidence: first=%#v replay=%#v", original, replayed)
	}
}

func TestFocusedServerRejectsOptionalAcceleratorWithGPUCount(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	request.ApplicationProfile.Requirements.Accelerator.Required = false
	request.ApplicationProfile.Requirements.Accelerator.CountMin = 2
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body))
	if response.Code != http.StatusUnprocessableEntity || strings.Contains(response.Body.String(), `"action":"DEPLOY"`) {
		t.Fatalf("contradictory GPU profile produced a plan: code=%d body=%s", response.Code, response.Body.String())
	}
	stored := performJSONRequest(t, server, http.MethodGet, "/api/v1/agent-control/flows/"+request.CorrelationID, "")
	if stored.Code != http.StatusNotFound {
		t.Fatalf("contradictory profile created Flow: code=%d body=%s", stored.Code, stored.Body.String())
	}
}

func TestFocusedServerPlansCPUOnlyProfileWithoutAccelerators(t *testing.T) {
	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	request.ApplicationProfile.Requirements.Accelerator = agentcontrol.AcceleratorRequirements{}
	request.ResourceRecommendation.Recommendations[0].Resource.Accelerators = []ResourceOpsAccelerator{}
	request.ResourceRecommendation.Recommendations[0].ScoreBreakdown.Accelerator = nil
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body))
	if response.Code != http.StatusCreated {
		t.Fatalf("CPU-only status = %d, want 201: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if flow.DeploymentPlan == nil || flow.Decision == nil ||
		flow.Decision.Action != agentcontrol.ActionDeploy ||
		flow.DeploymentPlan.DesiredInfrastructure.Accelerator.Count != 0 ||
		flow.DeploymentPlan.DesiredInfrastructure.Accelerator.Type != "" {
		t.Fatalf("CPU-only profile produced an accelerator requirement: %#v", flow)
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

func TestFocusedServerRetriesWhenRankedCapacityDoesNotSupportProfile(t *testing.T) {
	tests := []struct {
		name       string
		change     func(*DeploymentPlanningRequest)
		wantReason string
	}{
		{"cpu capacity", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.CPU.LogicalCores = 0
		}, "CPU_CAPACITY_INSUFFICIENT"},
		{"cpu availability missing", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.CPU.AvailableCores = nil
		}, "CPU_AVAILABLE_INSUFFICIENT_OR_UNKNOWN"},
		{"cpu availability insufficient", func(request *DeploymentPlanningRequest) {
			available := 7.5
			request.ResourceRecommendation.Recommendations[0].Resource.CPU.AvailableCores = &available
		}, "CPU_AVAILABLE_INSUFFICIENT_OR_UNKNOWN"},
		{"cpu availability exceeds capacity", func(request *DeploymentPlanningRequest) {
			available := 32.0
			request.ResourceRecommendation.Recommendations[0].Resource.CPU.AvailableCores = &available
		}, "CPU_AVAILABILITY_EXCEEDS_CAPACITY"},
		{"memory capacity", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Memory.TotalMiB = 0
		}, "MEMORY_CAPACITY_INSUFFICIENT"},
		{"memory availability missing", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Memory.AvailableMiB = nil
		}, "MEMORY_AVAILABLE_INSUFFICIENT_OR_UNKNOWN"},
		{"memory availability exceeds capacity", func(request *DeploymentPlanningRequest) {
			available := 70000.0
			request.ResourceRecommendation.Recommendations[0].Resource.Memory.AvailableMiB = &available
		}, "MEMORY_AVAILABILITY_EXCEEDS_CAPACITY"},
		{"storage capacity", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Storage.TotalGiB = 0
		}, "STORAGE_CAPACITY_INSUFFICIENT"},
		{"storage availability missing", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Storage.AvailableGiB = nil
		}, "STORAGE_AVAILABLE_INSUFFICIENT_OR_UNKNOWN"},
		{"storage availability exceeds capacity", func(request *DeploymentPlanningRequest) {
			available := 120.0
			request.ResourceRecommendation.Recommendations[0].Resource.Storage.AvailableGiB = &available
		}, "STORAGE_AVAILABILITY_EXCEEDS_CAPACITY"},
		{"gpu type mismatch", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Accelerators[0].Type = "TPU"
		}, "ACCELERATOR_CAPACITY_INSUFFICIENT"},
		{"gpu capacity", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Accelerators[0].MemoryTotalMiB = 0
		}, "ACCELERATOR_CAPACITY_INSUFFICIENT"},
		{"gpu availability missing", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Accelerators[0].MemoryAvailableMiB = nil
		}, "ACCELERATOR_AVAILABLE_INSUFFICIENT_OR_UNKNOWN"},
		{"gpu availability exceeds capacity", func(request *DeploymentPlanningRequest) {
			available := 30000.0
			request.ResourceRecommendation.Recommendations[0].Resource.Accelerators[0].MemoryAvailableMiB = &available
		}, "ACCELERATOR_AVAILABILITY_EXCEEDS_CAPACITY"},
		{"gpu unhealthy", func(request *DeploymentPlanningRequest) {
			request.ResourceRecommendation.Recommendations[0].Resource.Accelerators[0].Healthy = false
		}, "ACCELERATOR_AVAILABLE_INSUFFICIENT_OR_UNKNOWN"},
		{"gpu count", func(request *DeploymentPlanningRequest) {
			request.ApplicationProfile.Requirements.Accelerator.CountMin = 2
		}, "ACCELERATOR_CAPACITY_INSUFFICIENT"},
		{"duplicate gpu device", func(request *DeploymentPlanningRequest) {
			request.ApplicationProfile.Requirements.Accelerator.CountMin = 2
			duplicate := request.ResourceRecommendation.Recommendations[0].Resource.Accelerators[0]
			request.ResourceRecommendation.Recommendations[0].Resource.Accelerators = append(
				request.ResourceRecommendation.Recommendations[0].Resource.Accelerators, duplicate,
			)
		}, "ACCELERATOR_CAPACITY_INSUFFICIENT"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := focusedPlanningRequest()
			test.change(&request)
			server := NewFocusedServer(NewServerConfig())
			encoded, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(encoded))
			if response.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200 RETRY: %s", response.Code, response.Body.String())
			}
			var flow agentcontrol.Flow
			if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
				t.Fatal(err)
			}
			if flow.State != agentcontrol.StateRetryRequired || flow.Decision == nil ||
				flow.Decision.Action != agentcontrol.ActionRetry || flow.DeploymentPlan != nil ||
				flow.DesiredDeploymentSpec != nil || flow.ResourceRecommendation == nil ||
				flow.Decision.CorrectionRequest == nil || len(flow.Decision.CorrectionRequest.Issues) == 0 ||
				flow.Decision.CorrectionRequest.Issues[0].Code != "NO_FEASIBLE_RESOURCE_CANDIDATE" {
				t.Fatalf("insufficient resource produced an actionable plan: %#v", flow)
			}
			recommendation := flow.ResourceRecommendation.Data.ResourceRecommendation
			if recommendation.Status != agentcontrol.StateRetryRequired || recommendation.SelectedCandidateID != "" ||
				len(recommendation.Candidates) != 1 || recommendation.Candidates[0].Feasible ||
				!strings.Contains(strings.Join(recommendation.Candidates[0].RejectionReasons, " "), test.wantReason) {
				t.Fatalf("insufficient resource was not retained as a rejected candidate: %#v", recommendation)
			}
		})
	}
}

func TestFocusedServerUsesNextCapacityValidatedResourceInUpstreamOrder(t *testing.T) {
	request := focusedPlanningRequest()
	first := &request.ResourceRecommendation.Recommendations[0]
	available := 4.0
	first.Resource.CPU.AvailableCores = &available
	second := *first
	second.Rank = 2
	second.Resource = first.Resource
	second.Resource.ResourceID = "node-b"
	fullAvailability := 12.0
	second.Resource.CPU.AvailableCores = &fullAvailability
	request.ResourceRecommendation.Recommendations = append(request.ResourceRecommendation.Recommendations, second)
	request.ResourceRecommendation.Summary.Returned = 2
	request.ResourceRecommendation.Summary.Feasible = 2
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, NewFocusedServer(NewServerConfig()), http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if flow.DeploymentPlan == nil || flow.DeploymentPlan.SelectedCandidateID != "node-b" ||
		flow.ResourceRecommendation == nil || len(flow.ResourceRecommendation.Data.ResourceRecommendation.Candidates) != 2 ||
		flow.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].Feasible ||
		!flow.ResourceRecommendation.Data.ResourceRecommendation.Candidates[1].Feasible {
		t.Fatalf("upstream order or validated selection was lost: %#v", flow)
	}
}

func TestFocusedServerRequiresEnoughDistinctValidatedNodes(t *testing.T) {
	for _, test := range []struct {
		name         string
		secondID     string
		secondRank   int
		wantHTTPCode int
	}{
		{"one result for two replicas", "", 0, http.StatusOK},
		{"duplicate resource ID", "node-a", 2, http.StatusUnprocessableEntity},
		{"rank disagrees with order", "node-b", 3, http.StatusUnprocessableEntity},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := focusedPlanningRequest()
			request.ApplicationProfile.Requirements.Deployment.ReplicasMin = 2
			if test.secondID != "" {
				second := request.ResourceRecommendation.Recommendations[0]
				second.Resource.ResourceID = test.secondID
				second.Rank = test.secondRank
				request.ResourceRecommendation.Recommendations = append(request.ResourceRecommendation.Recommendations, second)
				request.ResourceRecommendation.Summary.Returned = 2
				request.ResourceRecommendation.Summary.Feasible = 2
			}
			encoded, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			response := performJSONRequest(t, NewFocusedServer(NewServerConfig()), http.MethodPost, pathDeploymentPlans, string(encoded))
			if response.Code != test.wantHTTPCode {
				t.Fatalf("status = %d, want %d: %s", response.Code, test.wantHTTPCode, response.Body.String())
			}
			if test.wantHTTPCode == http.StatusOK {
				var flow agentcontrol.Flow
				if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
					t.Fatal(err)
				}
				if flow.State != agentcontrol.StateRetryRequired || flow.DeploymentPlan != nil ||
					flow.ResourceRecommendation == nil || flow.ResourceRecommendation.Data.ResourceRecommendation.SelectedCandidateID != "" {
					t.Fatalf("insufficient node count produced a plan: %#v", flow)
				}
			}
		})
	}
}

func TestFocusedServerSelectsLaterValidatedNodeGroupWithoutReordering(t *testing.T) {
	request := focusedPlanningRequest()
	request.ApplicationProfile.Requirements.Deployment.ReplicasMin = 2
	request.ApplicationProfile.Requirements.Deployment.ReplicasMax = 3
	first := &request.ResourceRecommendation.Recommendations[0]
	insufficientCPU := 4.0
	first.Resource.CPU.AvailableCores = &insufficientCPU
	for index, resourceID := range []string{"node-b", "node-c"} {
		ranked := *first
		ranked.Resource = first.Resource
		ranked.Rank = index + 2
		ranked.Resource.ResourceID = resourceID
		availableCPU := 12.0
		ranked.Resource.CPU.AvailableCores = &availableCPU
		request.ResourceRecommendation.Recommendations = append(request.ResourceRecommendation.Recommendations, ranked)
	}
	request.ResourceRecommendation.Summary.Returned = 3
	request.ResourceRecommendation.Summary.Feasible = 3
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, NewFocusedServer(NewServerConfig()), http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if flow.DeploymentPlan == nil || flow.DeploymentPlan.DesiredInfrastructure.NodeCount != 2 ||
		flow.ResourceRecommendation == nil {
		t.Fatalf("missing validated two-node plan: %#v", flow)
	}
	selected := flow.ResourceRecommendation.Data.ResourceRecommendation
	if len(selected.Candidates) != 2 || selected.Candidates[0].Feasible || !selected.Candidates[1].Feasible ||
		selected.SelectedCandidateID != selected.Candidates[1].CandidateID ||
		flow.DeploymentPlan.SelectedCandidateID != selected.Candidates[1].CandidateID ||
		!strings.Contains(strings.Join(selected.Candidates[0].RejectionReasons, " "), "CPU_AVAILABLE_INSUFFICIENT_OR_UNKNOWN") ||
		!strings.Contains(strings.Join(flow.DeploymentPlan.ResourceHints, " "), "resource_id:node-b") ||
		!strings.Contains(strings.Join(flow.DeploymentPlan.ResourceHints, " "), "resource_id:node-c") ||
		strings.Contains(strings.Join(flow.DeploymentPlan.ResourceHints, " "), "resource_id:node-a") {
		t.Fatalf("invalid node was included in selected placement: %#v", flow)
	}
}

func TestFocusedServerRetriesWhenOnlyOneOfTwoNodesIsValid(t *testing.T) {
	request := focusedPlanningRequest()
	request.ApplicationProfile.Requirements.Deployment.ReplicasMin = 2
	request.ApplicationProfile.Requirements.Deployment.ReplicasMax = 2
	first := &request.ResourceRecommendation.Recommendations[0]
	insufficientCPU := 4.0
	first.Resource.CPU.AvailableCores = &insufficientCPU
	second := *first
	second.Rank = 2
	second.Resource.ResourceID = "node-b"
	availableCPU := 12.0
	second.Resource.CPU.AvailableCores = &availableCPU
	request.ResourceRecommendation.Recommendations = append(request.ResourceRecommendation.Recommendations, second)
	request.ResourceRecommendation.Summary.Returned = 2
	request.ResourceRecommendation.Summary.Feasible = 2
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, NewFocusedServer(NewServerConfig()), http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 RETRY: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if flow.State != agentcontrol.StateRetryRequired || flow.DeploymentPlan != nil ||
		flow.ResourceRecommendation == nil || flow.Decision == nil || flow.Decision.CorrectionRequest == nil ||
		len(flow.Decision.CorrectionRequest.Issues) == 0 ||
		flow.Decision.CorrectionRequest.Issues[0].Code != "NO_FEASIBLE_RESOURCE_CANDIDATE" {
		t.Fatalf("one valid node produced a two-node plan: %#v", flow)
	}
	recommendation := flow.ResourceRecommendation.Data.ResourceRecommendation
	if recommendation.SelectedCandidateID != "" || recommendation.Status != agentcontrol.StateRetryRequired ||
		len(recommendation.Candidates) != 1 || recommendation.Candidates[0].Feasible ||
		!strings.Contains(strings.Join(recommendation.Candidates[0].RejectionReasons, " "), "CPU_AVAILABLE_INSUFFICIENT_OR_UNKNOWN") {
		t.Fatalf("invalid two-node group was not preserved as rejection evidence: %#v", recommendation)
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
	availableCPU := 12.0
	availableMemory := 48000.0
	availableStorage := 80.0
	availableGPUMemory := 18000.0
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
					CPU:     ResourceOpsCPU{LogicalCores: 16, AvailableCores: &availableCPU},
					Memory:  ResourceOpsMemory{TotalMiB: 65536, AvailableMiB: &availableMemory},
					Storage: ResourceOpsStorage{TotalGiB: 100, AvailableGiB: &availableStorage},
					Accelerators: []ResourceOpsAccelerator{{
						DeviceID: "gpu-0", Type: "GPU", MemoryTotalMiB: 24576,
						MemoryAvailableMiB: &availableGPUMemory, Healthy: true,
					}},
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
