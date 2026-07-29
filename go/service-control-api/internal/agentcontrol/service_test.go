package agentcontrol

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestServiceCreatesDeployPlanForFeasibleRecommendation(t *testing.T) {
	service := NewService()

	waiting, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope())
	if err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	if waiting.State != StateWaitingForRecommendation {
		t.Fatalf("state = %q, want %q", waiting.State, StateWaitingForRecommendation)
	}
	ready, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope())
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if ready.State != StateDecisionApproved {
		t.Fatalf("state = %q, want %q", ready.State, StateDecisionApproved)
	}
	if ready.Decision == nil || ready.Decision.Action != ActionDeploy {
		t.Fatalf("decision = %#v, want DEPLOY", ready.Decision)
	}
	if ready.Decision.ReasoningMode != ReasoningModeRuleBased {
		t.Fatalf("reasoning mode = %q", ready.Decision.ReasoningMode)
	}
	if ready.DeploymentPlan == nil {
		t.Fatal("deployment plan was not created")
	}
	if ready.DeploymentPlan.SelectedCandidateID != "candidate-001" {
		t.Fatalf("selected candidate = %q", ready.DeploymentPlan.SelectedCandidateID)
	}
	if ready.DeploymentPlan.DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice != 24576 {
		t.Fatalf(
			"planned accelerator memory = %d",
			ready.DeploymentPlan.DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice,
		)
	}
	if ready.CorrelationID != "flow-001" || ready.TraceID != "trace-001" || ready.ProfileID != "profile-001" {
		t.Fatalf("flow identifiers were not preserved: %+v", ready)
	}
	if ready.Guard == nil || ready.Guard.Status != GuardApproved {
		t.Fatalf("guard = %#v, want APPROVED", ready.Guard)
	}
	if ready.DeploymentRequest == nil {
		t.Fatal("approved plan must create deployment.create.request")
	}
	if ready.DeploymentRequest.MessageType != MessageDeploymentCreateRequest {
		t.Fatalf("message type = %q", ready.DeploymentRequest.MessageType)
	}
	deploymentRequest := ready.DeploymentRequest.Data.DeploymentRequest
	manifest := deploymentRequest.DeploymentManifest
	if deploymentRequest.DecisionID != ready.Decision.DecisionID ||
		manifest.DecisionID != ready.Decision.DecisionID {
		t.Fatalf(
			"decision IDs are inconsistent: decision=%q request=%q manifest=%q",
			ready.Decision.DecisionID,
			deploymentRequest.DecisionID,
			manifest.DecisionID,
		)
	}
	if deploymentRequest.Application.AppID != manifest.Application.AppID ||
		deploymentRequest.Application.AppVersion != manifest.Application.AppVersion {
		t.Fatalf("application identity is inconsistent: %#v %#v", deploymentRequest.Application, manifest.Application)
	}
	if manifest.Metadata.ProfileID != ready.ProfileID {
		t.Fatalf("manifest profile_id = %q, want %q", manifest.Metadata.ProfileID, ready.ProfileID)
	}
	encoded, err := json.Marshal(ready.DeploymentRequest)
	if err != nil {
		t.Fatalf("marshal deployment request: %v", err)
	}
	if strings.Contains(string(encoded), ":null") {
		t.Fatalf("Common JSON must omit missing values instead of null: %s", encoded)
	}
}

func TestServiceRequestsResourceRetryForInsufficientCandidate(t *testing.T) {
	service := NewService()
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	recommendation := validResourceRecommendationEnvelope()
	recommendation.Data.ResourceRecommendation.Candidates[0].
		DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice = 16384

	flow, err := service.ReceiveResourceRecommendation(context.Background(), recommendation)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if flow.State != StateRetryRequired {
		t.Fatalf("state = %q, want %q", flow.State, StateRetryRequired)
	}
	if flow.Decision == nil || flow.Decision.Action != ActionRetry {
		t.Fatalf("decision = %#v, want RETRY", flow.Decision)
	}
	if flow.Decision.CorrectionRequest == nil ||
		flow.Decision.CorrectionRequest.Target != CorrectionTargetResourceRecommendation {
		t.Fatalf("correction request = %#v", flow.Decision.CorrectionRequest)
	}
	if flow.DeploymentPlan != nil {
		t.Fatal("RETRY decision must not create a deployment plan")
	}
	if flow.Guard == nil || flow.Guard.Status != GuardRetryRequired {
		t.Fatalf("guard = %#v, want RETRY_REQUIRED", flow.Guard)
	}
	if flow.DeploymentRequest != nil {
		t.Fatal("RETRY decision must not create deployment.create.request")
	}
}

