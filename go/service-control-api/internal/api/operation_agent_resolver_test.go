package api

import (
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestEligibleOperationAgentsRequiresCapabilityAndAction(t *testing.T) {
	agents := eligibleOperationAgents(AgentRegistry{}, []AgentProfile{
		{Name: "allowed", Source: agentSourceRuntime, Enabled: true,
			Capabilities:   []string{agentcontrol.OperationOptimizationCapability},
			BoundedActions: []string{agentcontrol.OperationOptimizationDecisionAction}},
		{Name: "wrong-action", Source: agentSourceRuntime, Enabled: true,
			Capabilities:   []string{agentcontrol.OperationOptimizationCapability},
			BoundedActions: []string{"observe_status"}},
	})
	if len(agents) != 1 || agents[0].Name != "allowed" {
		t.Fatalf("eligible operation Agents = %#v", agents)
	}
}

func TestResolveOperationAgentUsesRegistryDefault(t *testing.T) {
	registry := AgentRegistry{
		Defaults: map[string]string{
			agentcontrol.OperationOptimizationCapability: agentcontrol.OperationOptimizationAgentName,
		},
		Agents: []AgentProfile{operationAgent(agentcontrol.OperationOptimizationAgentName, true)},
	}

	selected, err := resolveOperationAgent(registry, nil, "")
	if err != nil {
		t.Fatalf("resolve default operation Agent: %v", err)
	}
	if selected.Name != agentcontrol.OperationOptimizationAgentName {
		t.Fatalf("unexpected default operation Agent: %#v", selected)
	}
}

func TestResolveOperationAgentRejectsDisabledAgent(t *testing.T) {
	agent := operationAgent("DisabledOperationAgent", false)

	if _, err := resolveOperationAgent(AgentRegistry{}, []AgentProfile{agent}, agent.Name); err == nil {
		t.Fatal("expected disabled operation Agent to be rejected")
	}
}

func TestResolveOperationAgentRejectsMissingCapability(t *testing.T) {
	agent := operationAgent("MissingCapabilityOperationAgent", true)
	agent.Capabilities = []string{"deployment_review"}

	if _, err := resolveOperationAgent(AgentRegistry{}, []AgentProfile{agent}, agent.Name); err == nil {
		t.Fatal("expected operation Agent without the capability to be rejected")
	}
}

func TestResolveOperationAgentRejectsMissingAction(t *testing.T) {
	agent := operationAgent("MissingActionOperationAgent", true)
	agent.BoundedActions = []string{"observe_status"}

	if _, err := resolveOperationAgent(AgentRegistry{}, []AgentProfile{agent}, agent.Name); err == nil {
		t.Fatal("expected operation Agent without the action to be rejected")
	}
}

func operationAgent(name string, enabled bool) AgentProfile {
	return AgentProfile{
		Name:           name,
		Enabled:        enabled,
		Source:         agentSourceConfiguration,
		Capabilities:   []string{agentcontrol.OperationOptimizationCapability},
		BoundedActions: []string{agentcontrol.OperationOptimizationDecisionAction},
	}
}
