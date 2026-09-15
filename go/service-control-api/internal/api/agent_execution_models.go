package api

import "kyunghee-aiops/service-control-api/internal/model"

type AgentExecutionRequest = model.AgentExecutionRequest
type AgentDispatchRequest = model.AgentDispatchRequest
type AgentProposal = model.AgentProposal
type AgentExecutionResult = model.AgentExecutionResult
type AgentExecutionResponse = model.AgentExecutionResponse
type AgentExecutionErrorResponse = model.AgentExecutionErrorResponse

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
