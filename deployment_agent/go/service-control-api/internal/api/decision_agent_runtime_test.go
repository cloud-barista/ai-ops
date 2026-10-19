package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestDecisionAgentRuntimeDispatchesSelectedRuntimeAgent(t *testing.T) {
	var calls int
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		calls++
		var dispatched AgentDispatchRequest
		if err := json.NewDecoder(request.Body).Decode(&dispatched); err != nil {
			t.Errorf("decode dispatch request: %v", err)
		}
		response.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(response).Encode(AgentExecutionResult{
			RunID:  dispatched.RunID,
			Agent:  dispatched.Agent,
			Status: "completed",
			Proposal: AgentProposal{
				Action: dispatched.Action,
				Parameters: map[string]any{
					"decision":              agentcontrol.ActionDeploy,
					"selected_candidate_id": "candidate-001",
					"reason":                "Runtime Agent selected a feasible candidate.",
					"confidence":            0.91,
				},
			},
			DomainValidation: "deployment_decision",
		})
	}))
	defer server.Close()

	store := newRuntimeAgentStore()
	runtimeAgent := decisionAgent("RuntimeDeploymentAgent", true, agentSourceRuntime)
	runtimeAgent.Endpoint = server.URL
	runtimeAgent.InvocationPath = "/v1/decide"
	if err := store.add(runtimeAgent); err != nil {
		t.Fatalf("register Runtime Agent: %v", err)
	}
	dispatcher := newAgentDispatcher(
		map[string]agentExecutor{
			"AIApplicationAutomationAgent": newInternalDecisionAgentExecutor(),
		},
		newHTTPAgentExecutor(server.Client(), 0),
	)
	runtime := newDecisionAgentRuntime(NewServerConfig(), store, dispatcher)
	profile, recommendation := decisionRuntimeFixtures()

	result, err := runtime.Decide(context.Background(), agentcontrol.DecisionAgentRequest{
		RunID:                  "run-runtime-001",
		RequestedAgent:         runtimeAgent.Name,
		CorrelationID:          "flow-001",
		TraceID:                "trace-001",
		ApplicationProfile:     profile,
		ResourceRecommendation: recommendation,
	})
	if err != nil {
		t.Fatalf("execute Runtime decision Agent: %v", err)
	}
	if calls != 1 {
		t.Fatalf("Runtime Agent calls = %d, want 1", calls)
	}
	if result.AgentName != runtimeAgent.Name || result.Source != agentSourceRuntime {
		t.Fatalf("decision result = %#v", result)
	}
	if result.Decision.Action != agentcontrol.ActionDeploy ||
		result.Decision.SelectedCandidateID != "candidate-001" {
		t.Fatalf("decision = %#v", result.Decision)
	}
}

func TestServiceAutomationRunnerUsesSelectedRuntimeDecisionAgent(t *testing.T) {
	var calls int
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		calls++
		var dispatched AgentDispatchRequest
		if err := json.NewDecoder(request.Body).Decode(&dispatched); err != nil {
			t.Errorf("decode dispatch request: %v", err)
		}
		response.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(response).Encode(AgentExecutionResult{
			RunID:  dispatched.RunID,
			Agent:  dispatched.Agent,
			Status: "completed",
			Proposal: AgentProposal{
				Action: dispatched.Action,
				Parameters: map[string]any{
					"decision":              agentcontrol.ActionDeploy,
					"selected_candidate_id": "mock-gpu-l4",
					"reason":                "Runtime Agent selected the catalog candidate.",
					"confidence":            0.9,
				},
			},
			DomainValidation: "deployment_decision",
		})
	}))
	defer server.Close()

	service := NewService(NewServerConfig())
	enabled := true
	_, err := service.RegisterExternalAgent(context.Background(), ExternalAgentRegistrationRequest{
		Name:           "RuntimeDeploymentAgent",
		Version:        "1.0.0",
		Role:           "Generate one guarded deployment decision.",
		Endpoint:       server.URL,
		InvocationPath: "/v1/decide",
		Capabilities:   []string{agentcontrol.AutomationCapability},
		BoundedActions: []string{agentcontrol.AutomationDecisionAction},
		Enabled:        &enabled,
	})
	if err != nil {
		t.Fatalf("register Runtime decision Agent: %v", err)
	}
	run, err := service.automationRunner.Run(context.Background(), agentcontrol.AutomationRunInput{
		InputType:     agentcontrol.InputTypeNaturalLanguage,
		Request:       "GPU 1개, CPU 4코어, 메모리 8GiB, 스토리지 20GiB로 추론 서비스를 배포해 주세요.",
		RequestedBy:   "test",
		DecisionAgent: "RuntimeDeploymentAgent",
	})
	if err != nil {
		t.Fatalf("run selected Runtime Agent automation: %v", err)
	}
	if calls != 1 {
		t.Fatalf("Runtime Agent calls = %d, want 1", calls)
	}
	if run.Flow == nil || run.Flow.AgentExecution == nil {
		t.Fatalf("run Flow evidence = %#v", run.Flow)
	}
	if run.Flow.AgentExecution.AgentName != "RuntimeDeploymentAgent" ||
		run.Flow.AgentExecution.Source != agentSourceRuntime {
		t.Fatalf("Agent execution = %#v", run.Flow.AgentExecution)
	}
}

func decisionRuntimeFixtures() (agentcontrol.ApplicationProfile, agentcontrol.ResourceRecommendation) {
	profile := agentcontrol.ApplicationProfile{
		ProfileID:  "profile-001",
		AppID:      "chat-service",
		AppVersion: "1.0.0",
		Requirements: agentcontrol.ApplicationRequirements{
			Compute: agentcontrol.ComputeRequirements{
				CPUCoresMin:   4,
				MemoryMiBMin:  8192,
				StorageGiBMin: 20,
			},
			Accelerator: agentcontrol.AcceleratorRequirements{
				Required: true,
				Type:     "GPU",
				CountMin: 1,
			},
			Deployment: agentcontrol.DeploymentRequirements{
				ReplicasMin: 1,
				ReplicasMax: 2,
				Isolation:   "ONE_MAJOR_APP_PER_VM",
			},
		},
	}
	recommendation := agentcontrol.ResourceRecommendation{
		RecommendationID:    "recommendation-001",
		ProfileID:           profile.ProfileID,
		Status:              "FOUND",
		SelectedCandidateID: "candidate-001",
		Candidates: []agentcontrol.ResourceCandidate{{
			CandidateID: "candidate-001",
			Feasible:    true,
			DesiredInfrastructure: agentcontrol.DesiredInfrastructure{
				NodeCount:         1,
				CPUCoresPerNode:   4,
				MemoryMiBPerNode:  8192,
				StorageGiBPerNode: 20,
				Accelerator: agentcontrol.AcceleratorAllocation{
					Type:  "GPU",
					Count: 1,
				},
				Isolation: "ONE_MAJOR_APP_PER_VM",
			},
			Scores: agentcontrol.ResourceScores{Total: 0.9},
		}},
	}
	return profile, recommendation
}

func mustAnyMap(t *testing.T, value any) map[string]any {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("encode fixture: %v", err)
	}
	var result map[string]any
	if err := json.Unmarshal(encoded, &result); err != nil {
		t.Fatalf("decode fixture: %v", err)
	}
	return result
}
