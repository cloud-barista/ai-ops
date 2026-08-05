package api

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

type registryOperationOptimizationRuntime struct {
	config        ServerConfig
	runtimeAgents *runtimeAgentStore
	dispatcher    *agentDispatcher
}

type trustedOperationInputSnapshot struct {
	DeploymentStatus     *agentcontrol.DeploymentStatus     `json:"deployment_status,omitempty"`
	OptimizationFeedback *agentcontrol.OptimizationFeedback `json:"optimization_feedback,omitempty"`
	SLO                  agentcontrol.SLORequirements       `json:"slo"`
	CurrentReplicas      int                                `json:"current_replicas"`
	MinimumReplicas      int                                `json:"minimum_replicas"`
	MaximumReplicas      int                                `json:"maximum_replicas"`
}

func newOperationOptimizationRuntime(
	config ServerConfig,
	runtimeAgents *runtimeAgentStore,
	dispatcher *agentDispatcher,
) agentcontrol.OperationOptimizationRuntime {
	return &registryOperationOptimizationRuntime{
		config:        config,
		runtimeAgents: runtimeAgents,
		dispatcher:    dispatcher,
	}
}

func (runtime *registryOperationOptimizationRuntime) Optimize(
	ctx context.Context,
	request agentcontrol.OperationOptimizationRequest,
) (agentcontrol.OperationOptimizationResult, error) {
	result := agentcontrol.OperationOptimizationResult{Status: "failed"}
	if runtime == nil || runtime.runtimeAgents == nil || runtime.dispatcher == nil {
		return result, fmt.Errorf("operation optimization Agent runtime dependencies are not configured")
	}
	if err := ensureContext(ctx); err != nil {
		return result, err
	}
	registry, err := loadAgentRegistry(runtime.config.path("config", "agent_registry.json"))
	if err != nil {
		return result, err
	}
	agent, err := resolveOperationAgent(registry, runtime.runtimeAgents.list(), request.RequestedAgent)
	if err != nil {
		result.AgentName = strings.TrimSpace(request.RequestedAgent)
		result.RequestGuard = agentControlGuard(rejectedGuardDecision(err.Error()), "agent_registry")
		result.Message = "Operation optimization Agent authorization was rejected."
		return result, err
	}
	result.AgentName = agent.Name
	result.Source = agent.Source

	input, err := operationOptimizationRuntimeInput(request)
	if err != nil {
		result.RequestGuard = agentControlGuard(rejectedGuardDecision(err.Error()), "agent_request")
		result.Message = "Operation optimization Agent input could not be encoded."
		return result, err
	}
	executionRequest := AgentExecutionRequest{
		Capability: agentcontrol.OperationOptimizationCapability,
		Action:     agentcontrol.OperationOptimizationDecisionAction,
		Input:      input,
		Context: map[string]any{
			"correlation_id": request.CorrelationID,
			"trace_id":       request.TraceID,
		},
	}
	requestGuard := validateAgentExecutionRequest(agent, executionRequest)
	result.RequestGuard = agentControlGuard(requestGuard, "agent_request_guard")
	if !requestGuard.Valid {
		result.Message = "Operation optimization Agent request was rejected."
		return result, fmt.Errorf("operation optimization Agent request rejected: %s", requestGuard.Reason)
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
		result.Message = "Operation optimization Agent execution failed."
		return result, fmt.Errorf("execute operation optimization Agent: %w", err)
	}
	result.Status = execution.Status
	result.LatencyMS = execution.LatencyMS
	result.Message = execution.Message
	resultGuard := validateAgentExecutionResult(agent, dispatchRequest, execution)
	result.ResultGuard = agentControlGuard(resultGuard, "agent_result_guard")
	if !resultGuard.Valid {
		return result, fmt.Errorf("operation optimization Agent result rejected: %s", resultGuard.Reason)
	}
	decision, err := decodeScalingDecisionProposal(execution.Proposal.Parameters)
	if err != nil {
		result.ResultGuard = agentControlGuard(rejectedGuardDecision(err.Error()), "agent_result_guard")
		return result, err
	}
	result.Decision = decision
	return agentcontrol.CanonicalizeOperationOptimizationResult(result), nil
}

func operationOptimizationRuntimeInput(
	request agentcontrol.OperationOptimizationRequest,
) (map[string]any, error) {
	snapshot := trustedOperationInputSnapshot{}
	if request.Flow.DeploymentStatus != nil {
		status := request.Flow.DeploymentStatus.Data.DeploymentStatus
		snapshot.DeploymentStatus = &status
	}
	if request.Flow.OptimizationFeedback != nil {
		feedback := request.Flow.OptimizationFeedback.Data.OptimizationFeedback
		snapshot.OptimizationFeedback = &feedback
	}
	if request.Flow.ApplicationContext != nil {
		requirements := request.Flow.ApplicationContext.Data.ApplicationProfile.Requirements
		snapshot.SLO = requirements.SLO
		snapshot.MinimumReplicas = requirements.Deployment.ReplicasMin
		snapshot.MaximumReplicas = requirements.Deployment.ReplicasMax
	}
	if request.Flow.DeploymentPlan != nil {
		snapshot.CurrentReplicas = request.Flow.DeploymentPlan.InferenceConfiguration.Replicas
	}
	input, err := encodeAnyMap(snapshot)
	if err != nil {
		return nil, fmt.Errorf("encode trusted operation input: %w", err)
	}
	return map[string]any{"operation_input": input}, nil
}

func decodeScalingDecisionProposal(parameters map[string]any) (agentcontrol.ScalingDecision, error) {
	encoded, err := json.Marshal(parameters)
	if err != nil {
		return agentcontrol.ScalingDecision{}, fmt.Errorf("encode operation Agent proposal: %w", err)
	}
	var decision agentcontrol.ScalingDecision
	if err := json.Unmarshal(encoded, &decision); err != nil {
		return agentcontrol.ScalingDecision{}, fmt.Errorf("decode operation Agent proposal: %w", err)
	}
	if strings.TrimSpace(decision.Action) == "" {
		return agentcontrol.ScalingDecision{}, fmt.Errorf("operation Agent proposal is missing scaling action")
	}
	return decision, nil
}
