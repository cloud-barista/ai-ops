package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthz(t *testing.T) {
	server := NewServer(NewServerConfig())
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"service":"service-control-api"`) {
		t.Fatalf("unexpected body: %s", response.Body.String())
	}
}

func TestListAgents(t *testing.T) {
	server := NewServer(NewServerConfig())
	request := httptest.NewRequest(http.MethodGet, "/api/v1/agents", nil)
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "AIApplicationManagementAgent") {
		t.Fatalf("expected application agent in body: %s", response.Body.String())
	}
}

func TestExternalAgentRegistrationAPIFlow(t *testing.T) {
	server := NewServer(NewServerConfig())
	registrationBody := strings.NewReader(`{
		"name":"ExternalDeploymentAdvisor",
		"korean_name":"외부 배포 검토 에이전트",
		"version":"0.1.0",
		"role":"Review AI application deployment plans.",
		"endpoint":"https://agent.example.com",
		"invocation_path":"/v1/actions",
		"capabilities":["deployment_review"],
		"bounded_actions":["review_deployment_plan"]
	}`)
	registrationRequest := httptest.NewRequest(http.MethodPost, "/api/v1/agents", registrationBody)
	registrationRequest.Header.Set("Content-Type", "application/json")
	registrationResponse := httptest.NewRecorder()

	server.ServeHTTP(registrationResponse, registrationRequest)

	if registrationResponse.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d body=%s", registrationResponse.Code, registrationResponse.Body.String())
	}
	if !strings.Contains(registrationResponse.Body.String(), `"source":"runtime"`) {
		t.Fatalf("expected runtime registration source: %s", registrationResponse.Body.String())
	}

	planBody := strings.NewReader(`{
		"capability":"deployment_review",
		"action":"review_deployment_plan",
		"parameters":{"workload":"llm-chat-inference"}
	}`)
	planRequest := httptest.NewRequest(http.MethodPost, "/api/v1/agents/ExternalDeploymentAdvisor/invocations/plan", planBody)
	planRequest.Header.Set("Content-Type", "application/json")
	planResponse := httptest.NewRecorder()

	server.ServeHTTP(planResponse, planRequest)

	if planResponse.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", planResponse.Code, planResponse.Body.String())
	}
	if !strings.Contains(planResponse.Body.String(), `"execution_status":"not_executed"`) {
		t.Fatalf("expected non-executing invocation plan: %s", planResponse.Body.String())
	}

	actionRequest := httptest.NewRequest(http.MethodPost, "/api/v1/agents/ExternalDeploymentAdvisor/actions/restart_vm/validate", nil)
	actionResponse := httptest.NewRecorder()
	server.ServeHTTP(actionResponse, actionRequest)

	if actionResponse.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", actionResponse.Code, actionResponse.Body.String())
	}
	if !strings.Contains(actionResponse.Body.String(), `"valid":false`) {
		t.Fatalf("expected unbounded action rejection: %s", actionResponse.Body.String())
	}
}

func TestSelectOpsLLM(t *testing.T) {
	server := NewServer(NewServerConfig())
	body := strings.NewReader(`{"policy":"quality_first"}`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/ops-llm/select", body)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"selected_model":"primary-ops-llm"`) {
		t.Fatalf("expected primary-ops-llm selection: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"selected_actual_model":"to-be-evaluated-primary-model"`) {
		t.Fatalf("expected selected actual model placeholder: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"benchmark_status":"not_executed"`) {
		t.Fatalf("expected not_executed benchmark status: %s", response.Body.String())
	}
}

func TestMalformedRequestUsesUserFacingMessage(t *testing.T) {
	server := NewServer(NewServerConfig())
	body := strings.NewReader(`{"policy":`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/ops-llm/select", body)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["valid"] != false {
		t.Fatalf("expected invalid response: %#v", result)
	}
	if result["message"] != "Malformed request body: check JSON syntax" {
		t.Fatalf("expected user-facing message, got %#v", result["message"])
	}
	if _, ok := result["error"]; ok {
		t.Fatalf("API response must not expose raw internal error: %#v", result)
	}
}

func TestRequiredRequestFieldUsesValidator(t *testing.T) {
	server := NewServer(NewServerConfig())
	body := strings.NewReader(`{}`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/apps/placement", body)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["valid"] != false {
		t.Fatalf("expected invalid response: %#v", result)
	}
	if result["message"] != "Required request field is missing" {
		t.Fatalf("expected validator message, got %#v", result["message"])
	}
}

func TestPlacementAndDeploymentPlan(t *testing.T) {
	server := NewServer(NewServerConfig())
	placementBody := strings.NewReader(`{"workload":"llm-chat-inference"}`)
	placementRequest := httptest.NewRequest(http.MethodPost, "/api/v1/apps/placement", placementBody)
	placementRequest.Header.Set("Content-Type", "application/json")
	placementResponse := httptest.NewRecorder()

	server.ServeHTTP(placementResponse, placementRequest)

	if placementResponse.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", placementResponse.Code, placementResponse.Body.String())
	}
	placement := decodeObject(t, placementResponse.Body.Bytes())
	assertPresent(t, placement, "selected_resource")
	assertPresent(t, placement, "action")
	assertPresent(t, placement, "ranked_candidates")
	assertPresent(t, placement, "rejected_resources")

	body := strings.NewReader(`{"workload":"llm-chat-inference"}`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/apps/deployment-plan", body)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"selected_resource":"gpu-vm-l4"`) {
		t.Fatalf("expected gpu-vm-l4 placement: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"service":"llm-chat-inference"`) {
		t.Fatalf("expected VM deployment plan: %s", response.Body.String())
	}
	deployment := decodeObject(t, response.Body.Bytes())
	assertPresent(t, deployment, "selected_resource")
	assertPresent(t, deployment, "deployment_plan")
}

func TestRunServiceOperationsEndpoint(t *testing.T) {
	server := NewServer(NewServerConfig())
	body := strings.NewReader(`{
		"llm_policy":"quality_first",
		"workload":"llm-chat-inference",
		"operation_service":"llm-chat-inference",
		"operation_resource":"gpu-vm-l4",
		"mode":"mock",
		"guard_backend":"go"
	}`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/service-operations/run", body)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"command":"run-service-operations"`) {
		t.Fatalf("expected service operations report: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"guard_backend":"go"`) {
		t.Fatalf("expected Go guard backend: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"selected_actual_model":"to-be-evaluated-primary-model"`) {
		t.Fatalf("expected selected actual model placeholder: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"benchmark_status":"not_executed"`) {
		t.Fatalf("expected benchmark status: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"guard_validation"`) {
		t.Fatalf("expected guard validation result: %s", response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	assertPresent(t, result, "deployment_execution_mode")
	assertPresent(t, result, "deployment_validation")
	if result["deployment_execution_mode"] != "mock" {
		t.Fatalf("expected mock execution mode, got %#v", result["deployment_execution_mode"])
	}
	validation := result["deployment_validation"].(map[string]any)
	if validation["valid"] != true {
		t.Fatalf("expected VM deployment validation to pass: %#v", validation)
	}
	guard := result["guard_validation"].(map[string]any)
	if guard["valid"] != true {
		t.Fatalf("expected guard validation to be valid: %#v", guard)
	}
}

func decodeObject(t *testing.T, data []byte) map[string]any {
	t.Helper()
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatalf("failed to decode response: %v body=%s", err, string(data))
	}
	return result
}

func assertPresent(t *testing.T, object map[string]any, key string) {
	t.Helper()
	if _, ok := object[key]; !ok {
		t.Fatalf("expected key %s in %#v", key, object)
	}
}
