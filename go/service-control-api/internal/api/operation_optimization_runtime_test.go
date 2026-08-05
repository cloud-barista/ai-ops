package api

import (
	"context"
	"errors"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestNewServiceRegistersInternalOperationOptimizationRuntime(t *testing.T) {
	service := NewService(NewServerConfig())
	if service.operationRuntime == nil {
		t.Fatal("operation optimization runtime is not configured")
	}
	result, err := service.operationRuntime.Optimize(context.Background(), operationRuntimeRequest())
	if err != nil {
		t.Fatalf("optimize with service-registered Internal Agent: %v", err)
	}
	if result.AgentName != agentcontrol.OperationOptimizationAgentName || result.Status != "completed" {
		t.Fatalf("service operation result = %#v", result)
	}
}

func TestOperationOptimizationRuntimeDispatchesInternalAgentThroughGuards(t *testing.T) {
	dispatcher := newAgentDispatcher(
		map[string]agentExecutor{
			agentcontrol.OperationOptimizationAgentName: newInternalOperationOptimizationExecutor(),
		},
		&recordingAgentExecutor{},
	)
	runtime := newOperationOptimizationRuntime(NewServerConfig(), newRuntimeAgentStore(), dispatcher)

	result, err := runtime.Optimize(context.Background(), operationRuntimeRequest())
	if err != nil {
		t.Fatalf("optimize with Internal operation Agent: %v", err)
	}
	if result.Source != agentSourceConfiguration || result.Status != "completed" {
		t.Fatalf("operation result = %#v", result)
	}
	if result.RequestGuard.Status != agentcontrol.GuardApproved ||
		result.ResultGuard.Status != agentcontrol.GuardApproved {
		t.Fatalf("Guard evidence = %#v", result)
	}
	if result.Decision.Action != agentcontrol.ScalingActionScaleOut || result.Decision.DesiredReplicas != 2 {
		t.Fatalf("scaling decision = %#v", result.Decision)
	}
}

func TestOperationOptimizationRuntimeDispatchesOnlyTrustedOperationSnapshot(t *testing.T) {
	decision := agentcontrol.ScalingDecision{
		Action:          agentcontrol.ScalingActionScaleOut,
		CurrentReplicas: 1,
		DesiredReplicas: 2,
	}
	internal := &recordingAgentExecutor{result: AgentExecutionResult{
		RunID:  "run-operation-001",
		Agent:  agentcontrol.OperationOptimizationAgentName,
		Status: "completed",
		Proposal: AgentProposal{
			Action:     agentcontrol.OperationOptimizationDecisionAction,
			Parameters: mustAnyMap(t, decision),
		},
		DomainValidation: "scaling_decision",
	}}
	runtime := newOperationOptimizationRuntime(
		NewServerConfig(),
		newRuntimeAgentStore(),
		newAgentDispatcher(map[string]agentExecutor{agentcontrol.OperationOptimizationAgentName: internal}, nil),
	)

	if _, err := runtime.Optimize(context.Background(), operationRuntimeRequest()); err != nil {
		t.Fatalf("optimize with trusted snapshot: %v", err)
	}
	if _, ok := internal.request.Input["flow"]; ok {
		t.Fatalf("dispatcher input must not contain a complete Flow: %#v", internal.request.Input)
	}
	if _, ok := internal.request.Input["operation_input"]; !ok {
		t.Fatalf("dispatcher input is missing operation snapshot: %#v", internal.request.Input)
	}
}

func TestOperationOptimizationRuntimeDoesNotFallbackAfterRuntimeDispatchFailure(t *testing.T) {
	store := newRuntimeAgentStore()
	runtimeAgent := operationAgent("RuntimeOperationAgent", true)
	runtimeAgent.Source = agentSourceRuntime
	if err := store.add(runtimeAgent); err != nil {
		t.Fatalf("register Runtime operation Agent: %v", err)
	}
	internal := &recordingAgentExecutor{}
	dispatcher := newAgentDispatcher(
		map[string]agentExecutor{agentcontrol.OperationOptimizationAgentName: internal},
		&recordingAgentExecutor{err: errors.New("runtime endpoint unavailable")},
	)
	runtime := newOperationOptimizationRuntime(NewServerConfig(), store, dispatcher)
	request := operationRuntimeRequest()
	request.RequestedAgent = runtimeAgent.Name

	result, err := runtime.Optimize(context.Background(), request)
	if err == nil {
		t.Fatal("expected Runtime operation Agent dispatch failure")
	}
	if result.AgentName != runtimeAgent.Name || result.Status != "failed" {
		t.Fatalf("runtime failure result = %#v", result)
	}
	if internal.calls != 0 {
		t.Fatalf("Internal Agent fallback calls = %d, want 0", internal.calls)
	}
}

func TestOperationOptimizationRuntimeRejectsMalformedRuntimeResult(t *testing.T) {
	store := newRuntimeAgentStore()
	runtimeAgent := operationAgent("RuntimeOperationAgent", true)
	runtimeAgent.Source = agentSourceRuntime
	if err := store.add(runtimeAgent); err != nil {
		t.Fatalf("register Runtime operation Agent: %v", err)
	}
	dispatcher := newAgentDispatcher(nil, &recordingAgentExecutor{result: AgentExecutionResult{
		RunID:  "run-operation-001",
		Agent:  runtimeAgent.Name,
		Status: "unknown",
		Proposal: AgentProposal{
			Action: agentcontrol.OperationOptimizationDecisionAction,
		},
		DomainValidation: "scaling_decision",
	}})
	runtime := newOperationOptimizationRuntime(NewServerConfig(), store, dispatcher)
	request := operationRuntimeRequest()
	request.RequestedAgent = runtimeAgent.Name

	result, err := runtime.Optimize(context.Background(), request)
	if err == nil {
		t.Fatal("expected malformed Runtime Agent status to be rejected")
	}
	if result.ResultGuard.Status != agentcontrol.GuardRejected {
		t.Fatalf("result Guard = %#v", result.ResultGuard)
	}
}

func operationRuntimeRequest() agentcontrol.OperationOptimizationRequest {
	return agentcontrol.OperationOptimizationRequest{
		RunID:         "run-operation-001",
		CorrelationID: "flow-operation-001",
		TraceID:       "trace-operation-001",
		Flow:          operationReadyFlow(),
	}
}
