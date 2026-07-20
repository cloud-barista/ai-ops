package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
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
	if !strings.Contains(response.Body.String(), "AIApplicationAutomationAgent") {
		t.Fatalf("expected application agent in body: %s", response.Body.String())
	}
	if strings.Contains(response.Body.String(), "AISemiconductorInfraOpsAgent") {
		t.Fatalf("deterministic VM validation must not be exposed as a built-in AI agent: %s", response.Body.String())
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
	request := httptest.NewRequest(http.MethodPost, "/api/v1/apps/vm-suitability", body)
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

func TestVMSuitabilityAndDeploymentPlan(t *testing.T) {
	server := NewServer(NewServerConfig())
	placementBody := strings.NewReader(`{
		"workload":"llm-chat-inference",
		"target_vm":{
			"id":"aws-us-west-2-g6-xlarge-l4-20260707",
			"source":"cb-tumblebug_and_vm_evidence",
			"evidence_status":"collected",
			"provider":"aws",
			"region":"us-west-2",
			"instance_type":"g6.xlarge",
			"accelerator":"gpu",
			"gpu_model":"NVIDIA L4",
			"gpu_memory_mib":23034,
			"performance":{"status":"not_measured"}
		}
	}`)
	placementRequest := httptest.NewRequest(http.MethodPost, "/api/v1/apps/vm-suitability", placementBody)
	placementRequest.Header.Set("Content-Type", "application/json")
	placementResponse := httptest.NewRecorder()

	server.ServeHTTP(placementResponse, placementRequest)

	if placementResponse.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", placementResponse.Code, placementResponse.Body.String())
	}
	placement := decodeObject(t, placementResponse.Body.Bytes())
	assertPresent(t, placement, "target_vm_id")
	assertPresent(t, placement, "action")
	assertPresent(t, placement, "compatibility_status")
	assertPresent(t, placement, "checks")

	body := strings.NewReader(`{
		"workload":"llm-chat-inference",
		"target_vm":{
			"id":"aws-us-west-2-g6-xlarge-l4-20260707",
			"source":"cb-tumblebug_and_vm_evidence",
			"evidence_status":"collected",
			"provider":"aws",
			"region":"us-west-2",
			"instance_type":"g6.xlarge",
			"accelerator":"gpu",
			"gpu_model":"NVIDIA L4",
			"gpu_memory_mib":23034,
			"performance":{"status":"not_measured"}
		}
	}`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/apps/deployment-plan", body)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"`) {
		t.Fatalf("expected recorded VM target: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"executor_type":"registered_external_agent"`) {
		t.Fatalf("expected generic registered-agent handoff plan: %s", response.Body.String())
	}
	deployment := decodeObject(t, response.Body.Bytes())
	assertPresent(t, deployment, "target_vm_id")
	assertPresent(t, deployment, "deployment_plan")
}

func TestLLMAutomationActionEndpoint(t *testing.T) {
	provider, closeProvider := automationProvider(t, `{
  "action":"observe_status",
  "reason":"Performance evidence is not measured.",
  "confidence":0.82,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`)
	defer closeProvider()
	config := NewServerConfig()
	config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
	server := NewServer(config)

	registration := strings.NewReader(`{
  "name":"GenericDeploymentExecutor",
  "version":"v1",
  "role":"Execute approved AI application control actions.",
  "endpoint":"http://executor.internal",
  "invocation_path":"/v1/actions",
  "capabilities":["ai_application_deployment_control"],
  "bounded_actions":["observe_status"],
  "enabled":true
}`)
	registrationRequest := httptest.NewRequest(http.MethodPost, "/api/v1/agents", registration)
	registrationRequest.Header.Set("content-type", "application/json")
	registrationResponse := httptest.NewRecorder()
	server.ServeHTTP(registrationResponse, registrationRequest)
	if registrationResponse.Code != http.StatusCreated {
		t.Fatalf("register executor: status=%d body=%s", registrationResponse.Code, registrationResponse.Body.String())
	}

	body, err := json.Marshal(LLMAutomationActionRequest{
		Workload:    "llm-chat-inference",
		TargetVM:    recordedL4VM(),
		CandidateID: "decision-model",
	})
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	request := httptest.NewRequest(http.MethodPost, "/api/v1/automation/action-proposals", bytes.NewReader(body))
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["status"] != "approved" || result["valid"] != true {
		t.Fatalf("expected approved LLM automation result: %#v", result)
	}
}

