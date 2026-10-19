package profileapi

import (
	"os"
	"strings"
	"testing"
)

func TestOpenAPIContainsOnlyFocusedLLMOpPaths(t *testing.T) {
	content, err := os.ReadFile("../../docs/swagger/swagger.yaml")
	if err != nil {
		t.Fatalf("read generated Swagger: %v", err)
	}
	document := string(content)
	for _, path := range []string{
		"/api/v1/application-profiles:",
		"/api/v1/ops-llm/select:",
		"/healthz:",
		"/openapi.yaml:",
	} {
		if !strings.Contains(document, path) {
			t.Fatalf("generated Swagger is missing %q", path)
		}
	}
	for _, forbidden := range []string{
		"/api/v1/agents",
		"/api/v1/agent-control",
		"/api/v1/planner",
		"/api/v1/control-runs",
		"/api/v1/autonomy",
		"DeploymentManifest",
		"DesiredDeploymentSpec",
	} {
		if strings.Contains(document, forbidden) {
			t.Fatalf("generated Swagger contains out-of-scope contract %q", forbidden)
		}
	}
}
