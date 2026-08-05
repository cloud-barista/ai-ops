package agentcontrol

import (
	"strings"
	"time"
)

func buildRepairDecision(flow Flow, now time.Time) *AutomationDecision {
	if flow.Decision == nil {
		return nil
	}

	failed := false
	errorCode := ""
	if flow.DeploymentStatus != nil {
		status := flow.DeploymentStatus.Data.DeploymentStatus
		failed = status.State == DeploymentStateFailed
		errorCode = strings.TrimSpace(status.ErrorCode)
	}
	if flow.OptimizationFeedback != nil {
		feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
		if feedback.Outcome == FeedbackOutcomeFailed {
			failed = true
		}
		if feedback.ErrorCode != "" {
			errorCode = strings.TrimSpace(feedback.ErrorCode)
		}
	}
	if !failed {
		return nil
	}

	action := ActionRequestUserClarification
	reasonCode := ReasonFailureNeedsClarification
	reason := "The deployment failed and requires additional information before repair."
	decision := &AutomationDecision{
		DecisionID:                 flow.Decision.DecisionID + "-repair",
		ReasoningMode:              ReasoningModeRuleBased,
		Confidence:                 1,
		SelectedCandidateID:        flow.Decision.SelectedCandidateID,
		RequiresManifestGeneration: false,
		CreatedAt:                  now.Format(time.RFC3339Nano),
	}

	switch errorCode {
	case ErrorCodeGPUOOM:
		action = ActionAdjustResourceRequirement
		reasonCode = ReasonFailureRequiresAdjustment
		reason = "GPU OOM requires a higher accelerator memory requirement before redeployment."
		decision.CorrectionRequest = &CorrectionRequest{
			Target: CorrectionTargetApplicationProfile,
			Reason: "Increase the minimum accelerator memory and request a new resource recommendation.",
			Issues: []ValidationIssue{{
				Field:  "requirements.accelerator.memory_mib_min_per_device",
				Code:   "DEPLOYMENT_FAILURE_GPU_OOM",
				Reason: "The deployment reported GPU OOM.",
				Actual: errorCode,
			}},
		}
	case ErrorCodeCUDAMismatch, ErrorCodeResourceUnavailable:
		action = ActionRequestAlternativeResource
		reasonCode = ReasonFailureRequiresAlternative
		reason = "The deployment failed because the selected resource is not compatible or available."
		decision.CorrectionRequest = &CorrectionRequest{
			Target: CorrectionTargetResourceRecommendation,
			Reason: "Request an alternative resource candidate and exclude the failed candidate.",
			Issues: []ValidationIssue{{
				Field:  "resource_recommendation.selected_candidate_id",
				Code:   "DEPLOYMENT_FAILURE_RESOURCE_UNSUITABLE",
				Reason: "The selected resource cannot be used for this deployment.",
				Actual: errorCode,
			}},
		}
	case ErrorCodeTransientDeployment:
		action = ActionRetryDeployment
		reasonCode = ReasonFailureTransient
		reason = "The deployment failed transiently and can be retried with the same requirements."
		decision.RetryPolicy = &RetryPolicy{MaxAttempts: 2, Attempt: 1}
	}
	decision.Action = action
	decision.Reason = reason
	decision.ReasonCodes = []string{reasonCode}
	if errorCode != "" {
		decision.ReasonCodes = append(decision.ReasonCodes, errorCode)
	}
	return decision
}
