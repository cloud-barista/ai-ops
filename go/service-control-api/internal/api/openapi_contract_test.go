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

func TestSubmissionOpenAPIIncludesAgentControlInputJoinWorkflow(t *testing.T) {
	config := NewServerConfig()
	content, err := os.ReadFile(config.OpenAPIPath)
	if err != nil {
		t.Fatalf("read submission OpenAPI: %v", err)
	}
	for _, expected := range []string{
		"/api/v1/agent-control/application-contexts:",
		"/api/v1/agent-control/resource-recommendations:",
		"/api/v1/agent-control/deployment-status:",
		"/api/v1/agent-control/optimization-feedback:",
		"/api/v1/agent-control/flows:",
		"/api/v1/agent-control/flows/{correlation_id}:",
		"/api/v1/agent-control/flows/{correlation_id}/reasoning-comparisons:",
		"ApplicationContextEnvelope:",
		"ResourceRecommendationEnvelope:",
		"DeploymentStatusEnvelope:",
		"OptimizationFeedbackEnvelope:",
		"AgentControlFlow:",
		"AgentAuthorization:",
		"AGENT_AUTHORIZATION_REJECTED",
		"ai_application_automation",
		"generate_deployment_decision",
		"AutomationDecision:",
		"AgentControlDeploymentPlan:",
		"AgentControlGuardResult:",
		"DeploymentCreateRequestEnvelope:",
		"AgentControlDeploymentManifest:",
		"ReasoningComparison:",
	} {
		if !strings.Contains(string(content), expected) {
			t.Fatalf("submission OpenAPI is missing %q", expected)
		}
	}
}

func TestOpenAPIDocumentsAutomaticThreeStageAgentFlow(t *testing.T) {
	config := NewServerConfig()
	documents := []string{
		config.OpenAPIPath,
		config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
		config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
	}
	for _, path := range documents {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read OpenAPI %s: %v", path, err)
		}
		for _, expected := range []string{
			"/api/v1/agent-control/application-analysis-requests",
			"/api/v1/agent-control/trusted-automation-runs",
			"/api/v1/agent-control/automation-runs/{run_id}",
			"TrustedAutomationRunRequest",
			"ApplicationAnalysisRequestEnvelope",
			"AutomationRun",
			"AutomationRunInput",
			"RequirementAnalysisResult",
			"RecommendationResult",
			"DesiredDeploymentSpec",
			"DeploymentSubmission",
			"deployment_submission",
		} {
			if !strings.Contains(string(content), expected) {
				t.Fatalf("OpenAPI %s is missing %q", path, expected)
			}
		}
	}
}

func TestSubmissionOpenAPIExposesOnlyTheSafeguardedAutomationPOST(t *testing.T) {
	config := NewServerConfig()
	content, err := os.ReadFile(config.OpenAPIPath)
	if err != nil {
		t.Fatalf("read submission OpenAPI: %v", err)
	}
	var document map[string]any
	if err := yaml.Unmarshal(content, &document); err != nil {
		t.Fatalf("parse submission OpenAPI: %v", err)
	}
	paths, ok := document["paths"].(map[string]any)
	if !ok {
		t.Fatalf("OpenAPI paths = %#v", document["paths"])
	}
	trusted, ok := paths["/api/v1/agent-control/trusted-automation-runs"].(map[string]any)
	if !ok || trusted["post"] == nil {
		t.Fatalf("trusted automation POST is missing: %#v", trusted)
	}
	legacy, ok := paths["/api/v1/agent-control/automation-runs"].(map[string]any)
	if ok && legacy["post"] != nil {
		t.Fatalf("unsafe legacy automation POST is still documented: %#v", legacy)
	}
}

