package api

import (
	"os"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

func TestSubmissionOpenAPIIsValidYAML(t *testing.T) {
	config := NewServerConfig()
	content, err := os.ReadFile(config.OpenAPIPath)
	if err != nil {
		t.Fatalf("read submission OpenAPI: %v", err)
	}
	var document map[string]any
	if err := yaml.Unmarshal(content, &document); err != nil {
		t.Fatalf("parse submission OpenAPI: %v", err)
	}
	if document["openapi"] != "3.0.3" {
		t.Fatalf("unexpected OpenAPI version: %#v", document["openapi"])
	}
}

func TestGeneratedSwaggerContainsDeletionOperations(t *testing.T) {
	config := NewServerConfig()
	for _, path := range []string{
		config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
		config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
	} {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read generated Swagger %s: %v", path, err)
		}
		for _, operationID := range []string{
			"DeleteAgent",
			"DeleteAutonomyEvent",
			"DeleteAutonomyEvents",
			"GetAutomationFeedback",
			"DeleteAutomationFeedback",
			"DeleteAllAutomationFeedback",
		} {
			if !strings.Contains(string(content), operationID) {
				t.Fatalf("generated Swagger %s is missing %s", path, operationID)
			}
		}
	}
}
