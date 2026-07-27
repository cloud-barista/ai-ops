package api

import "testing"

func TestResolvePlannerAgentSelectsEnabledAuthorizedConfigurationAgent(t *testing.T) {
	registry := AgentRegistry{Agents: []AgentProfile{
		plannerAgent("DisabledPlanner", false, agentSourceConfiguration),
		plannerAgent("ManifestPlanner", true, agentSourceConfiguration),
	}}

	selection, err := resolvePlannerAgent(registry, "", actionGenerateDeploymentManifest)
	if err != nil {
		t.Fatalf("resolve planner Agent: %v", err)
	}
	if selection.Name != "ManifestPlanner" ||
		selection.Capability != capabilityDeploymentManifestPlanning ||
		selection.Action != actionGenerateDeploymentManifest ||
		selection.Source != agentSourceConfiguration {
		t.Fatalf("unexpected selection: %#v", selection)
	}
}

func TestResolvePlannerAgentRejectsDisabledExplicitAgent(t *testing.T) {
	registry := AgentRegistry{Agents: []AgentProfile{plannerAgent("ManifestPlanner", false, agentSourceConfiguration)}}
	if _, err := resolvePlannerAgent(registry, "ManifestPlanner", actionGenerateDeploymentManifest); err == nil {
		t.Fatal("expected disabled Agent to be rejected")
	}
}

func TestResolvePlannerAgentRejectsMissingCapability(t *testing.T) {
	agent := plannerAgent("ManifestPlanner", true, agentSourceConfiguration)
	agent.Capabilities = []string{"unrelated_capability"}
	registry := AgentRegistry{Agents: []AgentProfile{agent}}

	if _, err := resolvePlannerAgent(registry, "ManifestPlanner", actionGenerateDeploymentManifest); err == nil {
		t.Fatal("expected missing capability to be rejected")
	}
}

func TestResolvePlannerAgentRejectsUnboundedAction(t *testing.T) {
	agent := plannerAgent("ManifestPlanner", true, agentSourceConfiguration)
	agent.BoundedActions = []string{"observe_status"}
	registry := AgentRegistry{Agents: []AgentProfile{agent}}

	if _, err := resolvePlannerAgent(registry, "ManifestPlanner", actionGenerateDeploymentManifest); err == nil {
		t.Fatal("expected unbounded action to be rejected")
	}
}

func TestResolvePlannerAgentDoesNotSelectRuntimeEndpointAgent(t *testing.T) {
	runtimeAgent := plannerAgent("ExternalPlanner", true, agentSourceRuntime)
	runtimeAgent.Endpoint = "https://agent.example.test"
	runtimeAgent.InvocationPath = "/invoke"
	registry := AgentRegistry{Agents: []AgentProfile{runtimeAgent}}

	if _, err := resolvePlannerAgent(registry, "", actionGenerateDeploymentManifest); err == nil {
		t.Fatal("expected runtime endpoint Agent to be excluded from internal Planner resolution")
	}
}

func TestResolvePlannerAgentHonorsExplicitAgentName(t *testing.T) {
	registry := AgentRegistry{Agents: []AgentProfile{
		plannerAgent("FirstPlanner", true, agentSourceConfiguration),
		plannerAgent("RequestedPlanner", true, agentSourceConfiguration),
	}}

	selection, err := resolvePlannerAgent(registry, "RequestedPlanner", actionSubmitDeploymentManifest)
	if err != nil {
		t.Fatalf("resolve explicit Planner Agent: %v", err)
	}
	if selection.Name != "RequestedPlanner" || selection.Action != actionSubmitDeploymentManifest {
		t.Fatalf("unexpected explicit selection: %#v", selection)
	}
}

func plannerAgent(name string, enabled bool, source string) AgentProfile {
	return AgentProfile{
		Name:           name,
		Enabled:        enabled,
		Source:         source,
		Capabilities:   []string{capabilityDeploymentManifestPlanning},
		BoundedActions: []string{actionGenerateDeploymentManifest, actionSubmitDeploymentManifest},
	}
}
