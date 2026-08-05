package api

import (
	"context"
	"fmt"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

type internalOperationOptimizationExecutor struct {
	now func() time.Time
}

func newInternalOperationOptimizationExecutor() agentExecutor {
	return &internalOperationOptimizationExecutor{now: func() time.Time { return time.Now().UTC() }}
}

func (executor *internalOperationOptimizationExecutor) Execute(
	ctx context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentExecutionResult{}, err
	}
	if executor == nil {
		return AgentExecutionResult{}, fmt.Errorf("Internal operation optimization Agent executor is required")
	}
	flow, err := decodeDecisionInput[agentcontrol.Flow](request.Input, "flow")
	if err != nil {
		return AgentExecutionResult{}, err
	}
	if flow.DeploymentStatus == nil ||
		strings.TrimSpace(flow.DeploymentStatus.Data.DeploymentStatus.State) != agentcontrol.DeploymentStateRunning {
		return AgentExecutionResult{}, fmt.Errorf("operation Agent input requires a RUNNING deployment status")
	}
	if flow.OptimizationFeedback == nil {
		return AgentExecutionResult{}, fmt.Errorf("operation Agent input requires optimization feedback")
	}
	now := executor.now
	if now == nil {
		now = func() time.Time { return time.Now().UTC() }
	}
	decision := agentcontrol.ProposeRuleBasedScalingDecision(flow, now())
	if decision == nil {
		return AgentExecutionResult{}, fmt.Errorf("operation Agent could not produce a scaling decision")
	}
	parameters, err := encodeAnyMap(*decision)
	if err != nil {
		return AgentExecutionResult{}, fmt.Errorf("encode Internal operation Agent result: %w", err)
	}
	return AgentExecutionResult{
		RunID:  request.RunID,
		Agent:  agent.Name,
		Status: "completed",
		Proposal: AgentProposal{
			Action:     agentcontrol.OperationOptimizationDecisionAction,
			Parameters: parameters,
		},
		Evidence: map[string]any{
			"executor":       "internal_rule_based",
			"reasoning_mode": agentcontrol.ReasoningModeRuleBased,
		},
		DomainValidation: "scaling_decision",
	}, nil
}
