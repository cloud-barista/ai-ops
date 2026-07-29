package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestAgentControlInputJoinAPI(t *testing.T) {
	server := NewServer(NewServerConfig())

	contextBody := marshalAgentControlMessage(t, apiApplicationContextEnvelope())
	contextResponse := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/application-contexts",
		contextBody,
	)
	if contextResponse.Code != http.StatusAccepted {
		t.Fatalf(
			"application context: code=%d body=%s",
			contextResponse.Code,
			contextResponse.Body.String(),
		)
	}
	if !strings.Contains(contextResponse.Body.String(), `"state":"WAITING_FOR_RESOURCE_RECOMMENDATION"`) {
		t.Fatalf("unexpected application context response: %s", contextResponse.Body.String())
	}

	recommendationBody := marshalAgentControlMessage(t, apiResourceRecommendationEnvelope())
	recommendationResponse := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/resource-recommendations",
		recommendationBody,
	)
	if recommendationResponse.Code != http.StatusAccepted {
		t.Fatalf(
			"resource recommendation: code=%d body=%s",
			recommendationResponse.Code,
			recommendationResponse.Body.String(),
		)
	}
	if !strings.Contains(recommendationResponse.Body.String(), `"state":"DEPLOY_APPROVED"`) ||
		!strings.Contains(recommendationResponse.Body.String(), `"action":"DEPLOY"`) ||
		!strings.Contains(recommendationResponse.Body.String(), `"plan_id":"plan-flow-api-001"`) ||
		!strings.Contains(
			recommendationResponse.Body.String(),
			`"agent_authorization":{"agent_name":"AIApplicationAutomationAgent","capability":"ai_application_automation","action":"generate_deployment_decision","authorized":true`,
		) ||
		!strings.Contains(
			recommendationResponse.Body.String(),
			`"reason":"Agent Registry authorizes the required capability and bounded action."`,
		) {
		t.Fatalf("unexpected resource recommendation response: %s", recommendationResponse.Body.String())
	}

	detail := performJSONRequest(
		t,
		server,
		http.MethodGet,
		"/api/v1/agent-control/flows/flow-api-001",
		"",
	)
	if detail.Code != http.StatusOK ||
		!strings.Contains(detail.Body.String(), `"profile_id":"profile-api-001"`) {
		t.Fatalf("flow detail: code=%d body=%s", detail.Code, detail.Body.String())
	}

	list := performJSONRequest(t, server, http.MethodGet, "/api/v1/agent-control/flows", "")
	if list.Code != http.StatusOK ||
		!strings.Contains(list.Body.String(), `"count":1`) ||
		!strings.Contains(list.Body.String(), `"correlation_id":"flow-api-001"`) {
		t.Fatalf("flow list: code=%d body=%s", list.Code, list.Body.String())
	}
}

func TestAgentControlFeedbackAPI(t *testing.T) {
	server := NewServer(NewServerConfig())
	postAgentControlInputPair(t, server)

	status := agentcontrol.DeploymentStatusEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       "msg-deployment-api-001",
			MessageType:     agentcontrol.MessageDeploymentStatusChanged,
			OccurredAt:      "2026-07-29T05:10:00Z",
			CorrelationID:   "flow-api-001",
			TraceID:         "trace-api-001",
			Source:          agentcontrol.Endpoint{System: "deployment-orchestrator", Component: "runtime-adapter"},
			Target:          agentcontrol.Endpoint{System: "khu-geon", Component: "agent-control"},
		},
		Data: agentcontrol.DeploymentStatusData{
			DeploymentStatus: agentcontrol.DeploymentStatus{
				DeploymentID: "deployment-api-001",
				DecisionID:   "decision-flow-api-001",
				State:        agentcontrol.DeploymentStateRunning,
				Message:      "The application is running.",
				UpdatedAt:    "2026-07-29T05:10:00Z",
			},
		},
	}
	statusResponse := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/deployment-status",
		marshalAgentControlMessage(t, status),
	)
	if statusResponse.Code != http.StatusAccepted ||
		!strings.Contains(statusResponse.Body.String(), `"deployment_state":"RUNNING"`) {
		t.Fatalf("deployment status: code=%d body=%s", statusResponse.Code, statusResponse.Body.String())
	}

	feedback := agentcontrol.OptimizationFeedbackEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       "msg-feedback-api-001",
			MessageType:     agentcontrol.MessageOptimizationFeedbackCreated,
			OccurredAt:      "2026-07-29T05:30:00Z",
			CorrelationID:   "flow-api-001",
			TraceID:         "trace-api-001",
			Source:          agentcontrol.Endpoint{System: "deployment-orchestrator", Component: "monitoring"},
			Target:          agentcontrol.Endpoint{System: "khu-geon", Component: "agent-control"},
		},
		Data: agentcontrol.OptimizationFeedbackData{
			OptimizationFeedback: agentcontrol.OptimizationFeedback{
				FeedbackID:   "feedback-api-001",
				DecisionID:   "decision-flow-api-001",
				DeploymentID: "deployment-api-001",
				Outcome:      agentcontrol.FeedbackOutcomeSucceeded,
				ObservationWindow: agentcontrol.ObservationWindow{
					StartedAt: "2026-07-29T05:10:00Z",
					EndedAt:   "2026-07-29T05:30:00Z",
				},
				Metrics: agentcontrol.OptimizationMetrics{
					Resource: agentcontrol.ResourceMetrics{
						CPUAveragePercent:         50,
						MemoryPeakMiB:             7000,
						AcceleratorAveragePercent: 75,
						AcceleratorMemoryPeakMiB:  14000,
					},
					Inference: agentcontrol.InferenceMetrics{
						LatencyP95MS:     1200,
						ThroughputRPS:    6.4,
						ErrorRatePercent: 0.1,
					},
					Cost: agentcontrol.CostMetrics{Currency: "KRW", EstimatedCost: 800},
				},
				CreatedAt: "2026-07-29T05:30:00Z",
			},
		},
	}
	feedbackResponse := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/optimization-feedback",
		marshalAgentControlMessage(t, feedback),
	)
	if feedbackResponse.Code != http.StatusAccepted ||
		!strings.Contains(feedbackResponse.Body.String(), `"outcome":"SUCCEEDED"`) ||
		!strings.Contains(feedbackResponse.Body.String(), `"success":true`) {
		t.Fatalf("optimization feedback: code=%d body=%s", feedbackResponse.Code, feedbackResponse.Body.String())
	}
}