func TestOpenAPIDocumentsTrustedAutomationAuditAndSafeErrors(t *testing.T) {
	type documentExpectation struct {
		path           string
		swagger2       bool
		responseSchema string
		auditSchema    string
		errorSchema    string
	}
	config := NewServerConfig()
	documents := []documentExpectation{
		{
			path:           config.OpenAPIPath,
			responseSchema: "TrustedAutomationRunResult",
			auditSchema:    "ExecutionAuditReference",
			errorSchema:    "TrustedAutomationRunErrorResponse",
		},
		{
			path:           config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
			swagger2:       true,
			responseSchema: "api.TrustedAutomationRunResponse",
			auditSchema:    "audittrail.Reference",
			errorSchema:    "api.TrustedAutomationRunErrorResponse",
		},
		{
			path:           config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
			swagger2:       true,
			responseSchema: "api.TrustedAutomationRunResponse",
			auditSchema:    "audittrail.Reference",
			errorSchema:    "api.TrustedAutomationRunErrorResponse",
		},
	}

	for _, document := range documents {
		content, err := os.ReadFile(document.path)
		if err != nil {
			t.Fatalf("read OpenAPI %s: %v", document.path, err)
		}
		var parsed map[string]any
		if err := yaml.Unmarshal(content, &parsed); err != nil {
			t.Fatalf("parse OpenAPI %s: %v", document.path, err)
		}

		paths := requireOpenAPIObject(t, parsed, "paths", document.path)
		trusted := requireOpenAPIObject(
			t,
			paths,
			"/api/v1/agent-control/trusted-automation-runs",
			document.path,
		)
		post := requireOpenAPIObject(t, trusted, "post", document.path)
		responses := requireOpenAPIObject(t, post, "responses", document.path)

		responseRef := openAPISchemaRef(document.swagger2, document.responseSchema)
		errorRef := openAPISchemaRef(document.swagger2, document.errorSchema)
		for _, status := range []string{"200", "201"} {
			if actual := openAPIResponseSchemaRef(t, responses, status, document.swagger2, document.path); actual != responseRef {
				t.Fatalf("OpenAPI %s trusted response %s ref=%q, want %q", document.path, status, actual, responseRef)
			}
		}
		for _, status := range []string{"400", "500"} {
			if actual := openAPIResponseSchemaRef(t, responses, status, document.swagger2, document.path); actual != errorRef {
				t.Fatalf("OpenAPI %s trusted error %s ref=%q, want %q", document.path, status, actual, errorRef)
			}
		}

		schemas := openAPISchemas(t, parsed, document.swagger2, document.path)
		response := requireOpenAPIObject(t, schemas, document.responseSchema, document.path)
		responseProperties := requireOpenAPIObject(t, response, "properties", document.path)
		status := requireOpenAPIObject(t, responseProperties, "status", document.path)
		statusValues, ok := status["enum"].([]any)
		if !ok || !containsOpenAPIEnum(statusValues, "GEON_REJECTED") {
			t.Fatalf("OpenAPI %s trusted status enum=%#v, missing GEON_REJECTED", document.path, status["enum"])
		}
		audit := requireOpenAPIObject(t, responseProperties, "audit", document.path)
		if reference, _ := audit["$ref"].(string); reference != openAPISchemaRef(document.swagger2, document.auditSchema) {
			t.Fatalf("OpenAPI %s audit ref=%q", document.path, reference)
		}

		auditSchema := requireOpenAPIObject(t, schemas, document.auditSchema, document.path)
		auditProperties := requireOpenAPIObject(t, auditSchema, "properties", document.path)
		for _, field := range []string{
			"schema_version",
			"audit_id",
			"persistence_status",
			"complete",
			"event_count",
			"relative_directory",
			"events_path",
			"summary_path",
			"last_event_sha256",
			"summary_sha256",
		} {
			if _, ok := auditProperties[field]; !ok {
				t.Fatalf("OpenAPI %s audit reference is missing %q", document.path, field)
			}
		}
		persistence := requireOpenAPIObject(t, auditProperties, "persistence_status", document.path)
		persistenceValues, ok := persistence["enum"].([]any)
		if !ok {
			t.Fatalf("OpenAPI %s persistence_status enum=%#v", document.path, persistence["enum"])
		}
		for _, expected := range []string{"RECORDING", "COMPLETE", "DEGRADED"} {
			if !containsOpenAPIEnum(persistenceValues, expected) {
				t.Fatalf("OpenAPI %s persistence_status enum missing %q: %#v", document.path, expected, persistenceValues)
			}
		}

		errorSchema := requireOpenAPIObject(t, schemas, document.errorSchema, document.path)
		errorProperties := requireOpenAPIObject(t, errorSchema, "properties", document.path)
		if _, unsafe := errorProperties["error"]; unsafe {
			t.Fatalf("OpenAPI %s trusted error exposes an unbounded error string", document.path)
		}
		for _, field := range []string{"message", "error_code", "result"} {
			if _, ok := errorProperties[field]; !ok {
				t.Fatalf("OpenAPI %s trusted error is missing %q", document.path, field)
			}
		}
		result := requireOpenAPIObject(t, errorProperties, "result", document.path)
		if reference, _ := result["$ref"].(string); reference != responseRef {
			t.Fatalf("OpenAPI %s trusted error result ref=%q, want %q", document.path, reference, responseRef)
		}
		errorCode := requireOpenAPIObject(t, errorProperties, "error_code", document.path)
		errorValues, ok := errorCode["enum"].([]any)
		if !ok || !containsOpenAPIEnum(errorValues, "AUDIT_PERSISTENCE_FAILED") {
			t.Fatalf("OpenAPI %s error_code enum=%#v, missing AUDIT_PERSISTENCE_FAILED", document.path, errorCode["enum"])
		}
	}
}

