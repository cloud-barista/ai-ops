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
	if !strings.Contains(response.Body.String(), `"deployment":"llm-chat-inference"`) {
		t.Fatalf("expected deployment plan: %s", response.Body.String())
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
		"recovery_namespace":"aiops-demo",
		"recovery_deployment":"aiops-service",
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
	assertPresent(t, result, "kubernetes_live_apply")
	if result["deployment_execution_mode"] != "mock" {
		t.Fatalf("expected mock execution mode, got %#v", result["deployment_execution_mode"])
	}
	if result["kubernetes_live_apply"] != false {
		t.Fatalf("prototype must not claim Kubernetes live apply: %#v", result["kubernetes_live_apply"])
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
