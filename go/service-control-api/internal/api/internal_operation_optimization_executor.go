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
	input, err := decodeDecisionInput[trustedOperationInputSnapshot](request.Input, "operation_input")
	if err != nil {
		return AgentExecutionResult{}, err
	}
	if input.DeploymentStatus == nil ||
		strings.TrimSpace(input.DeploymentStatus.State) != agentcontrol.DeploymentStateRunning {
		return AgentExecutionResult{}, fmt.Errorf("operation Agent input requires a RUNNING deployment status")
	}
	if input.OptimizationFeedback == nil {
		return AgentExecutionResult{}, fmt.Errorf("operation Agent input requires optimization feedback")
	}
	flow := agentcontrol.Flow{
		ApplicationContext: &agentcontrol.ApplicationContextEnvelope{
			Data: agentcontrol.ApplicationContextData{
				ApplicationProfile: agentcontrol.ApplicationProfile{
					Requirements: agentcontrol.ApplicationRequirements{
						Deployment: agentcontrol.DeploymentRequirements{
							ReplicasMin: input.MinimumReplicas,
							ReplicasMax: input.MaximumReplicas,
						},
						SLO: input.SLO,
					},
				},
			},
		},
		DeploymentPlan: &agentcontrol.DeploymentPlan{
			InferenceConfiguration: agentcontrol.InferenceConfiguration{Replicas: input.CurrentReplicas},
		},
		DeploymentStatus: &agentcontrol.DeploymentStatusEnvelope{
			Data: agentcontrol.DeploymentStatusData{DeploymentStatus: *input.DeploymentStatus},
		},
		OptimizationFeedback: &agentcontrol.OptimizationFeedbackEnvelope{
			Data: agentcontrol.OptimizationFeedbackData{OptimizationFeedback: *input.OptimizationFeedback},
		},
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