func openAPISchemaRef(swagger2 bool, schema string) string {
	if swagger2 {
		return "#/definitions/" + schema
	}
	return "#/components/schemas/" + schema
}

func openAPISchemas(
	t *testing.T,
	document map[string]any,
	swagger2 bool,
	path string,
) map[string]any {
	t.Helper()
	if swagger2 {
		return requireOpenAPIObject(t, document, "definitions", path)
	}
	components := requireOpenAPIObject(t, document, "components", path)
	return requireOpenAPIObject(t, components, "schemas", path)
}

func openAPIResponseSchemaRef(
	t *testing.T,
	responses map[string]any,
	status string,
	swagger2 bool,
	path string,
) string {
	t.Helper()
	response := requireOpenAPIObject(t, responses, status, path)
	if swagger2 {
		schema := requireOpenAPIObject(t, response, "schema", path)
		reference, _ := schema["$ref"].(string)
		return reference
	}
	content := requireOpenAPIObject(t, response, "content", path)
	mediaType := requireOpenAPIObject(t, content, "application/json", path)
	schema := requireOpenAPIObject(t, mediaType, "schema", path)
	reference, _ := schema["$ref"].(string)
	return reference
}

func requireOpenAPIObject(
	t *testing.T,
	parent map[string]any,
	key string,
	path string,
) map[string]any {
	t.Helper()
	value, ok := parent[key].(map[string]any)
	if !ok {
		t.Fatalf("OpenAPI %s field %q=%#v is not an object", path, key, parent[key])
	}
	return value
}

func TestOpenAPIDocumentsRegistrySelectedDecisionAgent(t *testing.T) {
	config := NewServerConfig()
	documents := []string{
		config.OpenAPIPath,
		config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
		config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
	}
	for _, path := range documents {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read OpenAPI %s: %v", path, err)
		}
		for _, expected := range []string{
			"decision_agent",
			"requested_decision_agent",
			"agent_execution",
			"AGENT_EXECUTION_FAILED",
			"AGENT_RESULT_REJECTED",
		} {
			if !strings.Contains(string(content), expected) {
				t.Fatalf("OpenAPI %s is missing selected-Agent contract %q", path, expected)
			}
		}
	}
}

