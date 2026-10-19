package profileapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"kyunghee-aiops/llmop-api/internal/applicationprofile"
)

type generatorStub struct {
	result  applicationprofile.GenerateResult
	err     error
	request applicationprofile.GenerateRequest
}

func (stub *generatorStub) Generate(
	_ context.Context,
	request applicationprofile.GenerateRequest,
) (applicationprofile.GenerateResult, error) {
	stub.request = request
	return stub.result, stub.err
}

func TestServerExposesOnlyFocusedLLMOpBoundaries(t *testing.T) {
	generator := &generatorStub{result: applicationprofile.GenerateResult{
		ApplicationProfile: applicationprofile.ApplicationProfile{
			ProfileID:  "profile-chat-001",
			AppID:      "chat",
			AppVersion: "1.0.0",
		},
		Analysis: applicationprofile.AnalysisEvidence{Mode: applicationprofile.AnalysisModeLLM},
	}}
	server := newServer(testConfig(), generator)

	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/application-profiles",
		strings.NewReader(`{"request":"GPU 추론 서비스를 위한 요구사항을 분석해 주세요."}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("POST application-profiles: code=%d body=%s", response.Code, response.Body.String())
	}
	if generator.request.Request == "" || !strings.Contains(response.Body.String(), `"application_profile"`) {
		t.Fatalf("generator request=%#v body=%s", generator.request, response.Body.String())
	}

	for _, path := range []string{
		"/api/v1/agents",
		"/api/v1/agent-control/automation-runs",
		"/api/v1/planner/deployments",
		"/api/v1/control-runs",
		"/api/v1/autonomy/start",
	} {
		request := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{}`))
		response := httptest.NewRecorder()
		server.ServeHTTP(response, request)
		if response.Code != http.StatusNotFound {
			t.Fatalf("POST %s code=%d, want 404", path, response.Code)
		}
	}
}

