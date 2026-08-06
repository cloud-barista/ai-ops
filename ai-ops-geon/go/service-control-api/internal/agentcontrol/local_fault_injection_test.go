package agentcontrol

import (
	"context"
	"testing"
)

type localFaultCase struct {
	name           string
	errorCode      string
	expectedAction string
	expectedReason string
}

// TestLocalFaultInjectionExperiment replays the status and feedback events
// emitted by a failed local deployment and verifies the repair contract.
func TestLocalFaultInjectionExperiment(t *testing.T) {
	cases := []localFaultCase{
		{
			name:           "gpu_oom",
			errorCode:      ErrorCodeGPUOOM,
			expectedAction: ActionAdjustResourceRequirement,
			expectedReason: ReasonFailureRequiresAdjustment,
		},
		{
			name:           "cuda_mismatch",
			errorCode:      ErrorCodeCUDAMismatch,
			expectedAction: ActionRequestAlternativeResource,
			expectedReason: ReasonFailureRequiresAlternative,
		},
		{
			name:           "resource_unavailable",
			errorCode:      ErrorCodeResourceUnavailable,
			expectedAction: ActionRequestAlternativeResource,
			expectedReason: ReasonFailureRequiresAlternative,
		},
		{
			name:           "resource_insufficient",
			errorCode:      ErrorCodeResourceInsufficient,
			expectedAction: ActionRequestAlternativeResource,
			expectedReason: ReasonFailureRequiresAlternative,
		},
		{
			name:           "transient_deployment",
			errorCode:      ErrorCodeTransientDeployment,
			expectedAction: ActionRetryDeployment,
			expectedReason: ReasonFailureTransient,
		},
		{
			name:           "unknown_failure",
			errorCode:      "UNKNOWN_FAILURE",
			expectedAction: ActionRequestUserClarification,
			expectedReason: ReasonFailureNeedsClarification,
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			service := NewService()
			approved := createApprovedFlow(t, service)

			status := validDeploymentStatusEnvelope()
			status.Data.DeploymentStatus.State = DeploymentStateFailed
			status.Data.DeploymentStatus.ErrorCode = testCase.errorCode
			status.Data.DeploymentStatus.Message = "local fault injection: " + testCase.errorCode
			if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
				t.Fatalf("receive failed deployment status: %v", err)
			}

			feedback := validOptimizationFeedbackEnvelope()
			feedback.Data.OptimizationFeedback.Outcome = FeedbackOutcomeFailed
			feedback.Data.OptimizationFeedback.ErrorCode = testCase.errorCode
			feedback.Data.OptimizationFeedback.SLOViolations = []string{"deployment_failed"}
			flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
			if err != nil {
				t.Fatalf("receive failed optimization feedback: %v", err)
			}
			if flow.RepairDecision == nil {
				t.Fatal("repair decision was not generated")
			}
			if flow.RepairDecision.Action != testCase.expectedAction {
				t.Fatalf("repair action = %q, want %q", flow.RepairDecision.Action, testCase.expectedAction)
			}
			if !containsString(flow.RepairDecision.ReasonCodes, testCase.expectedReason) ||
				!containsString(flow.RepairDecision.ReasonCodes, testCase.errorCode) {
				t.Fatalf("repair reason codes = %#v", flow.RepairDecision.ReasonCodes)
			}
			if flow.FeedbackSummary == nil || flow.FeedbackSummary.Success {
				t.Fatalf("feedback summary = %#v, want failed", flow.FeedbackSummary)
			}
			if flow.FeedbackSummary.ErrorCode != testCase.errorCode {
				t.Fatalf("feedback error code = %q, want %q", flow.FeedbackSummary.ErrorCode, testCase.errorCode)
			}
			experiences := service.QueryExperiences(ExperienceQuery{
				ProfileID:   "profile-001",
				CandidateID: approved.Decision.SelectedCandidateID,
				ErrorCode:   testCase.errorCode,
				Limit:       1,
			})
			if len(experiences) != 1 || experiences[0].Success {
				t.Fatalf("experiences = %#v, want one failed experience", experiences)
			}
			if testCase.expectedAction == ActionRetryDeployment {
				policy := flow.RepairDecision.RetryPolicy
				if policy == nil || policy.MaxAttempts != 2 || policy.Attempt != 1 {
					t.Fatalf("retry policy = %#v, want attempt 1 of 2", policy)
				}
			}
			t.Logf("fault=%s action=%s repair_reason=%s experience_error=%s", testCase.errorCode, flow.RepairDecision.Action, testCase.expectedReason, experiences[0].ErrorCode)
		})
	}
}

func TestLocalSLOFaultInjection(t *testing.T) {
	service := NewService()
	application := validApplicationContextEnvelope()
	application.Data.ApplicationProfile.Requirements.Deployment.ReplicasMax = 2
	application.Data.ApplicationProfile.Requirements.SLO.LatencyP95MSMax = 2000
	application.Data.ApplicationProfile.Requirements.SLO.ThroughputRPSMin = 5
	if _, err := service.ReceiveApplicationContext(context.Background(), application); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	flow, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope())
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}

	status := validDeploymentStatusEnvelope()
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive running deployment status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.Metrics.Inference.LatencyP95MS = 2500
	feedback.Data.OptimizationFeedback.Metrics.Inference.ThroughputRPS = 4
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms", "throughput_rps"}
	updated, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive SLO feedback: %v", err)
	}
	if updated.ScalingDecision == nil || updated.ScalingDecision.Action != ScalingActionScaleOut {
		t.Fatalf("scaling decision = %#v, want SCALE_OUT", updated.ScalingDecision)
	}
	if updated.ScalingDecision.CurrentReplicas != 1 || updated.ScalingDecision.DesiredReplicas != 2 {
		t.Fatalf("replicas = %d -> %d, want 1 -> 2", updated.ScalingDecision.CurrentReplicas, updated.ScalingDecision.DesiredReplicas)
	}
	if updated.RepairDecision != nil {
		t.Fatalf("successful SLO feedback must not create failure repair: %#v", updated.RepairDecision)
	}
	t.Logf("slo_fault=latency_p95_ms,throughput_rps action=%s replicas=%d->%d", updated.ScalingDecision.Action, flow.DeploymentPlan.InferenceConfiguration.Replicas, updated.ScalingDecision.DesiredReplicas)
}
