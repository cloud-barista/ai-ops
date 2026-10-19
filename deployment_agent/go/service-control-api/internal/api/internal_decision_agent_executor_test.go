package api

import (
	"context"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestInternalDecisionAgentExecutorReturnsCommonDecisionContract(t *testing.T) {
	profile, recommendation := decisionRuntimeFixtures()
	executor := newInternalDecisionAgentExecutor()
	request := decisionDispatchRequest(t, "run-internal-001", "AIApplicationAutomationAgent", profile, recommendation)

	result, err := executor.Execute(
		context.Background(),
		decisionAgent("AIApplicationAutomationAgent", true, agentSourceConfiguration),
		request,
	)
	if err != nil {
		t.Fatalf("execute Internal decision Agent: %v", err)
	}
	if result.Status != "completed" || result.DomainValidation != "deployment_decision" {
		t.Fatalf("execution result = %#v", result)
	}
	if result.Proposal.Action != agentcontrol.AutomationDecisionAction {
		t.Fatalf("proposal action = %q", result.Proposal.Action)
	}
	if result.Proposal.Parameters["decision"] != agentcontrol.ActionDeploy {
		t.Fatalf("proposal parameters = %#v", result.Proposal.Parameters)
	}
}

func TestAgentExecutionResultAllowsDeploymentDecisionDomain(t *testing.T) {
	agent := decisionAgent("RuntimeDeploymentAgent", true, agentSourceRuntime)
	request := AgentDispatchRequest{
		RunID:      "run-guard-001",
		Agent:      agent.Name,
		Capability: agentcontrol.AutomationCapability,
		Action:     agentcontrol.AutomationDecisionAction,
	}
	result := AgentExecutionResult{
		RunID:  request.RunID,
		Agent:  agent.Name,
		Status: "completed",
		Proposal: AgentProposal{
			Action: request.Action,
			Parameters: map[string]any{
				"decision":              agentcontrol.ActionDeploy,
				"selected_candidate_id": "candidate-001",
				"reason":                "candidate is feasible",
				"confidence":            0.9,
			},
		},
		DomainValidation: "deployment_decision",
	}

	guard := validateAgentExecutionResult(agent, request, result)
	if !guard.Valid {
		t.Fatalf("deployment decision result was rejected: %#v", guard)
	}
}

func decisionDispatchRequest(
	t *testing.T,
	runID string,
	agentName string,
	profile agentcontrol.ApplicationProfile,
	recommendation agentcontrol.ResourceRecommendation,
) AgentDispatchRequest {
	t.Helper()
	return AgentDispatchRequest{
		RunID:      runID,
		Agent:      agentName,
		Capability: agentcontrol.AutomationCapability,
		Action:     agentcontrol.AutomationDecisionAction,
		Input: map[string]any{
			"application_profile":     mustAnyMap(t, profile),
			"resource_recommendation": mustAnyMap(t, recommendation),
		},
		Context: map[string]any{
			"correlation_id": "flow-001",
			"trace_id":       "trace-001",
		},
	}
}