func TestAgentControlDeletesFlowAPI(t *testing.T) {
	server := NewServer(NewServerConfig())
	postAgentControlInputPair(t, server)

	deleted := performJSONRequest(
		t,
		server,
		http.MethodDelete,
		"/api/v1/agent-control/flows/flow-api-001",
		"",
	)
	if deleted.Code != http.StatusOK ||
		!strings.Contains(deleted.Body.String(), `"deleted":1`) ||
		!strings.Contains(deleted.Body.String(), `"correlation_id":"flow-api-001"`) {
		t.Fatalf("delete Flow: code=%d body=%s", deleted.Code, deleted.Body.String())
	}

	missing := performJSONRequest(
		t,
		server,
		http.MethodGet,
		"/api/v1/agent-control/flows/flow-api-001",
		"",
	)
	if missing.Code != http.StatusNotFound {
		t.Fatalf("deleted Flow GET: code=%d body=%s", missing.Code, missing.Body.String())
	}

	deleteAgain := performJSONRequest(
		t,
		server,
		http.MethodDelete,
		"/api/v1/agent-control/flows/flow-api-001",
		"",
	)
	if deleteAgain.Code != http.StatusNotFound {
		t.Fatalf("missing Flow delete: code=%d body=%s", deleteAgain.Code, deleteAgain.Body.String())
	}
}

func TestAgentControlClearsFlowsAPI(t *testing.T) {
	server := NewServer(NewServerConfig())
	postAgentControlInputPair(t, server)

	second := apiApplicationContextEnvelope()
	second.CorrelationID = "flow-api-002"
	second.TraceID = "trace-api-002"
	second.Data.ApplicationProfile.ProfileID = "profile-api-002"
	response := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/application-contexts",
		marshalAgentControlMessage(t, second),
	)
	if response.Code != http.StatusAccepted {
		t.Fatalf("second Flow: code=%d body=%s", response.Code, response.Body.String())
	}

	cleared := performJSONRequest(
		t,
		server,
		http.MethodDelete,
		"/api/v1/agent-control/flows",
		"",
	)
	if cleared.Code != http.StatusOK ||
		!strings.Contains(cleared.Body.String(), `"deleted":2`) ||
		!strings.Contains(cleared.Body.String(), `"flows":[]`) {
		t.Fatalf("clear Flows: code=%d body=%s", cleared.Code, cleared.Body.String())
	}
}

func TestAgentControlReasoningComparisonAPI(t *testing.T) {
	provider, closeProvider := automationProvider(t, `{
		"action":"DEPLOY",
		"selected_candidate_id":"candidate-api-001",
		"reason":"The candidate satisfies the application requirements.",
		"confidence":0.92
	}`)
	defer closeProvider()
	config := NewServerConfig()
	config.LLMCandidatesPath = writeAgentControlCandidateConfigAt(t, provider)
	server := NewServer(config)
	postAgentControlInputPair(t, server)

	response := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/flows/flow-api-001/reasoning-comparisons",
		`{"candidate_id":"qwen3.5-ops-planner"}`,
	)
	if response.Code != http.StatusOK ||
		!strings.Contains(response.Body.String(), `"mode":"simple_inference"`) ||
		!strings.Contains(response.Body.String(), `"mode":"validated_inference"`) ||
		!strings.Contains(response.Body.String(), `"guard_status":"APPROVED"`) {
		t.Fatalf("reasoning comparison: code=%d body=%s", response.Code, response.Body.String())
	}
}

