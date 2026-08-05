package agentcontrol

import (
	"context"
	"testing"
)

func TestOptimizerClonesDecisionContractFields(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	flow, ok := service.GetFlow("flow-001")
	if !ok || flow.Decision == nil {
		t.Fatal("approved decision was not stored")
	}
	flow.Decision.ReasonCodes[0] = "MUTATED"
	flow.Decision.RetryPolicy.MaxAttempts = 99

	stored, ok := service.GetFlow("flow-001")
	if !ok || stored.Decision == nil {
		t.Fatal("stored decision was not returned")
	}
	if stored.Decision.ReasonCodes[0] == "MUTATED" {
		t.Fatal("stored reason codes were aliased")
	}
	if stored.Decision.RetryPolicy.MaxAttempts == 99 {
		t.Fatal("stored retry policy was aliased")
	}
}

func TestOptimizerRequestsResourceRetryWhenRecommendationIsNotReady(t *testing.T) {
	service := NewService()
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	recommendation := validResourceRecommendationEnvelope()
	recommendation.Data.ResourceRecommendation.Status = "RETRY_REQUIRED"
	recommendation.Data.ResourceRecommendation.SelectedCandidateID = ""

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
	if flow.Decision.CorrectionRequest == nil || len(flow.Decision.CorrectionRequest.Issues) != 1 {
		t.Fatalf("correction request = %#v", flow.Decision.CorrectionRequest)
	}
	if flow.Decision.CorrectionRequest.Issues[0].Code != "RECOMMENDATION_NOT_READY" {
		t.Fatalf("issue = %#v, want RECOMMENDATION_NOT_READY", flow.Decision.CorrectionRequest.Issues[0])
	}
	if flow.DeploymentRequest != nil {
		t.Fatal("not-ready recommendation must not create deployment.create.request")
	}
}

func TestOptimizerRejectsMalformedResourceRecommendation(t *testing.T) {
	tests := []struct {
		name   string
		modify func(*ResourceRecommendation)
	}{
		{
			name: "unsupported status",
			modify: func(recommendation *ResourceRecommendation) {
				recommendation.Status = "UNKNOWN"
			},
		},
		{
			name: "duplicate candidate ids",
			modify: func(recommendation *ResourceRecommendation) {
				recommendation.Candidates = append(recommendation.Candidates, recommendation.Candidates[0])
			},
		},
		{
			name: "score out of range",
			modify: func(recommendation *ResourceRecommendation) {
				recommendation.Candidates[0].Scores.Availability = 1.1
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			service := NewService()
			recommendation := validResourceRecommendationEnvelope()
			tt.modify(&recommendation.Data.ResourceRecommendation)
			if _, err := service.ReceiveResourceRecommendation(context.Background(), recommendation); err == nil {
				t.Fatal("malformed resource recommendation was accepted")
			}
		})
	}
}

func TestOptimizerRejectsNegativePolicyRequirements(t *testing.T) {
	service := NewService()
	applicationContext := validApplicationContextEnvelope()
	applicationContext.Data.ApplicationProfile.Requirements.SLO.LatencyP95MSMax = -1
	applicationContext.Data.ApplicationProfile.Requirements.Cost.CostPerHourMax = -0.1
	if _, err := service.ReceiveApplicationContext(context.Background(), applicationContext); err != nil {
		t.Fatalf("structurally valid application context must be accepted: %v", err)
	}

	flow, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope())
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if flow.State != StateDecisionRejected {
		t.Fatalf("state = %q, want %q", flow.State, StateDecisionRejected)
	}
	if flow.Decision == nil || flow.Decision.CorrectionRequest == nil {
		t.Fatalf("decision = %#v", flow.Decision)
	}
	if len(flow.Decision.CorrectionRequest.Issues) != 2 {
		t.Fatalf("issues = %#v, want two policy issues", flow.Decision.CorrectionRequest.Issues)
	}
}

