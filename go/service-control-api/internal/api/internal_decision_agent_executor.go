package api

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

type internalDecisionAgentExecutor struct {
	now func() time.Time
}

func newInternalDecisionAgentExecutor() agentExecutor {
	return &internalDecisionAgentExecutor{now: func() time.Time { return time.Now().UTC() }}
}

func (executor *internalDecisionAgentExecutor) Execute(
	ctx context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentExecutionResult{}, err
	}
	if executor == nil {
		return AgentExecutionResult{}, fmt.Errorf("Internal decision Agent executor is required")
	}
	profile, err := decodeDecisionInput[agentcontrol.ApplicationProfile](request.Input, "application_profile")
	if err != nil {
		return AgentExecutionResult{}, err
	}
	recommendation, err := decodeDecisionInput[agentcontrol.ResourceRecommendation](request.Input, "resource_recommendation")
	if err != nil {
		return AgentExecutionResult{}, err
	}
	now := executor.now
	if now == nil {
		now = func() time.Time { return time.Now().UTC() }
	}
	decision := agentcontrol.ProposeRuleBasedDecision(
		profile,
		recommendation,
		"decision-"+stringContextValue(request.Context, "correlation_id"),
		now().Format(time.RFC3339Nano),
	)
	parameters, err := encodeDecisionParameters(decision)
	if err != nil {
		return AgentExecutionResult{}, err
	}
	return AgentExecutionResult{
		RunID:  request.RunID,
		Agent:  agent.Name,
		Status: "completed",
		Proposal: AgentProposal{
			Action:     request.Action,
			Parameters: parameters,
		},
		Evidence: map[string]any{
			"executor":       "internal_rule_based",
			"reasoning_mode": decision.ReasoningMode,
		},
		DomainValidation: "deployment_decision",
	}, nil
}

func decodeDecisionInput[T any](input map[string]any, key string) (T, error) {
	var result T
	value, ok := input[key]
	if !ok {
		return result, fmt.Errorf("decision Agent input is missing %s", key)
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return result, fmt.Errorf("encode decision Agent input %s: %w", key, err)
	}
	if err := json.Unmarshal(encoded, &result); err != nil {
		return result, fmt.Errorf("decode decision Agent input %s: %w", key, err)
	}
	return result, nil
}

func encodeDecisionParameters(decision agentcontrol.AutomationDecision) (map[string]any, error) {
	encoded, err := json.Marshal(struct {
		Decision            string                          `json:"decision"`
		SelectedCandidateID string                          `json:"selected_candidate_id,omitempty"`
		Reason              string                          `json:"reason"`
		Confidence          float64                         `json:"confidence"`
		ReasoningMode       string                          `json:"reasoning_mode,omitempty"`
		CorrectionRequest   *agentcontrol.CorrectionRequest `json:"correction_request,omitempty"`
	}{
		Decision:            decision.Action,
		SelectedCandidateID: decision.SelectedCandidateID,
		Reason:              decision.Reason,
		Confidence:          decision.Confidence,
		ReasoningMode:       decision.ReasoningMode,
		CorrectionRequest:   decision.CorrectionRequest,
	})
	if err != nil {
		return nil, fmt.Errorf("encode Internal decision Agent result: %w", err)
	}
	var parameters map[string]any
	if err := json.Unmarshal(encoded, &parameters); err != nil {
		return nil, fmt.Errorf("decode Internal decision Agent result: %w", err)
	}
	return parameters, nil
}

func stringContextValue(values map[string]any, key string) string {
	value, _ := values[key].(string)
	return value
}
