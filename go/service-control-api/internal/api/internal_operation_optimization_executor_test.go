package api

import (
	"context"
	"errors"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestInternalOperationOptimizationExecutorReturnsBoundedDecision(t *testing.T) {
	executor := newInternalOperationOptimizationExecutor()
	result, err := executor.Execute(
		context.Background(),
		operationAgent(agentcontrol.OperationOptimizationAgentName, true),
		operationDispatchRequest(t, operationReadyFlow()),
	)
	if err != nil {
		t.Fatalf("execute Internal operation Agent: %v", err)
	}
	if result.Status != "completed" || result.DomainValidation != "scaling_decision" {
		t.Fatalf("execution result = %#v", result)
	}
	if result.Proposal.Action != agentcontrol.OperationOptimizationDecisionAction {
		t.Fatalf("proposal action = %q", result.Proposal.Action)
	}
	if result.Proposal.Parameters["action"] != agentcontrol.ScalingActionScaleOut ||
		result.Proposal.Parameters["current_replicas"] != float64(1) ||
		result.Proposal.Parameters["desired_replicas"] != float64(2) {
		t.Fatalf("proposal parameters = %#v", result.Proposal.Parameters)
	}
}

func TestInternalOperationOptimizationExecutorRejectsMalformedStatus(t *testing.T) {
	flow := operationReadyFlow()
	flow.DeploymentStatus.Data.DeploymentStatus.State = "UNKNOWN"

	_, err := newInternalOperationOptimizationExecutor().Execute(
		context.Background(),
		operationAgent(agentcontrol.OperationOptimizationAgentName, true),
		operationDispatchRequest(t, flow),
	)
	if err == nil {
		t.Fatal("expected malformed deployment status to be rejected")
	}
}

func TestInternalOperationOptimizationExecutorRejectsMissingFeedback(t *testing.T) {
	flow := operationReadyFlow()
	flow.OptimizationFeedback = nil

	_, err := newInternalOperationOptimizationExecutor().Execute(
		context.Background(),
		operationAgent(agentcontrol.OperationOptimizationAgentName, true),
		operationDispatchRequest(t, flow),
	)
	if err == nil {
		t.Fatal("expected missing feedback to be rejected")
	}
}

func TestInternalOperationOptimizationExecutorHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := newInternalOperationOptimizationExecutor().Execute(
		ctx,
		operationAgent(agentcontrol.OperationOptimizationAgentName, true),
		operationDispatchRequest(t, operationReadyFlow()),
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context cancellation", err)
	}
}

func operationDispatchRequest(t *testing.T, flow agentcontrol.Flow) AgentDispatchRequest {
	t.Helper()
	return AgentDispatchRequest{
		RunID:      "run-operation-001",
		Agent:      agentcontrol.OperationOptimizationAgentName,
		Capability: agentcontrol.OperationOptimizationCapability,
		Action:     agentcontrol.OperationOptimizationDecisionAction,
		Input: map[string]any{
			"flow": mustAnyMap(t, flow),
		},
		Context: map[string]any{
			"correlation_id": "flow-operation-001",
			"trace_id":       "trace-operation-001",
		},
	}
}

func operationReadyFlow() agentcontrol.Flow {
	return agentcontrol.Flow{
		ApplicationContext: &agentcontrol.ApplicationContextEnvelope{
			Data: agentcontrol.ApplicationContextData{
				ApplicationProfile: agentcontrol.ApplicationProfile{
					Requirements: agentcontrol.ApplicationRequirements{
						Deployment: agentcontrol.DeploymentRequirements{ReplicasMin: 1, ReplicasMax: 2},
						SLO:        agentcontrol.SLORequirements{LatencyP95MSMax: 100},
					},
				},
			},
		},
		DeploymentPlan: &agentcontrol.DeploymentPlan{
			InferenceConfiguration: agentcontrol.InferenceConfiguration{Replicas: 1},
		},
		DeploymentStatus: &agentcontrol.DeploymentStatusEnvelope{
			Data: agentcontrol.DeploymentStatusData{
				DeploymentStatus: agentcontrol.DeploymentStatus{State: agentcontrol.DeploymentStateRunning},
			},
		},
		OptimizationFeedback: &agentcontrol.OptimizationFeedbackEnvelope{
			Data: agentcontrol.OptimizationFeedbackData{
				OptimizationFeedback: agentcontrol.OptimizationFeedback{
					SLOViolations: []string{"latency_p95_ms"},
					Metrics: agentcontrol.OptimizationMetrics{
						Inference: agentcontrol.InferenceMetrics{LatencyP95MS: 120},
					},
				},
			},
		},
	}
}