func TestOptimizerQueriesDeploymentExperiences(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	status := validDeploymentStatusEnvelope()
	status.Data.DeploymentStatus.State = DeploymentStateFailed
	status.Data.DeploymentStatus.ErrorCode = ErrorCodeGPUOOM
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.Outcome = FeedbackOutcomeFailed
	feedback.Data.OptimizationFeedback.ErrorCode = ErrorCodeGPUOOM
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.FeedbackSummary == nil || flow.FeedbackSummary.ErrorCode != ErrorCodeGPUOOM {
		t.Fatalf("feedback summary = %#v, want GPU_OOM", flow.FeedbackSummary)
	}

	experiences := service.QueryExperiences(ExperienceQuery{
		ProfileID:   "profile-001",
		CandidateID: "candidate-001",
		ErrorCode:   ErrorCodeGPUOOM,
		Limit:       1,
	})
	if len(experiences) != 1 {
		t.Fatalf("experiences = %#v, want one result", experiences)
	}
	experience := experiences[0]
	if experience.CorrelationID != "flow-001" ||
		experience.DeploymentState != DeploymentStateFailed ||
		experience.Outcome != FeedbackOutcomeFailed ||
		experience.Success {
		t.Fatalf("experience = %#v", experience)
	}
	if len(experience.SLOViolations) != 1 || experience.SLOViolations[0] != "latency_p95_ms" {
		t.Fatalf("slo violations = %#v", experience.SLOViolations)
	}
	if got := service.QueryExperiences(ExperienceQuery{ErrorCode: ErrorCodeCUDAMismatch}); len(got) != 0 {
		t.Fatalf("unmatched experiences = %#v", got)
	}
}

func TestOptimizerBuildsFailureRepairDecision(t *testing.T) {
	tests := []struct {
		name       string
		errorCode  string
		action     string
		reasonCode string
	}{
		{
			name:       "gpu oom",
			errorCode:  ErrorCodeGPUOOM,
			action:     ActionAdjustResourceRequirement,
			reasonCode: ReasonFailureRequiresAdjustment,
		},
		{
			name:       "cuda mismatch",
			errorCode:  ErrorCodeCUDAMismatch,
			action:     ActionRequestAlternativeResource,
			reasonCode: ReasonFailureRequiresAlternative,
		},
		{
			name:       "resource insufficient",
			errorCode:  ErrorCodeResourceInsufficient,
			action:     ActionRequestAlternativeResource,
			reasonCode: ReasonFailureRequiresAlternative,
		},
		{
			name:       "transient deployment",
			errorCode:  ErrorCodeTransientDeployment,
			action:     ActionRetryDeployment,
			reasonCode: ReasonFailureTransient,
		},
		{
			name:       "unknown failure",
			errorCode:  "UNKNOWN_FAILURE",
			action:     ActionRequestUserClarification,
			reasonCode: ReasonFailureNeedsClarification,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			service := NewService()
			createApprovedFlow(t, service)
			status := validDeploymentStatusEnvelope()
			status.Data.DeploymentStatus.State = DeploymentStateFailed
			status.Data.DeploymentStatus.ErrorCode = tt.errorCode

			flow, err := service.ReceiveDeploymentStatus(context.Background(), status)
			if err != nil {
				t.Fatalf("receive deployment status: %v", err)
			}
			if flow.RepairDecision == nil || flow.RepairDecision.Action != tt.action {
				t.Fatalf("repair decision = %#v, want action %q", flow.RepairDecision, tt.action)
			}
			if !containsString(flow.RepairDecision.ReasonCodes, tt.reasonCode) ||
				!containsString(flow.RepairDecision.ReasonCodes, tt.errorCode) {
				t.Fatalf("repair reason codes = %#v", flow.RepairDecision.ReasonCodes)
			}
			if tt.action == ActionRetryDeployment &&
				(flow.RepairDecision.RetryPolicy == nil || flow.RepairDecision.RetryPolicy.Attempt != 1) {
				t.Fatalf("retry policy = %#v", flow.RepairDecision.RetryPolicy)
			}
			if flow.Decision == nil || flow.Decision.Action != ActionDeploy {
				t.Fatalf("original decision was not preserved: %#v", flow.Decision)
			}

			flow.RepairDecision.ReasonCodes[0] = "MUTATED"
			stored, ok := service.GetFlow("flow-001")
			if !ok || stored.RepairDecision == nil || stored.RepairDecision.ReasonCodes[0] == "MUTATED" {
				t.Fatalf("repair decision was not cloned: %#v", stored.RepairDecision)
			}
		})
	}
}