func TestSubmissionOpenAPIIncludesAgentControlDeletionAndScalingDecision(t *testing.T) {
	config := NewServerConfig()
	content, err := os.ReadFile(config.OpenAPIPath)
	if err != nil {
		t.Fatalf("read submission OpenAPI: %v", err)
	}
	var document map[string]any
	if err := yaml.Unmarshal(content, &document); err != nil {
		t.Fatalf("parse submission OpenAPI: %v", err)
	}

	paths := document["paths"].(map[string]any)
	for _, path := range []string{
		"/api/v1/agent-control/flows",
		"/api/v1/agent-control/flows/{correlation_id}",
	} {
		operations := paths[path].(map[string]any)
		if _, ok := operations["delete"]; !ok {
			t.Fatalf("submission OpenAPI path %s is missing DELETE", path)
		}
	}
	optimizationFeedback := paths["/api/v1/agent-control/optimization-feedback"].(map[string]any)["post"].(map[string]any)
	parameters, ok := optimizationFeedback["parameters"].([]any)
	if !ok {
		t.Fatalf("optimization feedback does not document query parameters: %#v", optimizationFeedback)
	}
	for _, parameter := range parameters {
		item := parameter.(map[string]any)
		if item["name"] != "operation_agent" || item["in"] != "query" {
			continue
		}
		schema := item["schema"].(map[string]any)
		if schema["type"] != "string" || item["required"] == true {
			t.Fatalf("operation_agent query parameter=%#v", item)
		}
		goto operationAgentQueryDocumented
	}
	t.Fatal("optimization feedback is missing the optional operation_agent query parameter")

operationAgentQueryDocumented:

	schemas := document["components"].(map[string]any)["schemas"].(map[string]any)
	flowProperties := schemas["AgentControlFlow"].(map[string]any)["properties"].(map[string]any)
	scalingReference := flowProperties["scaling_decision"].(map[string]any)["$ref"]
	if scalingReference != "#/components/schemas/ScalingDecision" {
		t.Fatalf("scaling_decision schema reference=%#v", scalingReference)
	}
	for _, field := range []string{"requested_operation_agent", "operation_agent_execution"} {
		if _, ok := flowProperties[field]; !ok {
			t.Fatalf("AgentControlFlow is missing operation Agent evidence field %q", field)
		}
	}
	if reference := flowProperties["operation_agent_execution"].(map[string]any)["$ref"]; reference != "#/components/schemas/OperationOptimizationResult" {
		t.Fatalf("operation_agent_execution schema reference=%#v", reference)
	}
	operationExecution := schemas["OperationOptimizationResult"].(map[string]any)
	if required := operationExecution["required"].([]any); len(required) != 2 ||
		required[0] != "run_id" || required[1] != "status" {
		t.Fatalf("OperationOptimizationResult required fields=%#v, want run_id and status", required)
	}
	if _, ok := operationExecution["properties"].(map[string]any)["run_id"]; !ok {
		t.Fatalf("OperationOptimizationResult is missing run_id: %#v", operationExecution)
	}
	if description, _ := operationExecution["description"].(string); !strings.Contains(description, "Terminal failed or rejected") {
		t.Fatalf("OperationOptimizationResult does not document terminal evidence semantics: %#v", operationExecution)
	}

	scaling := schemas["ScalingDecision"].(map[string]any)
	properties := scaling["properties"].(map[string]any)
	action := properties["action"].(map[string]any)
	reason := properties["reason"].(map[string]any)
	if reason["minLength"] != 1 || reason["maxLength"] != 8000 {
		t.Fatalf("ScalingDecision reason bounds=%#v", reason)
	}
	actionValues := action["enum"].([]any)
	if description, _ := action["description"].(string); !strings.Contains(description, "NO_ACTION") {
		t.Fatalf("ScalingDecision action does not document legacy NO_ACTION compatibility: %#v", action)
	}
	for _, expected := range []string{"KEEP", "SCALE_OUT", "SCALE_IN"} {
		found := false
		for _, value := range actionValues {
			if value == expected {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("ScalingDecision action enum is missing %q: %#v", expected, actionValues)
		}
	}
}

func TestGeneratedSwaggerDocumentsCanonicalOperationScalingAction(t *testing.T) {
	config := NewServerConfig()
	for _, path := range []string{
		config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
		config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
	} {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read generated Swagger %s: %v", path, err)
		}
		var document map[string]any
		if err := yaml.Unmarshal(content, &document); err != nil {
			t.Fatalf("parse generated Swagger %s: %v", path, err)
		}
		definitions := document["definitions"].(map[string]any)
		operationProperties := definitions["agentcontrol.OperationOptimizationResult"].(map[string]any)["properties"].(map[string]any)
		if _, ok := operationProperties["run_id"]; !ok {
			t.Fatalf("generated Swagger %s operation result is missing run_id: %#v", path, operationProperties)
		}
		action := definitions["agentcontrol.ScalingDecision"].(map[string]any)["properties"].(map[string]any)["action"].(map[string]any)
		actionValues, ok := action["enum"].([]any)
		if !ok {
			t.Fatalf("generated Swagger %s action is missing its canonical enum: %#v", path, action)
		}
		for _, expected := range []string{"KEEP", "SCALE_OUT", "SCALE_IN"} {
			if !containsOpenAPIEnum(actionValues, expected) {
				t.Fatalf("generated Swagger %s action enum missing %q: %#v", path, expected, actionValues)
			}
		}
		description, _ := action["description"].(string)
		for _, expected := range []string{"NO_ACTION", "proposal/result", "normalized to KEEP"} {
			if !strings.Contains(description, expected) {
				t.Fatalf("generated Swagger %s action description missing %q: %#v", path, expected, action)
			}
		}
	}
}

