package api

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestAgentControlRegistryAuthorizerRejectsMissingCapability(t *testing.T) {
	repoRoot := t.TempDir()
	configDirectory := filepath.Join(repoRoot, "config")
	if err := os.MkdirAll(configDirectory, 0o755); err != nil {
		t.Fatalf("create config directory: %v", err)
	}
	registry := `{
		"version":"1",
		"agents":[{
			"name":"AIApplicationAutomationAgent",
			"role":"test",
			"bounded_actions":["generate_deployment_decision"],
			"capabilities":["unrelated_capability"],
			"enabled":true
		}]
	}`
	if err := os.WriteFile(
		filepath.Join(configDirectory, "agent_registry.json"),
		[]byte(registry),
		0o600,
	); err != nil {
		t.Fatalf("write Agent Registry: %v", err)
	}

	authorization, err := newAgentControlRegistryAuthorizer(ServerConfig{RepoRoot: repoRoot}).
		Authorize(context.Background(), agentcontrol.AgentAuthorizationRequest{
			AgentName:  agentcontrol.AutomationAgentName,
			Capability: agentcontrol.AutomationCapability,
			Action:     agentcontrol.AutomationDecisionAction,
		})
	if err != nil {
		t.Fatalf("authorize: %v", err)
	}
	if authorization.Authorized {
		t.Fatalf("authorization = %#v, want rejected", authorization)
	}
	if authorization.Reason == "" {
		t.Fatal("rejected authorization must include a reason")
	}
}
