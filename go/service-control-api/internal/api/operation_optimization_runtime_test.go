package api

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
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

	request := operationRuntimeRequest()
	result, err := runtime.Optimize(context.Background(), request)
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
	if result.RunID != request.RunID {
		t.Fatalf("Internal operation run_id = %q, want %q", result.RunID, request.RunID)
	}
	if result.Decision.Action != agentcontrol.ScalingActionScaleOut || result.Decision.DesiredReplicas != 2 {
		t.Fatalf("scaling decision = %#v", result.Decision)
	}
}

func TestOperationOptimizationRuntimeDispatchesRuntimeAgentWithMatchingRunID(t *testing.T) {
	store := newRuntimeAgentStore()
	runtimeAgent := operationAgent("RuntimeOperationAgent", true)
	runtimeAgent.Source = agentSourceRuntime
	if err := store.add(runtimeAgent); err != nil {
		t.Fatalf("register Runtime operation Agent: %v", err)
	}
	request := operationRuntimeRequest()
	request.RequestedAgent = runtimeAgent.Name
	executor := &recordingAgentExecutor{result: validRuntimeOperationExecution(t, runtimeAgent.Name, request.RunID)}
	runtime := newOperationOptimizationRuntime(
		NewServerConfig(), store, newAgentDispatcher(nil, executor),
	)

	result, err := runtime.Optimize(context.Background(), request)
	if err != nil {
		t.Fatalf("optimize with Runtime operation Agent: %v", err)
	}
	if executor.request.RunID != request.RunID || result.RunID != request.RunID {
		t.Fatalf("Runtime operation IDs: dispatch=%q result=%q want=%q", executor.request.RunID, result.RunID, request.RunID)
	}
}

func TestOperationOptimizationRuntimeRejectsEmptyOrMismatchedRuntimeRunID(t *testing.T) {
	tests := []struct {
		name        string
		resultRunID string
	}{
		{name: "empty", resultRunID: ""},
		{name: "mismatched", resultRunID: "run-operation-other"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store := newRuntimeAgentStore()
			runtimeAgent := operationAgent("RuntimeOperationAgent", true)
			runtimeAgent.Source = agentSourceRuntime
			if err := store.add(runtimeAgent); err != nil {
				t.Fatalf("register Runtime operation Agent: %v", err)
			}
			request := operationRuntimeRequest()
			request.RequestedAgent = runtimeAgent.Name
			execution := validRuntimeOperationExecution(t, runtimeAgent.Name, test.resultRunID)
			runtime := newOperationOptimizationRuntime(
				NewServerConfig(), store,
				newAgentDispatcher(nil, &recordingAgentExecutor{result: execution}),
			)

			result, err := runtime.Optimize(context.Background(), request)
			if err == nil {
				t.Fatal("expected Runtime operation run_id to be rejected")
			}
			if result.ResultGuard.Status != agentcontrol.GuardRejected ||
				!strings.Contains(result.ResultGuard.Checks[0].Reason, "run_id") {
				t.Fatalf("Runtime result Guard = %#v", result.ResultGuard)
			}
		})
	}
}

func TestOperationOptimizationRuntimeRejectsEmptyRequestRunIDBeforeDispatch(t *testing.T) {
	executor := &recordingAgentExecutor{}
	runtime := newOperationOptimizationRuntime(
		NewServerConfig(), newRuntimeAgentStore(),
		newAgentDispatcher(map[string]agentExecutor{
			agentcontrol.OperationOptimizationAgentName: executor,
		}, nil),
	)
	request := operationRuntimeRequest()
	request.RunID = "   "

	result, err := runtime.Optimize(context.Background(), request)
	if err == nil {
		t.Fatal("expected empty operation request run_id to be rejected")
	}
	if executor.calls != 0 {
		t.Fatalf("operation executor calls = %d, want 0", executor.calls)
	}
	if result.RequestGuard.Status != agentcontrol.GuardRejected {
		t.Fatalf("operation request Guard = %#v", result.RequestGuard)
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

func TestOperationOptimizationRuntimeSanitizesFailedRuntimeResultWithoutDecodingProposal(t *testing.T) {
	store := newRuntimeAgentStore()
	runtimeAgent := operationAgent("RuntimeOperationAgent", true)
	runtimeAgent.Source = agentSourceRuntime
	if err := store.add(runtimeAgent); err != nil {
		t.Fatalf("register Runtime operation Agent: %v", err)
	}
	const sensitiveMessage = "Authorization: Bearer super-secret-token"
	const sensitiveProposal = "private endpoint https://runtime.internal/token"
	dispatcher := newAgentDispatcher(nil, &recordingAgentExecutor{result: AgentExecutionResult{
		RunID:   "run-operation-001",
		Agent:   runtimeAgent.Name,
		Status:  "failed",
		Message: sensitiveMessage,
		Proposal: AgentProposal{
			Action: agentcontrol.OperationOptimizationDecisionAction,
			Parameters: map[string]any{
				"action":           agentcontrol.ScalingActionScaleOut,
				"current_replicas": 1,
				"desired_replicas": 2,
				"reason":           sensitiveProposal,
				"evidence":         []string{sensitiveProposal},
			},
		},
		DomainValidation: "scaling_decision",
	}})
	runtime := newOperationOptimizationRuntime(NewServerConfig(), store, dispatcher)
	request := operationRuntimeRequest()
	request.RequestedAgent = runtimeAgent.Name

	result, err := runtime.Optimize(context.Background(), request)
	if err != nil {
		t.Fatalf("failed Runtime Agent result must remain a safe Flow result: %v", err)
	}
	if result.Status != "failed" || result.Message != "Operation Agent execution failed." {
		t.Fatalf("sanitized runtime result = %#v", result)
	}
	if result.ResultGuard.Status != agentcontrol.GuardRejected {
		t.Fatalf("failed Runtime Agent result Guard = %#v", result.ResultGuard)
	}
	if result.Decision.Action != "" || result.Decision.Reason != "" || len(result.Decision.Evidence) != 0 {
		t.Fatalf("failed Runtime Agent proposal must not be decoded: %#v", result.Decision)
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal sanitized runtime result: %v", err)
	}
	if strings.Contains(string(encoded), sensitiveMessage) || strings.Contains(string(encoded), sensitiveProposal) {
		t.Fatalf("sanitized runtime result leaked runtime content: %s", encoded)
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

func validRuntimeOperationExecution(t *testing.T, agentName string, runID string) AgentExecutionResult {
	t.Helper()
	decision := agentcontrol.ScalingDecision{
		Action:          agentcontrol.ScalingActionScaleOut,
		Reason:          "Trusted SLO evidence requires one bounded replica increase.",
		CurrentReplicas: 1,
		DesiredReplicas: 2,
		Evidence:        []string{"latency_p95_ms"},
		CreatedAt:       "2026-08-05T08:00:00Z",
	}
	return AgentExecutionResult{
		RunID: runID, Agent: agentName, Status: "completed",
		Proposal: AgentProposal{
			Action:     agentcontrol.OperationOptimizationDecisionAction,
			Parameters: mustAnyMap(t, decision),
		},
		DomainValidation: "scaling_decision",
	}
}