func postAgentControlInputPair(t *testing.T, server http.Handler) {
	t.Helper()
	contextResponse := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/application-contexts",
		marshalAgentControlMessage(t, apiApplicationContextEnvelope()),
	)
	if contextResponse.Code != http.StatusAccepted {
		t.Fatalf("application context: code=%d body=%s", contextResponse.Code, contextResponse.Body.String())
	}
	recommendationResponse := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/resource-recommendations",
		marshalAgentControlMessage(t, apiResourceRecommendationEnvelope()),
	)
	if recommendationResponse.Code != http.StatusAccepted {
		t.Fatalf(
			"resource recommendation: code=%d body=%s",
			recommendationResponse.Code,
			recommendationResponse.Body.String(),
		)
	}
}

func marshalAgentControlMessage(t *testing.T, message any) string {
	t.Helper()
	body, err := json.Marshal(message)
	if err != nil {
		t.Fatalf("marshal message: %v", err)
	}
	return string(body)
}

func apiApplicationContextEnvelope() agentcontrol.ApplicationContextEnvelope {
	return agentcontrol.ApplicationContextEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       "msg-context-api-001",
			MessageType:     agentcontrol.MessageApplicationContextCreated,
			OccurredAt:      "2026-07-29T05:00:00Z",
			CorrelationID:   "flow-api-001",
			TraceID:         "trace-api-001",
			Source: agentcontrol.Endpoint{
				System:    "khu-requirements",
				Component: "application-profile-generator",
			},
			Target: agentcontrol.Endpoint{
				System:    "khu-geon",
				Component: "agent-control",
			},
		},
		Data: agentcontrol.ApplicationContextData{
			ApplicationProfile: agentcontrol.ApplicationProfile{
				ProfileID:  "profile-api-001",
				AppID:      "chat-service",
				AppVersion: "1.0.0",
				Requirements: agentcontrol.ApplicationRequirements{
					Compute: agentcontrol.ComputeRequirements{
						CPUCoresMin:   4,
						MemoryMiBMin:  8192,
						StorageGiBMin: 20,
					},
					Accelerator: agentcontrol.AcceleratorRequirements{
						Required:              true,
						Type:                  "GPU",
						CountMin:              1,
						MemoryMiBMinPerDevice: 16384,
					},
					Deployment: agentcontrol.DeploymentRequirements{
						ReplicasMin: 1,
						ReplicasMax: 2,
						Isolation:   "ONE_MAJOR_APP_PER_VM",
					},
				},
			},
			ModelRecommendation: agentcontrol.ModelRecommendation{
				SelectedModel: agentcontrol.SelectedModel{
					ModelID:      "qwen2.5-7b-instruct",
					ModelVersion: "1",
					Source:       "HUGGING_FACE",
				},
				InferenceConfiguration: agentcontrol.InferenceConfiguration{
					RuntimeEngine:      "VLLM",
					Precision:          "FP16",
					MaxBatchSize:       8,
					MaxConcurrency:     20,
					TensorParallelSize: 1,
					Replicas:           1,
				},
			},
		},
	}
}

func apiResourceRecommendationEnvelope() agentcontrol.ResourceRecommendationEnvelope {
	return agentcontrol.ResourceRecommendationEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       "msg-resource-api-001",
			MessageType:     agentcontrol.MessageResourceRecommendationCreated,
			OccurredAt:      "2026-07-29T05:01:00Z",
			CorrelationID:   "flow-api-001",
			TraceID:         "trace-api-001",
			CausationID:     "msg-context-api-001",
			Source: agentcontrol.Endpoint{
				System:    "khu-resource-recommendation",
				Component: "resource-recommender",
			},
			Target: agentcontrol.Endpoint{
				System:    "khu-geon",
				Component: "agent-control",
			},
		},
		Data: agentcontrol.ResourceRecommendationData{
			ResourceRecommendation: agentcontrol.ResourceRecommendation{
				RecommendationID:    "resource-rec-api-001",
				ProfileID:           "profile-api-001",
				SnapshotID:          "snapshot-api-001",
				Status:              "FOUND",
				SelectedCandidateID: "candidate-api-001",
				Candidates: []agentcontrol.ResourceCandidate{
					{
						CandidateID: "candidate-api-001",
						Rank:        1,
						Feasible:    true,
						DesiredInfrastructure: agentcontrol.DesiredInfrastructure{
							NodeCount:         1,
							CPUCoresPerNode:   4,
							MemoryMiBPerNode:  8192,
							StorageGiBPerNode: 20,
							Accelerator: agentcontrol.AcceleratorAllocation{
								Type:                  "GPU",
								Count:                 1,
								MemoryMiBMinPerDevice: 23034,
							},
							Isolation: "ONE_MAJOR_APP_PER_VM",
						},
						Scores: agentcontrol.ResourceScores{
							ResourceFit:    0.95,
							SLOHeadroom:    0.82,
							CostEfficiency: 0.80,
							Availability:   0.97,
							Total:          0.90,
						},
					},
				},
			},
		},
	}
}
