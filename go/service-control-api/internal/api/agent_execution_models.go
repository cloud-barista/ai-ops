package api

import (
	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
)

type AgentExecutionRequest struct {
	Capability string         `json:"capability" validate:"required"`
	Action     string         `json:"action" validate:"required"`
	Input      map[string]any `json:"input,omitempty"`
	Context    map[string]any `json:"context,omitempty"`
}

type AgentDispatchRequest struct {
	RunID      string         `json:"run_id"`
	Agent      string         `json:"agent"`
	Capability string         `json:"capability"`
	Action     string         `json:"action"`
	Input      map[string]any `json:"input,omitempty"`
	Context    map[string]any `json:"context,omitempty"`
}

type AgentProposal struct {
	Action     string         `json:"action"`
	Parameters map[string]any `json:"parameters,omitempty"`
}

type AgentExecutionResult struct {
	RunID            string                            `json:"run_id"`
	Agent            string                            `json:"agent"`
	Status           string                            `json:"status"`
	Proposal         AgentProposal                     `json:"proposal"`
	Result           map[string]any                    `json:"result,omitempty"`
	Evidence         map[string]any                    `json:"evidence,omitempty"`
	Message          string                            `json:"message,omitempty"`
	LatencyMS        int64                             `json:"latency_ms"`
	Manifest         *appdeploy.DeploymentManifest     `json:"manifest,omitempty"`
	Generation       *deploymentplanner.GenerateResult `json:"generation,omitempty"`
	DomainValidation string                            `json:"domain_validation"`
}

type AgentExecutionResponse struct {
	RequestID     string               `json:"request_id"`
	RunID         string               `json:"run_id"`
	SelectedAgent AgentProfile         `json:"selected_agent"`
	RequestGuard  GuardDecision        `json:"request_guard"`
	Execution     AgentExecutionResult `json:"execution"`
	ResultGuard   GuardDecision        `json:"result_guard"`
}

type AgentExecutionErrorResponse struct {
	AgentExecutionResponse
	Valid   bool   `json:"valid"`
	Message string `json:"message"`
	Reason  string `json:"reason,omitempty"`
}

func agentExecutionError(
	message string,
	result AgentExecutionResponse,
) AgentExecutionErrorResponse {
	reason := result.ResultGuard.Reason
	if reason == "" {
		reason = result.RequestGuard.Reason
	}
	safeResult := result
	safeResult.Execution.Message = ""
	return AgentExecutionErrorResponse{
		AgentExecutionResponse: safeResult,
		Valid:                  false,
		Message:                message,
		Reason:                 reason,
	}
}
