package api

import (
	"os"
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
