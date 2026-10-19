package agentcontrol

import (
	"context"
	"strings"
)

type DecisionAgentRuntime interface {
	Decide(context.Context, DecisionAgentRequest) (DecisionAgentResult, error)
}

type DecisionAgentRequest struct {
	RunID                  string                 `json:"run_id"`
	RequestedAgent         string                 `json:"requested_agent,omitempty"`
	CorrelationID          string                 `json:"correlation_id"`
	TraceID                string                 `json:"trace_id"`
	ApplicationProfile     ApplicationProfile     `json:"application_profile"`
	ResourceRecommendation ResourceRecommendation `json:"resource_recommendation"`
}

type DecisionAgentResult struct {
	AgentName    string             `json:"agent_name"`
	Source       string             `json:"source"`
	Status       string             `json:"status"`
	RequestGuard GuardResult        `json:"request_guard"`
	ResultGuard  GuardResult        `json:"result_guard"`
	Decision     AutomationDecision `json:"decision"`
	LatencyMS    int64              `json:"latency_ms"`
	Message      string             `json:"message,omitempty"`
}

func ProposeRuleBasedDecision(
	profile ApplicationProfile,
	recommendation ResourceRecommendation,
	decisionID string,
	createdAt string,
) AutomationDecision {
	if issues := validateApplicationRequirements(profile.Requirements); len(issues) > 0 {
		return AutomationDecision{
			DecisionID:    decisionID,
			Action:        ActionReject,
			Reason:        "Application requirements are invalid and must be corrected.",
			ReasoningMode: ReasoningModeRuleBased,
			Confidence:    1,
			CorrectionRequest: &CorrectionRequest{
				Target: CorrectionTargetApplicationProfile,
				Reason: "Correct the invalid Application Profile fields and submit it again.",
				Issues: issues,
			},
			CreatedAt: createdAt,
		}
	}

	candidate, issues := selectRecommendedCandidate(profile.Requirements, recommendation)
	if len(issues) > 0 {
		return AutomationDecision{
			DecisionID:    decisionID,
			Action:        ActionRetry,
			Reason:        "The Resource Recommendation cannot satisfy the Application Profile.",
			ReasoningMode: ReasoningModeRuleBased,
			Confidence:    1,
			CorrectionRequest: &CorrectionRequest{
				Target: CorrectionTargetResourceRecommendation,
				Reason: "Recommend another resource candidate that satisfies all minimum requirements.",
				Issues: issues,
			},
			CreatedAt: createdAt,
		}
	}

	return AutomationDecision{
		DecisionID:          decisionID,
		Action:              ActionDeploy,
		Reason:              "The selected resource candidate satisfies the minimum deployment requirements.",
		ReasoningMode:       ReasoningModeRuleBased,
		Confidence:          candidate.Scores.Total,
		SelectedCandidateID: candidate.CandidateID,
		CreatedAt:           createdAt,
	}
}

func validateDecisionAgentResult(
	profile ApplicationProfile,
	recommendation ResourceRecommendation,
	result DecisionAgentResult,
) GuardResult {
	decision := result.Decision
	if result.Status != "completed" {
		return rejectedDecisionGuard("decision_agent_status", "Decision Agent did not complete successfully.")
	}
	if result.RequestGuard.Status != GuardApproved {
		return rejectedDecisionGuard("agent_request_guard", "Decision Agent request was not approved.")
	}
	if result.ResultGuard.Status != GuardApproved {
		return rejectedDecisionGuard("agent_result_guard", "Decision Agent result was not approved.")
	}
	if decision.Confidence < 0 || decision.Confidence > 1 {
		return rejectedDecisionGuard("decision_confidence", "Decision confidence must be between 0 and 1.")
	}
	if strings.TrimSpace(decision.Reason) == "" {
		return rejectedDecisionGuard("decision_reason", "Decision reason is required.")
	}

	switch decision.Action {
	case ActionReject:
		return GuardResult{
			Status: GuardRejected,
			Checks: []GuardCheck{{
				Name:   "deployment_decision",
				Passed: true,
				Reason: "The Agent produced a valid REJECT decision.",
			}},
		}
	case ActionRetry:
		if decision.CorrectionRequest == nil || strings.TrimSpace(decision.CorrectionRequest.Reason) == "" {
			return rejectedDecisionGuard("correction_request", "RETRY requires a correction request.")
		}
		return GuardResult{
			Status: GuardRetryRequired,
			Checks: []GuardCheck{{
				Name:   "deployment_decision",
				Passed: true,
				Reason: "The Agent produced a valid RETRY decision.",
			}},
			Issues: append([]ValidationIssue(nil), decision.CorrectionRequest.Issues...),
		}
	case ActionDeploy:
		candidate, ok := findDecisionCandidate(recommendation, decision.SelectedCandidateID)
		if !ok {
			return retryDecisionGuard(
				"selected_candidate",
				"The selected candidate is not present in ResourceRecommendation.",
			)
		}
		selected := recommendation
		selected.SelectedCandidateID = candidate.CandidateID
		_, issues := selectRecommendedCandidate(profile.Requirements, selected)
		if len(issues) > 0 {
			return GuardResult{
				Status: GuardRetryRequired,
				Checks: []GuardCheck{{
					Name:   "resource_compatibility",
					Passed: false,
					Reason: "The Agent-selected resource does not satisfy the Application Profile.",
				}},
				Issues: append([]ValidationIssue(nil), issues...),
			}
		}
		return GuardResult{
			Status: GuardApproved,
			Checks: []GuardCheck{
				{Name: "application_requirements", Passed: true, Reason: "Application requirements are structurally and semantically valid."},
				{Name: "resource_compatibility", Passed: true, Reason: "The Agent-selected resource satisfies all minimum requirements."},
				{Name: "agent_result", Passed: true, Reason: "The selected Agent result passed the deployment decision contract."},
			},
		}
	default:
		return rejectedDecisionGuard("deployment_decision", "Decision must be DEPLOY, REJECT, or RETRY.")
	}
}

func findDecisionCandidate(
	recommendation ResourceRecommendation,
	candidateID string,
) (ResourceCandidate, bool) {
	candidateID = strings.TrimSpace(candidateID)
	if candidateID == "" {
		return ResourceCandidate{}, false
	}
	for _, candidate := range recommendation.Candidates {
		if candidate.CandidateID == candidateID {
			return candidate, true
		}
	}
	return ResourceCandidate{}, false
}

func rejectedDecisionGuard(name string, reason string) GuardResult {
	return GuardResult{
		Status: GuardRejected,
		Checks: []GuardCheck{{Name: name, Passed: false, Reason: reason}},
	}
}

func retryDecisionGuard(name string, reason string) GuardResult {
	return GuardResult{
		Status: GuardRetryRequired,
		Checks: []GuardCheck{{Name: name, Passed: false, Reason: reason}},
	}
}
