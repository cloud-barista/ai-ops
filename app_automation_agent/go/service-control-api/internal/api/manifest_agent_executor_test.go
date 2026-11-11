package api

import (
	"context"
	"testing"
)

func TestManifestAgentExecutorGeneratesTypedDeploymentManifest(t *testing.T) {
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}
	executor := newManifestAgentExecutor(
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		generator,
	)
	agent := AgentProfile{
		Name:   "AIApplicationAutomationAgent",
		Source: agentSourceConfiguration,
	}
	result, err := executor.Execute(context.Background(), agent, AgentDispatchRequest{
		RunID:      "run-manifest-001",
		Agent:      agent.Name,
		Capability: capabilityDeploymentManifestPlanning,
		Action:     actionGenerateDeploymentManifest,
		Input: map[string]any{
			"natural_language_request": "Deploy a GPU inference application.",
			"app_version_id":           "appver-001",
			"candidate_id":             "decision-model",
			"requested_by":             "ai-ops-geon-planner",
		},
	})
	if err != nil {
		t.Fatalf("execute Manifest Agent: %v", err)
	}
	if generator.calls != 1 || result.Status != "completed" {
		t.Fatalf("Manifest Generator was not executed: calls=%d result=%#v", generator.calls, result)
	}
	if result.Manifest == nil || result.Manifest.Spec.AppVersionID != "appver-001" {
		t.Fatalf("typed DeploymentManifest was not returned: %#v", result.Manifest)
	}
	if result.Generation == nil || result.Proposal.Action != actionGenerateDeploymentManifest {
		t.Fatalf("generation evidence is incomplete: %#v", result)
	}
}

func TestResolvePlannerAgentRejectsAmbiguousEligibleAgents(t *testing.T) {
	registry := AgentRegistry{Agents: []AgentProfile{
		{
			Name:           "FirstManifestAgent",
			Enabled:        true,
			Capabilities:   []string{capabilityDeploymentManifestPlanning},
			BoundedActions: []string{actionGenerateDeploymentManifest},
		},
		{
			Name:           "SecondManifestAgent",
			Enabled:        true,
			Capabilities:   []string{capabilityDeploymentManifestPlanning},
			BoundedActions: []string{actionGenerateDeploymentManifest},
		},
	}}

	if _, err := resolvePlannerAgent(registry, "", actionGenerateDeploymentManifest); err == nil {
		t.Fatal("expected ambiguous eligible Planner Agents to be rejected")
	}
}