func TestServerSelectsOpsLLMWithConfiguredPolicy(t *testing.T) {
	server := newServer(testConfig(), &generatorStub{})
	request := httptest.NewRequest(
		http.MethodPost,
		pathOpsLLMSelect,
		strings.NewReader(`{"policy":"quality_first"}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"selected_candidate_id":"qwen3.5-profile-generator"`) ||
		!strings.Contains(response.Body.String(), `"benchmark_status":"not_executed"`) ||
		!strings.Contains(response.Body.String(), `"ranking"`) {
		t.Fatalf("unexpected selection response: %s", response.Body.String())
	}
}

func TestServerRejectsUnknownOpsLLMPolicy(t *testing.T) {
	server := newServer(testConfig(), &generatorStub{})
	request := httptest.NewRequest(
		http.MethodPost,
		pathOpsLLMSelect,
		strings.NewReader(`{"policy":"unconfigured"}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest ||
		!strings.Contains(response.Body.String(), `"code":"UNKNOWN_POLICY"`) {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
}

func TestServerMapsGeneratorErrorsWithoutRawDetails(t *testing.T) {
	tests := []struct {
		name       string
		err        error
		statusCode int
		code       string
	}{
		{name: "invalid request", err: applicationprofile.ErrInvalidRequest, statusCode: 400, code: "INVALID_REQUEST"},
		{name: "model unavailable", err: applicationprofile.ErrModelUnavailable, statusCode: 503, code: "MODEL_UNAVAILABLE"},
		{name: "invalid output", err: applicationprofile.ErrInvalidModelOutput, statusCode: 502, code: "INVALID_MODEL_OUTPUT"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(testConfig(), &generatorStub{err: fmt.Errorf("private detail: %w", test.err)})
			request := httptest.NewRequest(http.MethodPost, "/api/v1/application-profiles", strings.NewReader(`{"request":"analyze"}`))
			request.Header.Set("content-type", "application/json")
			response := httptest.NewRecorder()
			server.ServeHTTP(response, request)
			if response.Code != test.statusCode {
				t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
			}
			var body ErrorResponse
			if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
				t.Fatalf("decode response: %v", err)
			}
			if body.Code != test.code || strings.Contains(body.Message, "private detail") {
				t.Fatalf("response = %#v", body)
			}
		})
	}
}

func TestServerRejectsUnknownRequestFieldsBeforeGenerator(t *testing.T) {
	generator := &generatorStub{err: errors.New("must not be called")}
	server := newServer(testConfig(), generator)
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/application-profiles",
		strings.NewReader(`{"request":"analyze","deployment_agent":"agent-a"}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
	if generator.request.Request != "" {
		t.Fatalf("generator was called with %#v", generator.request)
	}
}

func TestServerRejectsOversizedBody(t *testing.T) {
	generator := &generatorStub{}
	config := testConfig()
	config.MaxRequestBytes = 16
	server := newServer(config, generator)
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/application-profiles",
		strings.NewReader(`{"request":"this body is too large"}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusRequestEntityTooLarge ||
		!strings.Contains(response.Body.String(), `"code":"REQUEST_TOO_LARGE"`) {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
	if generator.request.Request != "" {
		t.Fatalf("generator was called with %#v", generator.request)
	}
}

func TestHealthzDescribesFocusedRole(t *testing.T) {
	server := newServer(testConfig(), &generatorStub{})
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK ||
		!strings.Contains(response.Body.String(), `"service":"llmop-api"`) ||
		!strings.Contains(response.Body.String(), `"role":"application_profile_generation_and_llm_selection"`) {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
}

func TestServerGeneratesProfileThroughConfiguredLLMClient(t *testing.T) {
	model := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/chat/completions" {
			http.Error(response, "unexpected model path", http.StatusNotFound)
			return
		}
		response.Header().Set("content-type", "application/json")
		_, _ = response.Write([]byte(`{"choices":[{"message":{"content":"{\"app_id\":\"chat\",\"app_version\":\"1.0.0\",\"workload\":{\"task_type\":\"LLM_INFERENCE\",\"request_pattern\":\"ONLINE\",\"expected_rps\":5},\"requirements\":{\"compute\":{\"cpu_cores_min\":4,\"memory_mib_min\":8192,\"storage_gib_min\":20},\"accelerator\":{\"required\":true,\"type\":\"GPU\",\"count_min\":1,\"memory_mib_min_per_device\":16384},\"deployment\":{\"replicas_min\":1,\"replicas_max\":2,\"isolation\":\"ONE_MAJOR_APP_PER_VM\"},\"slo\":{\"latency_p95_ms_max\":0,\"throughput_rps_min\":0},\"cost\":{\"currency\":\"\",\"cost_per_hour_max\":0}},\"analysis\":{\"confidence\":0.9,\"assumptions\":[],\"missing_fields\":[],\"warnings\":[]}}"}}]}`))
	}))
	defer model.Close()

	candidatePath := filepath.Join(t.TempDir(), "candidates.json")
	candidateConfig := fmt.Sprintf(`{"version":"1","candidates":[{"candidate_id":"test-profile","provider":"openai-compatible","actual_model":"test-model","endpoint":%q,"enabled":true,"json_mode":true}]}`, model.URL+"/v1/chat/completions")
	if err := os.WriteFile(candidatePath, []byte(candidateConfig), 0o600); err != nil {
		t.Fatalf("write candidates: %v", err)
	}
	server := NewServer(Config{
		LLMCandidatesPath: candidatePath,
		CandidateID:       "test-profile",
		MaxRequestBytes:   defaultMaxRequestBytes,
	})
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/application-profiles",
		strings.NewReader(`{"request":"GPU 1개와 CPU 4코어가 필요한 추론 서비스","application":{"app_id":"chat","app_version":"1.0.0"}}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"profile_id":"profile-chat-`) ||
		!strings.Contains(response.Body.String(), `"mode":"llm"`) ||
		strings.Contains(response.Body.String(), "manifest") {
		t.Fatalf("unexpected response: %s", response.Body.String())
	}
}

func TestServerReturnsLabeledFallbackWhenSelectedCandidateIsDisabled(t *testing.T) {
	config := testConfig()
	config.LLMCandidatesPath = filepath.Join(
		"..", "..", "..", "..", "config", "application_profile_llm_candidates.json",
	)
	config.CandidateID = "qwen3.5-profile-generator"
	config.MaxLLMAttempts = 2
	server := NewServer(config)
	request := httptest.NewRequest(
		http.MethodPost,
		pathApplicationProfiles,
		strings.NewReader(`{
  "request":"GPU 1개, CPU 4코어, 메모리 8GiB, 스토리지 20GiB 온라인 LLM 추론 서비스",
  "application":{"app_id":"fallback-chat","app_version":"1.0.0"}
}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusCreated ||
		!strings.Contains(response.Body.String(), `"mode":"local_rule"`) ||
		!strings.Contains(response.Body.String(), `"fallback_reason":"candidate_unavailable"`) ||
		strings.Contains(response.Body.String(), `"mode":"llm"`) {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
}

func TestServerAcceptsStructuredAppSpecWithoutCandidateConfiguration(t *testing.T) {
	config := testConfig()
	config.LLMCandidatesPath = filepath.Join(t.TempDir(), "does-not-exist.json")
	config.CandidateID = "unused"
	server := NewServer(config)
	request := httptest.NewRequest(
		http.MethodPost,
		pathApplicationProfiles,
		strings.NewReader(`{
  "input_type":"structured",
  "app_spec":{
    "app_id":"batch-embedder",
    "app_version":"1.0.0",
    "workload_type":"EMBEDDING",
    "request_pattern":"BATCH",
    "cpu_cores":8,
    "memory_mib":32768,
    "storage_gib":50
  }
}`),
	)
	request.Header.Set("content-type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusCreated ||
		!strings.Contains(response.Body.String(), `"mode":"structured"`) ||
		!strings.Contains(response.Body.String(), `"app_id":"batch-embedder"`) {
		t.Fatalf("code=%d body=%s", response.Code, response.Body.String())
	}
}

func testConfig() Config {
	return Config{
		OpsLLMBenchmarkPath: filepath.Join("..", "..", "..", "..", "config", "ops_llm_benchmark.json"),
		MaxRequestBytes:     defaultMaxRequestBytes,
	}
}
