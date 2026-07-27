package api

import (
	"net/http"
	"net/http/httptest"
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

func TestSubmissionOpenAPIIncludesControlRunWorkflow(t *testing.T) {
	config := NewServerConfig()
	content, err := os.ReadFile(config.OpenAPIPath)
	if err != nil {
		t.Fatalf("read submission OpenAPI: %v", err)
	}
	for _, expected := range []string{
		"/api/v1/control-runs:",
		"/api/v1/control-runs/{run_id}:",
		"/api/v1/control-runs/{run_id}/submit:",
		"CreateControlRun",
		"SubmitControlRun",
	} {
		if !strings.Contains(string(content), expected) {
			t.Fatalf("submission OpenAPI is missing %q", expected)
		}
	}
}

func TestSubmissionOpenAPIIncludesGuardedAgentExecution(t *testing.T) {
	config := NewServerConfig()
	content, err := os.ReadFile(config.OpenAPIPath)
	if err != nil {
		t.Fatalf("read submission OpenAPI: %v", err)
	}
	for _, expected := range []string{
		"/api/v1/agents/{name}/execute:",
		"operationId: ExecuteAgent",
		"AgentExecutionRequest:",
		"AgentExecutionResponse:",
	} {
		if !strings.Contains(string(content), expected) {
			t.Fatalf("submission OpenAPI is missing %q", expected)
		}
	}
}

func TestServedOpenAPIIncludesAgentExecutionDiagnosticResponses(t *testing.T) {
	server := NewServer(NewServerConfig())
	request := httptest.NewRequest(http.MethodGet, "/openapi.yaml", nil)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("OpenAPI status=%d body=%s", response.Code, response.Body.String())
	}
	var document map[string]any
	if err := yaml.Unmarshal(response.Body.Bytes(), &document); err != nil {
		t.Fatalf("parse served OpenAPI: %v", err)
	}
	paths := document["paths"].(map[string]any)
	execute := paths["/api/v1/agents/{name}/execute"].(map[string]any)["post"].(map[string]any)
	responses := execute["responses"].(map[string]any)
	for _, status := range []string{"403", "422"} {
		response := responses[status].(map[string]any)
		content := response["content"].(map[string]any)
		schema := content["application/json"].(map[string]any)["schema"].(map[string]any)
		if schema["$ref"] != "#/components/schemas/AgentExecutionErrorResponse" {
			t.Fatalf("%s response schema=%#v", status, schema)
		}
	}
	properties := document["components"].(map[string]any)["schemas"].(map[string]any)["AgentExecutionErrorResponse"].(map[string]any)["properties"].(map[string]any)
	for _, name := range []string{"run_id", "message", "reason", "request_guard", "result_guard"} {
		if _, ok := properties[name]; !ok {
			t.Fatalf("diagnostic response is missing %q: %#v", name, properties)
		}
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

func TestGeneratedSwaggerIncludesGuardedAgentExecution(t *testing.T) {
	config := NewServerConfig()
	for _, path := range []string{
		config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
		config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
	} {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read generated Swagger %s: %v", path, err)
		}
		for _, expected := range []string{
			"ExecuteAgent",
			"api.AgentExecutionRequest",
			"api.AgentExecutionResponse",
		} {
			if !strings.Contains(string(content), expected) {
				t.Fatalf("generated Swagger %s is missing %s", path, expected)
			}
		}
	}
}

func TestGeneratedSwaggerIncludesControlRunOperations(t *testing.T) {
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
			"CreateControlRun",
			"ListControlRuns",
			"GetControlRun",
			"SubmitControlRun",
			"DeleteControlRun",
			"DeleteControlRuns",
		} {
			if !strings.Contains(string(content), operationID) {
				t.Fatalf("generated Swagger %s is missing %s", path, operationID)
			}
		}
	}
}