func TestServiceRejectsInvalidApplicationRequirements(t *testing.T) {
	service := NewService()
	applicationContext := validApplicationContextEnvelope()
	applicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMin = 3
	applicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMax = 1
	if _, err := service.ReceiveApplicationContext(context.Background(), applicationContext); err != nil {
		t.Fatalf("structurally valid application context must be accepted: %v", err)
	}

	flow, err := service.ReceiveResourceRecommendation(
		context.Background(),
		validResourceRecommendationEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if flow.State != StateDecisionRejected {
		t.Fatalf("state = %q, want %q", flow.State, StateDecisionRejected)
	}
	if flow.Decision == nil || flow.Decision.Action != ActionReject {
		t.Fatalf("decision = %#v, want REJECT", flow.Decision)
	}
	if flow.Decision.CorrectionRequest == nil ||
		flow.Decision.CorrectionRequest.Target != CorrectionTargetApplicationProfile {
		t.Fatalf("correction request = %#v", flow.Decision.CorrectionRequest)
	}
	if flow.DeploymentPlan != nil {
		t.Fatal("REJECT decision must not create a deployment plan")
	}
	if flow.Guard == nil || flow.Guard.Status != GuardRejected {
		t.Fatalf("guard = %#v, want REJECTED", flow.Guard)
	}
	if flow.DeploymentRequest != nil {
		t.Fatal("REJECT decision must not create deployment.create.request")
	}
}

func TestServiceRejectsMismatchedProfileID(t *testing.T) {
	service := NewService()
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	recommendation := validResourceRecommendationEnvelope()
	recommendation.Data.ResourceRecommendation.ProfileID = "profile-other"

	if _, err := service.ReceiveResourceRecommendation(context.Background(), recommendation); err == nil {
		t.Fatal("mismatched profile_id must be rejected")
	}
}

func TestServiceSummarizesSuccessfulDeploymentFeedback(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	flow, err := service.ReceiveDeploymentStatus(
		context.Background(),
		validDeploymentStatusEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	if flow.DeploymentStatus == nil || flow.DeploymentStatus.Data.DeploymentStatus.State != DeploymentStateRunning {
		t.Fatalf("deployment status = %#v, want RUNNING", flow.DeploymentStatus)
	}

	flow, err = service.ReceiveOptimizationFeedback(
		context.Background(),
		validOptimizationFeedbackEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.FeedbackSummary == nil {
		t.Fatal("feedback summary was not created")
	}
	if !flow.FeedbackSummary.Success {
		t.Fatalf("success = false, summary = %#v", flow.FeedbackSummary)
	}
	if flow.FeedbackSummary.DeploymentID != "deployment-001" ||
		flow.FeedbackSummary.DecisionID != "decision-flow-001" {
		t.Fatalf("feedback IDs were not preserved: %#v", flow.FeedbackSummary)
	}
	if !strings.Contains(flow.FeedbackSummary.Cause, "SLO") {
		t.Fatalf("cause = %q, want an SLO result", flow.FeedbackSummary.Cause)
	}
}

func TestServiceSummarizesFailedDeploymentStatus(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	status.Data.DeploymentStatus.State = DeploymentStateFailed
	status.Data.DeploymentStatus.Message = "runtime adapter could not start the application"

	flow, err := service.ReceiveDeploymentStatus(context.Background(), status)
	if err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	if flow.FeedbackSummary == nil {
		t.Fatal("failed deployment must create a feedback summary")
	}
	if flow.FeedbackSummary.Success {
		t.Fatalf("success = true, summary = %#v", flow.FeedbackSummary)
	}
	if !strings.Contains(flow.FeedbackSummary.Cause, "runtime adapter") {
		t.Fatalf("cause = %q, want deployment failure message", flow.FeedbackSummary.Cause)
	}
}

func TestServiceRejectsFeedbackForAnotherDecision(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	status.Data.DeploymentStatus.DecisionID = "decision-other"

	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err == nil {
		t.Fatal("feedback for another decision must be rejected")
	}
}

func TestServiceComparesSimpleAndGuardedReasoning(t *testing.T) {
	reasoner := stubReasoner{
		result: ModelReasoningResult{
			ExecutionStatus: "executed",
			CandidateID:     "qwen3.5-ops-planner",
			Provider:        "test-provider",
			ActualModel:     "qwen3.5:4b",
			LatencyMS:       120,
			Proposal: ReasoningProposal{
				Action:              ActionDeploy,
				SelectedCandidateID: "candidate-001",
				Reason:              "The recommended candidate satisfies the application requirements.",
				Confidence:          0.88,
			},
		},
	}
	service := NewServiceWithReasoner(reasoner)
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(
		context.Background(),
		"flow-001",
		"qwen3.5-ops-planner",
	)
	if err != nil {
		t.Fatalf("compare reasoning: %v", err)
	}
	if comparison.RuleBased.Action != ActionDeploy {
		t.Fatalf("rule-based action = %q", comparison.RuleBased.Action)
	}
	if comparison.SimpleInference.GuardStatus != GuardNotApplied {
		t.Fatalf("simple inference guard = %q", comparison.SimpleInference.GuardStatus)
	}
	if comparison.ValidatedInference.GuardStatus != GuardApproved {
		t.Fatalf("validated inference guard = %q", comparison.ValidatedInference.GuardStatus)
	}
	if !comparison.Agreement.ActionMatch || !comparison.Agreement.CandidateMatch {
		t.Fatalf("agreement = %#v", comparison.Agreement)
	}
	flow, ok := service.GetFlow("flow-001")
	if !ok || flow.ReasoningComparison == nil {
		t.Fatal("comparison was not recorded in the Agent Control flow")
	}
}

func TestServiceGuardRejectsUnsafeReasoningProposal(t *testing.T) {
	service := NewServiceWithReasoner(stubReasoner{
		result: ModelReasoningResult{
			ExecutionStatus: "executed",
			CandidateID:     "qwen3.5-ops-planner",
			ActualModel:     "qwen3.5:4b",
			Proposal: ReasoningProposal{
				Action:              ActionDeploy,
				SelectedCandidateID: "candidate-invented",
				Reason:              "Use an unregistered resource.",
				Confidence:          0.9,
			},
		},
	})
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(context.Background(), "flow-001", "qwen3.5-ops-planner")
	if err != nil {
		t.Fatalf("compare reasoning: %v", err)
	}
	if comparison.SimpleInference.Action != ActionDeploy {
		t.Fatalf("simple inference must preserve the raw proposal: %#v", comparison.SimpleInference)
	}
	if comparison.ValidatedInference.GuardStatus != GuardRejected {
		t.Fatalf("unsafe proposal guard = %q, want REJECTED", comparison.ValidatedInference.GuardStatus)
	}
	if comparison.ValidatedInference.Action != ActionDeploy {
		t.Fatalf("validated fallback action = %q, want safe baseline DEPLOY", comparison.ValidatedInference.Action)
	}
}

func TestServiceRecordsUnavailableReasoningProvider(t *testing.T) {
	service := NewServiceWithReasoner(stubReasoner{err: errors.New("connect: connection refused")})
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(context.Background(), "flow-001", "qwen3.5-ops-planner")
	if err != nil {
		t.Fatalf("provider failure must return a comparison report: %v", err)
	}
	if comparison.SimpleInference.ExecutionStatus != ReasoningProviderUnavailable {
		t.Fatalf("execution status = %q", comparison.SimpleInference.ExecutionStatus)
	}
	if !strings.Contains(comparison.SimpleInference.Error, "connection refused") {
		t.Fatalf("provider error was not recorded: %#v", comparison.SimpleInference)
	}
}

func TestServiceDistinguishesRejectedModelOutputFromUnavailableProvider(t *testing.T) {
	service := NewServiceWithReasoner(stubReasoner{
		result: ModelReasoningResult{
			ExecutionStatus: "rejected",
			CandidateID:     "qwen3.5-ops-planner",
			Provider:        "local-openai-compatible",
			ActualModel:     "qwen3.5:4b",
			LatencyMS:       45,
		},
		err: errors.New("parse reasoning proposal: unknown field"),
	})
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(context.Background(), "flow-001", "qwen3.5-ops-planner")
	if err != nil {
		t.Fatalf("rejected output must return a comparison report: %v", err)
	}
	if comparison.SimpleInference.ExecutionStatus != "rejected" {
		t.Fatalf("execution status = %q, want rejected", comparison.SimpleInference.ExecutionStatus)
	}
}

type stubReasoner struct {
	result ModelReasoningResult
	err    error
}

func (reasoner stubReasoner) Propose(
	context.Context,
	string,
	ReasoningInput,
) (ModelReasoningResult, error) {
	return reasoner.result, reasoner.err
}

func createApprovedFlow(t *testing.T, service *Service) Flow {
	t.Helper()
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	flow, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope())
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	return flow
}

func validDeploymentStatusEnvelope() DeploymentStatusEnvelope {
	return DeploymentStatusEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-deployment-status-001",
			MessageType:     MessageDeploymentStatusChanged,
			OccurredAt:      "2026-07-29T03:10:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			CausationID:     "msg-deploy-request-flow-001",
			Source:          Endpoint{System: "deployment-orchestrator", Component: "runtime-adapter"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: DeploymentStatusData{
			DeploymentStatus: DeploymentStatus{
				DeploymentID: "deployment-001",
				DecisionID:   "decision-flow-001",
				State:        DeploymentStateRunning,
				ActualInfrastructure: ActualInfrastructure{
					Provider:    "MOCK",
					Region:      "kr-central-1",
					ResourceIDs: []string{"vm-gpu-01"},
				},
				Message:   "The application is running.",
				UpdatedAt: "2026-07-29T03:10:00Z",
			},
		},
	}
}

func validOptimizationFeedbackEnvelope() OptimizationFeedbackEnvelope {
	return OptimizationFeedbackEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-feedback-001",
			MessageType:     MessageOptimizationFeedbackCreated,
			OccurredAt:      "2026-07-29T03:30:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			CausationID:     "msg-deployment-status-001",
			Source:          Endpoint{System: "deployment-orchestrator", Component: "monitoring"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: OptimizationFeedbackData{
			OptimizationFeedback: OptimizationFeedback{
				FeedbackID:   "feedback-001",
				DecisionID:   "decision-flow-001",
				DeploymentID: "deployment-001",
				Outcome:      FeedbackOutcomeSucceeded,
				ObservationWindow: ObservationWindow{
					StartedAt: "2026-07-29T03:10:00Z",
					EndedAt:   "2026-07-29T03:30:00Z",
				},
				Metrics: OptimizationMetrics{
					Resource: ResourceMetrics{
						CPUAveragePercent:         48.2,
						MemoryPeakMiB:             26800,
						AcceleratorAveragePercent: 72.5,
						AcceleratorMemoryPeakMiB:  21800,
					},
					Inference: InferenceMetrics{
						LatencyP95MS:     1480,
						ThroughputRPS:    6.2,
						ErrorRatePercent: 0.2,
					},
					Cost: CostMetrics{
						Currency:      "KRW",
						EstimatedCost: 833.33,
					},
				},
				SLOViolations: []string{},
				CreatedAt:     "2026-07-29T03:30:00Z",
			},
		},
	}
}

func validApplicationContextEnvelope() ApplicationContextEnvelope {
	return ApplicationContextEnvelope{
		Envelope: Envelope{
			ContractVersion: "1.0",
			MessageID:       "msg-context-001",
			MessageType:     MessageApplicationContextCreated,
			OccurredAt:      "2026-07-29T03:00:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			Source:          Endpoint{System: "khu-ai-app", Component: "application-profile-generator"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: ApplicationContextData{
			ApplicationProfile: ApplicationProfile{
				ProfileID:  "profile-001",
				AppID:      "chat-service",
				AppVersion: "1.0.0",
				Requirements: ApplicationRequirements{
					Compute: ComputeRequirements{
						CPUCoresMin:   8,
						MemoryMiBMin:  32768,
						StorageGiBMin: 100,
					},
					Accelerator: AcceleratorRequirements{
						Required:              true,
						Type:                  "GPU",
						CountMin:              1,
						MemoryMiBMinPerDevice: 24576,
					},
					Deployment: DeploymentRequirements{
						ReplicasMin: 1,
						ReplicasMax: 2,
						Isolation:   "ONE_MAJOR_APP_PER_VM",
					},
					SLO: SLORequirements{
						LatencyP95MSMax:  2000,
						ThroughputRPSMin: 5,
					},
				},
			},
			ModelRecommendation: ModelRecommendation{
				SelectedModel: SelectedModel{
					ModelID:      "qwen2.5-7b-instruct",
					ModelVersion: "1",
					Source:       "HUGGING_FACE",
				},
				InferenceConfiguration: InferenceConfiguration{
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

func validResourceRecommendationEnvelope() ResourceRecommendationEnvelope {
	return ResourceRecommendationEnvelope{
		Envelope: Envelope{
			ContractVersion: "1.0",
			MessageID:       "msg-resource-001",
			MessageType:     MessageResourceRecommendationCreated,
			OccurredAt:      "2026-07-29T03:01:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			CausationID:     "msg-context-001",
			Source:          Endpoint{System: "khu-resource-service", Component: "resource-recommender"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: ResourceRecommendationData{
			ResourceRecommendation: ResourceRecommendation{
				RecommendationID:    "resource-rec-001",
				ProfileID:           "profile-001",
				SnapshotID:          "snapshot-001",
				Status:              "FOUND",
				SelectedCandidateID: "candidate-001",
				Candidates: []ResourceCandidate{
					{
						CandidateID: "candidate-001",
						Rank:        1,
						Feasible:    true,
						DesiredInfrastructure: DesiredInfrastructure{
							NodeCount:         1,
							CPUCoresPerNode:   8,
							MemoryMiBPerNode:  32768,
							StorageGiBPerNode: 100,
							Accelerator: AcceleratorAllocation{
								Type:                  "GPU",
								Count:                 1,
								MemoryMiBMinPerDevice: 24576,
							},
							Isolation: "ONE_MAJOR_APP_PER_VM",
						},
						ResourceHints: []string{"vm-gpu-01"},
						Scores: ResourceScores{
							ResourceFit:    0.96,
							SLOHeadroom:    0.84,
							CostEfficiency: 0.83,
							Availability:   0.98,
							Total:          0.90,
						},
					},
				},
			},
		},
	}
}
