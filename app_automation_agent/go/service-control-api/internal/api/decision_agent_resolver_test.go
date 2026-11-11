package api

import (
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestResolveDecisionAgentUsesRegistryDefault(t *testing.T) {
	registry := AgentRegistry{
		Defaults: map[string]string{
			agentcontrol.AutomationCapability: "AIApplicationAutomationAgent",
		},
		Agents: []AgentProfile{
			decisionAgent("AIApplicationAutomationAgent", true, agentSourceConfiguration),
		},
	}

	selected, err := resolveDecisionAgent(registry, nil, "")
	if err != nil {
		t.Fatalf("resolve default decision Agent: %v", err)
	}
	if selected.Name != "AIApplicationAutomationAgent" || selected.Source != agentSourceConfiguration {
		t.Fatalf("unexpected default Agent: %#v", selected)
	}
}

func TestResolveDecisionAgentSelectsEligibleRuntimeAgent(t *testing.T) {
	registry := AgentRegistry{Defaults: map[string]string{
		agentcontrol.AutomationCapability: "AIApplicationAutomationAgent",
	}}
	runtimeAgent := decisionAgent("RuntimeDeploymentAgent", true, agentSourceRuntime)
	runtimeAgent.Endpoint = "https://agent.example.test"
	runtimeAgent.InvocationPath = "/v1/decide"

	selected, err := resolveDecisionAgent(registry, []AgentProfile{runtimeAgent}, runtimeAgent.Name)
	if err != nil {
		t.Fatalf("resolve Runtime decision Agent: %v", err)
	}
	if selected.Name != runtimeAgent.Name || selected.Source != agentSourceRuntime {
		t.Fatalf("unexpected Runtime Agent: %#v", selected)
	}
}

func TestEligibleDecisionAgentsExcludesUnderAuthorizedAgents(t *testing.T) {
	missingCapability := decisionAgent("MissingCapabilityAgent", true, agentSourceRuntime)
	missingCapability.Capabilities = []string{"research_evaluation"}
	disabled := decisionAgent("DisabledDecisionAgent", false, agentSourceRuntime)
	eligible := decisionAgent("EligibleDecisionAgent", true, agentSourceRuntime)

	result := eligibleDecisionAgents(AgentRegistry{}, []AgentProfile{
		missingCapability,
		disabled,
		eligible,
	})
	if len(result) != 1 || result[0].Name != eligible.Name {
		t.Fatalf("eligible decision Agents = %#v", result)
	}
}

func TestResolveDecisionAgentRejectsMissingDefault(t *testing.T) {
	registry := AgentRegistry{Agents: []AgentProfile{
		decisionAgent("AIApplicationAutomationAgent", true, agentSourceConfiguration),
	}}

	if _, err := resolveDecisionAgent(registry, nil, ""); err == nil {
		t.Fatal("expected missing Registry default to be rejected")
	}
}

func TestResolveDecisionAgentRejectsExplicitIneligibleAgent(t *testing.T) {
	registry := AgentRegistry{}
	ineligible := decisionAgent("ResearchRuntimeAgent", true, agentSourceRuntime)
	ineligible.BoundedActions = []string{"evaluate_result"}

	if _, err := resolveDecisionAgent(registry, []AgentProfile{ineligible}, ineligible.Name); err == nil {
		t.Fatal("expected under-authorized Runtime Agent to be rejected")
	}
}

func decisionAgent(name string, enabled bool, source string) AgentProfile {
	return AgentProfile{
		Name:           name,
		Enabled:        enabled,
		Source:         source,
		Capabilities:   []string{agentcontrol.AutomationCapability},
		BoundedActions: []string{agentcontrol.AutomationDecisionAction},
	}
}
