package api

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

type registryDecisionAgentRuntime struct {
	config        ServerConfig
	runtimeAgents *runtimeAgentStore
	dispatcher    *agentDispatcher
}

func newDecisionAgentRuntime(
	config ServerConfig,
	runtimeAgents *runtimeAgentStore,
	dispatcher *agentDispatcher,
) agentcontrol.DecisionAgentRuntime {
	return &registryDecisionAgentRuntime{
		config:        config,
		runtimeAgents: runtimeAgents,
		dispatcher:    dispatcher,
	}
}

func (runtime *registryDecisionAgentRuntime) Decide(
	ctx context.Context,
	request agentcontrol.DecisionAgentRequest,
) (agentcontrol.DecisionAgentResult, error) {
	result := agentcontrol.DecisionAgentResult{Status: "failed"}
	if runtime == nil || runtime.runtimeAgents == nil || runtime.dispatcher == nil {
		return result, fmt.Errorf("decision Agent runtime dependencies are not configured")
	}
	if err := ensureContext(ctx); err != nil {
		return result, err
	}
	registry, err := loadAgentRegistry(runtime.config.path("config", "agent_registry.json"))
	if err != nil {
		return result, err
	}
	agent, err := resolveDecisionAgent(registry, runtime.runtimeAgents.list(), request.RequestedAgent)
	if err != nil {
		result.AgentName = strings.TrimSpace(request.RequestedAgent)
		result.RequestGuard = agentControlGuard(rejectedGuardDecision(err.Error()), "agent_registry")
		result.Message = "Decision Agent authorization was rejected."
		return result, err
	}
	result.AgentName = agent.Name
	result.Source = agent.Source

	input, err := decisionRuntimeInput(request)
	if err != nil {
		result.RequestGuard = agentControlGuard(rejectedGuardDecision(err.Error()), "agent_request")
		result.Message = "Decision Agent input could not be encoded."
		return result, err
	}
	executionRequest := AgentExecutionRequest{
		Capability: agentcontrol.AutomationCapability,
		Action:     agentcontrol.AutomationDecisionAction,
		Input:      input,
		Context: map[string]any{
			"correlation_id": request.CorrelationID,
			"trace_id":       request.TraceID,
		},
	}
	requestGuard := validateAgentExecutionRequest(agent, executionRequest)
	result.RequestGuard = agentControlGuard(requestGuard, "agent_request_guard")
	if !requestGuard.Valid {
		result.Message = "Decision Agent request was rejected."
		return result, fmt.Errorf("decision Agent request rejected: %s", requestGuard.Reason)
	}

	dispatchRequest := AgentDispatchRequest{
		RunID:      request.RunID,
		Agent:      agent.Name,
		Capability: executionRequest.Capability,
		Action:     executionRequest.Action,
		Input:      executionRequest.Input,
		Context:    executionRequest.Context,
	}
	execution, err := runtime.dispatcher.Dispatch(ctx, agent, dispatchRequest)
	if err != nil {
		result.Message = "Decision Agent execution failed."
		return result, fmt.Errorf("execute decision Agent: %w", err)
	}
	result.Status = execution.Status
	result.LatencyMS = execution.LatencyMS
	result.Message = execution.Message
	resultGuard := validateAgentExecutionResult(agent, dispatchRequest, execution)
	result.ResultGuard = agentControlGuard(resultGuard, "agent_result_guard")
	if !resultGuard.Valid {
		return result, fmt.Errorf("decision Agent result rejected: %s", resultGuard.Reason)
	}
	decision, err := decodeDecisionProposal(execution.Proposal.Parameters)
	if err != nil {
		result.ResultGuard = agentControlGuard(rejectedGuardDecision(err.Error()), "agent_result_guard")
		return result, err
	}
	result.Decision = decision
	return result, nil
}

func decisionRuntimeInput(request agentcontrol.DecisionAgentRequest) (map[string]any, error) {
	profile, err := encodeAnyMap(request.ApplicationProfile)
	if err != nil {
		return nil, fmt.Errorf("encode ApplicationProfile: %w", err)
	}
	recommendation, err := encodeAnyMap(request.ResourceRecommendation)
	if err != nil {
		return nil, fmt.Errorf("encode ResourceRecommendation: %w", err)
	}
	return map[string]any{
		"application_profile":     profile,
		"resource_recommendation": recommendation,
	}, nil
}

func encodeAnyMap(value any) (map[string]any, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(encoded, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func decodeDecisionProposal(parameters map[string]any) (agentcontrol.AutomationDecision, error) {
	encoded, err := json.Marshal(parameters)
	if err != nil {
		return agentcontrol.AutomationDecision{}, fmt.Errorf("encode decision Agent proposal: %w", err)
	}
	var proposal struct {
		Decision            string                          `json:"decision"`
		SelectedCandidateID string                          `json:"selected_candidate_id"`
		Reason              string                          `json:"reason"`
		Confidence          float64                         `json:"confidence"`
		ReasoningMode       string                          `json:"reasoning_mode"`
		CorrectionRequest   *agentcontrol.CorrectionRequest `json:"correction_request"`
	}
	if err := json.Unmarshal(encoded, &proposal); err != nil {
		return agentcontrol.AutomationDecision{}, fmt.Errorf("decode decision Agent proposal: %w", err)
	}
	if strings.TrimSpace(proposal.Decision) == "" {
		return agentcontrol.AutomationDecision{}, fmt.Errorf("decision Agent proposal is missing decision")
	}
	return agentcontrol.AutomationDecision{
		Action:              strings.ToUpper(strings.TrimSpace(proposal.Decision)),
		SelectedCandidateID: strings.TrimSpace(proposal.SelectedCandidateID),
		Reason:              strings.TrimSpace(proposal.Reason),
		Confidence:          proposal.Confidence,
		ReasoningMode:       strings.TrimSpace(proposal.ReasoningMode),
		CorrectionRequest:   proposal.CorrectionRequest,
	}, nil
}

func agentControlGuard(guard GuardDecision, name string) agentcontrol.GuardResult {
	status := agentcontrol.GuardRejected
	if guard.Valid {
		status = agentcontrol.GuardApproved
	}
	return agentcontrol.GuardResult{
		Status: status,
		Checks: []agentcontrol.GuardCheck{{
			Name:   name,
			Passed: guard.Valid,
			Reason: guard.Reason,
		}},
	}
}
