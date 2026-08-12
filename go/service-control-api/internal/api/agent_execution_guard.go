package api

import (
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

var forbiddenAgentExecutionKeys = map[string]struct{}{
	"api_key":         {},
	"authorization":   {},
	"credential":      {},
	"endpoint":        {},
	"invocation_path": {},
	"password":        {},
	"private_key":     {},
	"secret":          {},
	"target_url":      {},
	"token":           {},
}

func validateAgentExecutionRequest(agent AgentProfile, request AgentExecutionRequest) GuardDecision {
	switch {
	case !agent.Enabled:
		return rejectedGuardDecision("Agent is disabled")
	case strings.TrimSpace(request.Capability) == "":
		return rejectedGuardDecision("Agent capability is required")
	case strings.TrimSpace(request.Action) == "":
		return rejectedGuardDecision("Agent action is required")
	case !contains(agent.Capabilities, request.Capability):
		return rejectedGuardDecision(fmt.Sprintf(
			"capability is not registered for Agent %s: %s",
			agent.Name,
			request.Capability,
		))
	case !contains(agent.BoundedActions, request.Action):
		return rejectedGuardDecision(fmt.Sprintf(
			"action is outside the Agent boundary for %s: %s",
			agent.Name,
			request.Action,
		))
	}

	if key := findForbiddenAgentExecutionKey(request.Input); key != "" {
		return rejectedGuardDecision(fmt.Sprintf("Agent input contains a forbidden field: %s", key))
	}
	if key := findForbiddenAgentExecutionKey(request.Context); key != "" {
		return rejectedGuardDecision(fmt.Sprintf("Agent context contains a forbidden field: %s", key))
	}
	return GuardDecision{
		Valid:  true,
		Status: "approved",
		Reason: "Agent Registry authorizes the required capability and bounded action.",
	}
}

func validateAgentExecutionResult(
	agent AgentProfile,
	request AgentDispatchRequest,
	result AgentExecutionResult,
) GuardDecision {
	switch {
	case strings.TrimSpace(request.RunID) == "":
		return rejectedGuardDecision("Agent request run_id is required")
	case strings.TrimSpace(result.RunID) == "":
		return rejectedGuardDecision("Agent result run_id is required")
	case result.RunID != request.RunID:
		return rejectedGuardDecision("Agent result run_id does not match the dispatched run")
	case result.Agent != request.Agent || result.Agent != agent.Name:
		return rejectedGuardDecision("Agent result identity does not match the selected Agent")
	case !agentExecutionStatusAllowed(result.Status):
		return rejectedGuardDecision(fmt.Sprintf("Agent result status is not allowed: %s", result.Status))
	case result.Proposal.Action != request.Action:
		return rejectedGuardDecision("Agent result action does not match the dispatched action")
	case !contains(agent.BoundedActions, result.Proposal.Action):
		return rejectedGuardDecision(fmt.Sprintf(
			"Agent result action is outside the bounded action policy: %s",
			result.Proposal.Action,
		))
	case result.Manifest != nil && result.DomainValidation != "manifest_guard":
		return rejectedGuardDecision("DeploymentManifest result is missing Manifest Guard evidence")
	case result.Manifest == nil &&
		!(request.Action == agentcontrol.AutomationDecisionAction && result.DomainValidation == "deployment_decision") &&
		!(request.Action == agentcontrol.OperationOptimizationDecisionAction && result.DomainValidation == "scaling_decision") &&
		result.DomainValidation != "not_registered":
		return rejectedGuardDecision("Agent result must declare domain validation status")
	}
	return GuardDecision{
		Valid:  true,
		Status: "approved",
		Reason: "Agent result matches the dispatched identity and bounded action contract",
	}
}

func rejectedGuardDecision(reason string) GuardDecision {
	return GuardDecision{Valid: false, Status: "rejected", Reason: reason}
}

func agentExecutionStatusAllowed(status string) bool {
	switch status {
	case "completed", "rejected", "failed":
		return true
	default:
		return false
	}
}

func findForbiddenAgentExecutionKey(values map[string]any) string {
	for key, value := range values {
		normalized := normalizeAgentExecutionKey(key)
		if _, forbidden := forbiddenAgentExecutionKeys[normalized]; forbidden {
			return key
		}
		switch typed := value.(type) {
		case map[string]any:
			if found := findForbiddenAgentExecutionKey(typed); found != "" {
				return found
			}
		case []any:
			for _, item := range typed {
				if object, ok := item.(map[string]any); ok {
					if found := findForbiddenAgentExecutionKey(object); found != "" {
						return found
					}
				}
			}
		}
	}
	return ""
}

func normalizeAgentExecutionKey(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = strings.ReplaceAll(value, "-", "_")
	value = strings.ReplaceAll(value, " ", "_")
	return value
}