func containsOpenAPIEnum(values []any, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
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
			"DeleteAgentControlFlow",
			"DeleteAgentControlFlows",
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
		for _, schema := range []string{
			"agentcontrol.ScalingDecision",
			"scaling_decision",
		} {
			if !strings.Contains(string(content), schema) {
				t.Fatalf("generated Swagger %s is missing %s", path, schema)
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

func TestGeneratedOpenAPIDocumentsPackageControlRunContract(t *testing.T) {
	type parameter struct {
		In       string `yaml:"in"`
		Name     string `yaml:"name"`
		Required bool   `yaml:"required"`
		Type     string `yaml:"type"`
	}
	type schema struct {
		Ref        string            `yaml:"$ref"`
		Type       string            `yaml:"type"`
		Items      *schema           `yaml:"items"`
		Properties map[string]schema `yaml:"properties"`
	}
	type response struct {
		Description string `yaml:"description"`
		Schema      schema `yaml:"schema"`
	}
	type operation struct {
		Consumes    []string            `yaml:"consumes"`
		OperationID string              `yaml:"operationId"`
		Parameters  []parameter         `yaml:"parameters"`
		Responses   map[string]response `yaml:"responses"`
	}
	type pathItem struct {
		Post *operation `yaml:"post"`
	}
	type swaggerDocument struct {
		Definitions map[string]schema   `yaml:"definitions"`
		Paths       map[string]pathItem `yaml:"paths"`
	}

	requiredFieldsTemplate := map[string]bool{
		"source":                   true,
		"package_type":             true,
		"app_name":                 true,
		"app_version":              true,
		"entrypoint":               true,
		"runtime_type":             true,
		"service_port":             false,
		"healthcheck_path":         false,
		"natural_language_request": true,
		"candidate_id":             true,
		"requested_by":             false,
		"agent_name":               false,
		"target_profile_id":        false,
		"cpu":                      true,
		"memory":                   true,
		"gpu":                      true,
		"storage":                  true,
		"cost_policy":              false,
	}

	config := NewServerConfig()
	for _, path := range []string{
		config.path("go", "service-control-api", "docs", "swagger", "swagger.json"),
		config.path("go", "service-control-api", "docs", "swagger", "swagger.yaml"),
	} {
		requiredFields := make(map[string]bool, len(requiredFieldsTemplate))
		for name, required := range requiredFieldsTemplate {
			requiredFields[name] = required
		}
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read generated Swagger %s: %v", path, err)
		}
		var document swaggerDocument
		if err := yaml.Unmarshal(content, &document); err != nil {
			t.Fatalf("parse generated Swagger %s: %v", path, err)
		}

		submit := document.Paths["/api/v1/control-runs/{run_id}/submit"].Post
		if submit == nil || submit.OperationID != "SubmitControlRun" {
			t.Fatalf("generated Swagger %s lost the existing submit endpoint", path)
		}

		fromPackage := document.Paths["/api/v1/control-runs/from-package"].Post
		if fromPackage == nil {
			t.Fatalf("generated Swagger %s is missing the package ControlRun endpoint", path)
		}
		if fromPackage.OperationID != "CreateControlRunFromPackage" {
			t.Fatalf("generated Swagger %s operationId=%q", path, fromPackage.OperationID)
		}
		if len(fromPackage.Consumes) != 1 || fromPackage.Consumes[0] != "multipart/form-data" {
			t.Fatalf("generated Swagger %s consumes=%v", path, fromPackage.Consumes)
		}

		if len(fromPackage.Parameters) != len(requiredFields) {
			t.Fatalf(
				"generated Swagger %s parameters=%d, want %d",
				path,
				len(fromPackage.Parameters),
				len(requiredFields),
			)
		}
		for _, formParameter := range fromPackage.Parameters {
			required, ok := requiredFields[formParameter.Name]
			if !ok {
				t.Fatalf("generated Swagger %s has unexpected parameter %q", path, formParameter.Name)
			}
			if formParameter.In != "formData" || formParameter.Required != required {
				t.Fatalf(
					"generated Swagger %s parameter %s in=%q required=%t",
					path,
					formParameter.Name,
					formParameter.In,
					formParameter.Required,
				)
			}
			delete(requiredFields, formParameter.Name)
			if formParameter.Name == "source" && formParameter.Type != "file" {
				t.Fatalf("generated Swagger %s source type=%q", path, formParameter.Type)
			}
		}
		if len(requiredFields) != 0 {
			t.Fatalf("generated Swagger %s is missing parameters %v", path, requiredFields)
		}
		for _, status := range []string{"201", "400", "403", "413", "422", "500", "502"} {
			if _, ok := fromPackage.Responses[status]; !ok {
				t.Fatalf("generated Swagger %s is missing response %s", path, status)
			}
		}

		const polymorphicErrorRef = "#/definitions/api.PackageControlRunErrorResponse"
		for _, status := range []string{"400", "500"} {
			errorResponse := fromPackage.Responses[status]
			if errorResponse.Schema.Ref != polymorphicErrorRef {
				t.Fatalf(
					"generated Swagger %s response %s schema=%q",
					path,
					status,
					errorResponse.Schema.Ref,
				)
			}
			for _, payloadType := range []string{"api.ErrorResponse", "controlrun.Run"} {
				if !strings.Contains(errorResponse.Description, payloadType) {
					t.Fatalf(
						"generated Swagger %s response %s does not explain %s",
						path,
						status,
						payloadType,
					)
				}
			}
		}

		polymorphicError := document.Definitions["api.PackageControlRunErrorResponse"]
		for _, property := range []string{"valid", "message", "run_id", "status"} {
			if _, ok := polymorphicError.Properties[property]; !ok {
				t.Fatalf(
					"generated Swagger %s polymorphic error is missing %q",
					path,
					property,
				)
			}
		}

		for _, definitionName := range []string{
			"appdeploy.PackageBuildResponse",
			"appdeploy.AppRegistrationResponse",
			"controlrun.ApplicationEvidence",
		} {
			appSpec := document.Definitions[definitionName].Properties["app_spec"]
			if appSpec.Type != "object" || appSpec.Items != nil {
				t.Fatalf(
					"generated Swagger %s %s.app_spec type=%q items=%#v",
					path,
					definitionName,
					appSpec.Type,
					appSpec.Items,
				)
			}
		}
	}
}