func TestAppDeployPlannerEndpointGeneratesValidManifestAndTracksDeployment(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("content-type", "application/json")
		switch {
		case request.Method == http.MethodPost && request.URL.Path == "/llm":
			content := `{"schema_version":"deployment.khu.ai/v1alpha1","kind":"DeploymentManifest","metadata":{"name":"llm-service"},"spec":{"app_version_id":"appver-llm-v1","accelerator":"nvidia","resources":{"cpu":"4","memory":"16Gi","gpu":"1","storage":"20Gi"}}}`
			_, _ = writer.Write([]byte(`{"choices":[{"message":{"content":` + strconv.Quote(content) + `}}]}`))
		case request.Method == http.MethodPost && request.URL.Path == "/api/v1/deployments":
			var body struct {
				Manifest map[string]any `json:"manifest"`
			}
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
				t.Fatalf("decode AppDeploy request: %v", err)
			}
			if body.Manifest["kind"] != "DeploymentManifest" {
				t.Fatalf("expected guarded manifest handoff: %#v", body.Manifest)
			}
			writer.WriteHeader(http.StatusAccepted)
			_, _ = writer.Write([]byte(`{"deployment_id":"dep-planner-1","status":"REQUESTED"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v1/deployments/dep-planner-1":
			_, _ = writer.Write([]byte(`{"deployment_id":"dep-planner-1","status":"RUNNING","target_profile_id":"appdeploy-selected-target"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v1/deployments/dep-planner-1/logs":
			_, _ = writer.Write([]byte(`{"deployment_id":"dep-planner-1","items":[{"stage":"RUNNING","message":"ready"}]}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	defer upstream.Close()

	config := NewServerConfig()
	config.LLMCandidatesPath = writeAutomationCandidateConfig(t, upstream.URL+"/llm")
	config.AppDeployBaseURL = upstream.URL + "/api/v1"
	server := NewServer(config)
	body := strings.NewReader(`{
		"natural_language_request":"GPU 1개와 16Gi 메모리가 필요한 추론 앱을 배포해 주세요.",
		"app_version_id":"appver-llm-v1",
		"candidate_id":"decision-model",
		"poll_interval_ms":1,
		"max_poll_attempts":3
	}`)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/planner/deployments", body)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["valid"] != true || result["status"] != "RUNNING" {
		t.Fatalf("unexpected planner response: %#v", result)
	}
	deployment := result["deployment"].(map[string]any)
	if deployment["target_profile_id"] != "appdeploy-selected-target" {
		t.Fatalf("AppDeploy-selected target was not preserved: %#v", deployment)
	}
}

func TestAutomationFeedbackEndpoint(t *testing.T) {
	provider, closeProvider := automationProvider(t, `{
  "action":"observe_status",
  "reason":"Observe the validated target.",
  "confidence":0.8,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`)
	defer closeProvider()
	config := NewServerConfig()
	config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
	server := NewServer(config)

	registration := strings.NewReader(`{
  "name":"GenericDeploymentExecutor",
  "version":"v1",
  "role":"Execute approved AI application control actions.",
  "endpoint":"http://executor.internal",
  "invocation_path":"/v1/actions",
  "capabilities":["ai_application_deployment_control"],
  "bounded_actions":["observe_status"],
  "enabled":true
}`)
	registrationRequest := httptest.NewRequest(http.MethodPost, "/api/v1/agents", registration)
	registrationRequest.Header.Set("content-type", "application/json")
	registrationResponse := httptest.NewRecorder()
	server.ServeHTTP(registrationResponse, registrationRequest)

	actionBody, _ := json.Marshal(LLMAutomationActionRequest{
		Workload:    "llm-chat-inference",
		TargetVM:    recordedL4VM(),
		CandidateID: "decision-model",
	})
	actionRequest := httptest.NewRequest(http.MethodPost, "/api/v1/automation/action-proposals", bytes.NewReader(actionBody))
	actionRequest.Header.Set("content-type", "application/json")
	actionResponse := httptest.NewRecorder()
	server.ServeHTTP(actionResponse, actionRequest)
	action := decodeObject(t, actionResponse.Body.Bytes())
	correlationID, _ := action["correlation_id"].(string)
	if correlationID == "" {
		t.Fatalf("expected correlation id: %#v", action)
	}

	feedbackBody, _ := json.Marshal(AutomationFeedbackRequest{
		CorrelationID: correlationID,
		Executor:      "GenericDeploymentExecutor",
		Status:        "running",
		Message:       "execution started",
	})
	feedbackRequest := httptest.NewRequest(http.MethodPost, "/api/v1/automation/feedback", bytes.NewReader(feedbackBody))
	feedbackRequest.Header.Set("content-type", "application/json")
	feedbackResponse := httptest.NewRecorder()
	server.ServeHTTP(feedbackResponse, feedbackRequest)
	if feedbackResponse.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d body=%s", feedbackResponse.Code, feedbackResponse.Body.String())
	}
	feedback := decodeObject(t, feedbackResponse.Body.Bytes())
	if feedback["status"] != "running" || feedback["correlation_id"] != correlationID {
		t.Fatalf("unexpected feedback response: %#v", feedback)
	}
}

func TestRunServiceOperationsEndpoint(t *testing.T) {
	server := NewServer(NewServerConfig())
	body := strings.NewReader(`{
		"llm_policy":"quality_first",
		"workload":"llm-chat-inference",
		"target_vm":{
			"id":"aws-us-west-2-g6-xlarge-l4-20260707",
			"source":"cb-tumblebug_and_vm_evidence",
			"evidence_status":"collected",
			"provider":"aws",
			"region":"us-west-2",
			"instance_type":"g6.xlarge",
			"accelerator":"gpu",
			"gpu_model":"NVIDIA L4",
			"gpu_memory_mib":23034,
			"performance":{"status":"not_measured"}
		},
		"operation_service":"llm-chat-inference",
		"operation_resource":"aws-us-west-2-g6-xlarge-l4-20260707",
		"mode":"plan_only",
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
	if result["deployment_execution_mode"] != "plan_only" {
		t.Fatalf("expected plan-only execution mode, got %#v", result["deployment_execution_mode"])
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
