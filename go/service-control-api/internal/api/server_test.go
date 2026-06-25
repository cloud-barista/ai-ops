package api

import (
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
	if !strings.Contains(response.Body.String(), `"selected_model":"gpt-5.5"`) {
		t.Fatalf("expected gpt-5.5 selection: %s", response.Body.String())
	}
}

func TestPlacementAndDeploymentPlan(t *testing.T) {
	server := NewServer(NewServerConfig())
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
}

func TestRunServiceOperationsEndpoint(t *testing.T) {
	server := NewServer(NewServerConfig())
	body := strings.NewReader(`{
		"llm_policy":"quality_first",
		"workload":"llm-chat-inference",
		"namespace":"online-boutique",
		"deployment":"paymentservice",
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
}
