package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/api"
)

type apiIntegrationValidationOptions struct {
	OutputDir string
	Port      int
	BaseURL   string
}

type apiEndpointValidation struct {
	Name       string   `json:"name"`
	Method     string   `json:"method"`
	Path       string   `json:"path"`
	Valid      bool     `json:"valid"`
	StatusCode int      `json:"status_code"`
	RawPath    string   `json:"raw_path"`
	Checks     []string `json:"checks"`
	Error      string   `json:"error,omitempty"`
}

type apiIntegrationValidationSummary struct {
	Command                   string                  `json:"command"`
	ValidationType            string                  `json:"validation_type"`
	Target                    string                  `json:"target"`
	Valid                     bool                    `json:"valid"`
	EndpointCount             int                     `json:"endpoint_count"`
	ValidEndpointCount        int                     `json:"valid_endpoint_count"`
	ProductionLevelValidation bool                    `json:"production_level_validation"`
	FullOperationalValidation bool                    `json:"full_operational_validation"`
	Description               string                  `json:"description"`
	BaseURL                   string                  `json:"base_url"`
	OutputDir                 string                  `json:"output_dir"`
	SummaryPath               string                  `json:"summary_path"`
	Endpoints                 []apiEndpointValidation `json:"endpoints"`
}

type apiEndpointSpec struct {
	Name     string
	Method   string
	Path     string
	Body     map[string]any
	BodyFunc func() map[string]any
	Check    func(map[string]any) []string
}

func runAPIIntegrationValidation(config api.ServerConfig, options apiIntegrationValidationOptions) (apiIntegrationValidationSummary, error) {
	if options.OutputDir == "" {
		options.OutputDir = filepath.Join(config.RepoRoot, "runs", "api-integration-local-"+time.Now().Format("20060102-150405"))
	}
	outputDirAbs, err := filepath.Abs(options.OutputDir)
	if err != nil {
		return apiIntegrationValidationSummary{}, err
	}
	if err := os.MkdirAll(outputDirAbs, 0o755); err != nil {
		return apiIntegrationValidationSummary{}, err
	}

	baseURL := strings.TrimRight(options.BaseURL, "/")
	var shutdown func(context.Context) error
	if baseURL == "" {
		provider := startAPIIntegrationLLMProvider()
		defer provider.Close()
		candidatesPath := filepath.Join(outputDirAbs, "api-integration-llm-candidates.json")
		candidateConfig := fmt.Sprintf(`{"version":"1","candidates":[{"candidate_id":"api-integration-model","role_label":"primary-ops-llm","provider":"local-contract-test","actual_model":"api-integration-test-model","endpoint":%q,"enabled":true}]}`, provider.URL)
		if err := os.WriteFile(candidatesPath, []byte(candidateConfig), 0o600); err != nil {
			return apiIntegrationValidationSummary{}, err
		}
		config.LLMCandidatesPath = candidatesPath
		baseURL, shutdown, err = startLocalAPIServer(config, options.Port)
		if err != nil {
			return apiIntegrationValidationSummary{}, err
		}
	}
	if shutdown != nil {
		defer func() {
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			_ = shutdown(ctx)
		}()
	}

	client := &http.Client{Timeout: 10 * time.Second}
	specs := apiIntegrationEndpointSpecs()
	endpoints := make([]apiEndpointValidation, 0, len(specs))
	validCount := 0
	for index, spec := range specs {
		endpoint := callAndValidateEndpoint(client, baseURL, outputDirAbs, index+1, spec)
		if endpoint.Valid {
			validCount++
		}
		endpoints = append(endpoints, endpoint)
	}

	summaryPath := filepath.Join(outputDirAbs, "api-integration-validation-summary.json")
	summary := apiIntegrationValidationSummary{
		Command:                   "api-integration-validation",
		ValidationType:            "local_api_integration_validation",
		Target:                    "local",
		Valid:                     validCount == len(specs),
		EndpointCount:             len(specs),
		ValidEndpointCount:        validCount,
		ProductionLevelValidation: false,
		FullOperationalValidation: false,
		Description:               "Local service-control API endpoints were called sequentially and key response fields were verified. This is not production-level operational validation.",
		BaseURL:                   baseURL,
		OutputDir:                 outputDirAbs,
		SummaryPath:               summaryPath,
		Endpoints:                 endpoints,
	}
	if err := writeJSONFile(summaryPath, summary); err != nil {
		return summary, err
	}
	if !summary.Valid {
		return summary, fmt.Errorf("api integration validation failed: %d/%d endpoints valid", validCount, len(specs))
	}
	return summary, nil
}

func startAPIIntegrationLLMProvider() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		content := `{"action":"observe_status","reason":"Contract test provider response.","confidence":0.8,"required_capability":"ai_application_deployment_control","target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"}`
		writer.Header().Set("content-type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]string{"content": content}}},
		})
	}))
}

func startLocalAPIServer(config api.ServerConfig, port int) (string, func(context.Context) error, error) {
	address := fmt.Sprintf("127.0.0.1:%d", port)
	listener, err := net.Listen("tcp", address)
	if err != nil {
		if port > 0 {
			return "http://" + address, nil, nil
		}
		return "", nil, err
	}
	server := &http.Server{
		Handler:           api.NewServer(config),
		ReadHeaderTimeout: 5 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() {
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			errCh <- err
		}
		close(errCh)
	}()
	baseURL := "http://" + listener.Addr().String()
	if err := waitForAPIHealth(baseURL, 5*time.Second); err != nil {
		_ = server.Close()
		if serveErr := <-errCh; serveErr != nil {
			return baseURL, nil, fmt.Errorf("%w; server error: %s", err, serveErr)
		}
		return baseURL, nil, err
	}
	return baseURL, server.Shutdown, nil
}

func waitForAPIHealth(baseURL string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 500 * time.Millisecond}
	for time.Now().Before(deadline) {
		response, err := client.Get(baseURL + "/healthz")
		if err == nil {
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK {
				return nil
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	return fmt.Errorf("local API server did not become ready at %s", baseURL)
}

func apiIntegrationEndpointSpecs() []apiEndpointSpec {
	var automationCorrelationID string
	var automationExecutor string
	return []apiEndpointSpec{
		{
			Name:   "healthz",
			Method: http.MethodGet,
			Path:   "/healthz",
			Check: func(response map[string]any) []string {
				return requireValues(response, map[string]any{
					"status":  "ok",
					"service": "service-control-api",
				})
			},
		},
		{
			Name:   "agents",
			Method: http.MethodGet,
			Path:   "/api/v1/agents",
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{"command": "list-agents"})
				checks = append(checks, requireArrayMin(response, "agents", 1)...)
				return checks
			},
		},
		{
			Name:   "external-agent-register",
			Method: http.MethodPost,
			Path:   "/api/v1/agents",
			Body: map[string]any{
				"name":            "ValidationDeploymentAdvisor",
				"version":         "0.1.0",
				"role":            "Review AI application deployment plans.",
				"endpoint":        "https://agent.example.com",
				"invocation_path": "/v1/actions",
				"capabilities": []string{
					"deployment_review",
					"ai_application_deployment_control",
				},
				"bounded_actions": []string{
					"review_deployment_plan",
					"deploy_application",
					"observe_status",
				},
			},
			Check: func(response map[string]any) []string {
				return requireValues(response, map[string]any{
					"name":    "ValidationDeploymentAdvisor",
					"source":  "runtime",
					"enabled": true,
				})
			},
		},
		{
			Name:   "external-agent-invocation-plan",
			Method: http.MethodPost,
			Path:   "/api/v1/agents/ValidationDeploymentAdvisor/invocations/plan",
			Body: map[string]any{
				"capability": "deployment_review",
				"action":     "review_deployment_plan",
				"parameters": map[string]any{"workload": "llm-chat-inference"},
			},
			Check: func(response map[string]any) []string {
				return requireValues(response, map[string]any{
					"valid":            true,
					"agent":            "ValidationDeploymentAdvisor",
					"execution_status": "not_executed",
					"target_url":       "https://agent.example.com/v1/actions",
				})
			},
		},
		{
			Name:   "llm-automation-action",
			Method: http.MethodPost,
			Path:   "/api/v1/automation/action-proposals",
			Body: map[string]any{
				"workload":     "llm-chat-inference",
				"target_vm":    apiValidationTargetVM(),
				"candidate_id": "api-integration-model",
			},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{
					"valid":  true,
					"status": "approved",
				})
				checks = append(checks, requirePresent(response, "correlation_id", "decision", "guard", "handoff")...)
				decision, ok := response["decision"].(map[string]any)
				if !ok {
					checks = append(checks, "decision is not an object")
				} else {
					checks = append(checks, requireValues(decision, map[string]any{"decision_execution_status": "executed"})...)
				}
				if value, ok := response["correlation_id"].(string); ok {
					automationCorrelationID = value
				}
				if handoff, ok := response["handoff"].(map[string]any); ok {
					if value, ok := handoff["agent"].(string); ok {
						automationExecutor = value
					}
				}
				return checks
			},
		},
		{
			Name:   "automation-feedback",
			Method: http.MethodPost,
			Path:   "/api/v1/automation/feedback",
			BodyFunc: func() map[string]any {
				return map[string]any{
					"correlation_id":        automationCorrelationID,
					"executor":              automationExecutor,
					"status":                "succeeded",
					"external_execution_id": "api-integration-execution",
					"latency_ms":            15.0,
					"throughput_rps":        2.0,
				}
			},
			Check: func(response map[string]any) []string {
				return requireValues(response, map[string]any{
					"correlation_id": automationCorrelationID,
					"executor":       automationExecutor,
					"status":         "succeeded",
				})
			},
		},
		{
			Name:   "ops-llm-select",
			Method: http.MethodPost,
			Path:   "/api/v1/ops-llm/select",
			Body:   map[string]any{"policy": "quality_first"},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{"valid": true})
				checks = append(checks, requirePresent(response, "selected_model", "selected_actual_model", "selected_provider", "benchmark_status", "ranking")...)
				checks = append(checks, requireArrayMin(response, "ranking", 1)...)
				return checks
			},
		},
		{
			Name:   "apps-vm-suitability",
			Method: http.MethodPost,
			Path:   "/api/v1/apps/vm-suitability",
			Body: map[string]any{
				"workload":  "llm-chat-inference",
				"target_vm": apiValidationTargetVM(),
			},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{"valid": true})
				checks = append(checks, requirePresent(response, "target_vm_id", "compatibility_status", "resource_checks_passed", "checks")...)
				return checks
			},
		},
		{
			Name:   "apps-deployment-plan",
			Method: http.MethodPost,
			Path:   "/api/v1/apps/deployment-plan",
			Body: map[string]any{
				"workload":  "llm-chat-inference",
				"target_vm": apiValidationTargetVM(),
			},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{"valid": true})
				checks = append(checks, requirePresent(response, "target_vm_id", "deployment_plan")...)
				return checks
			},
		},
		{
			Name:   "service-operations-run",
			Method: http.MethodPost,
			Path:   "/api/v1/service-operations/run",
			Body: map[string]any{
				"llm_policy":         "quality_first",
				"workload":           "llm-chat-inference",
				"target_vm":          apiValidationTargetVM(),
				"operation_service":  "llm-chat-inference",
				"operation_resource": "aws-us-west-2-g6-xlarge-l4-20260707",
				"mode":               "plan_only",
				"guard_backend":      "go",
			},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{
					"valid": true,
				})
				checks = append(checks, requirePresent(response,
					"benchmark_status",
					"selected_resource",
					"deployment_plan",
					"deployment_validation",
					"deployment_execution_mode",
					"guard_backend",
					"guard_validation",
				)...)
				if _, ok := response["selected_llm"]; !ok {
					if _, selectedModelOK := response["selected_model"]; !selectedModelOK {
						checks = append(checks, "missing selected_llm or selected_model")
					}
				}
				guardValidation, ok := response["guard_validation"].(map[string]any)
				if !ok {
					checks = append(checks, "guard_validation is not an object")
				} else {
					checks = append(checks, requireValues(guardValidation, map[string]any{"valid": true})...)
				}
				return checks
			},
		},
	}
}

func apiValidationTargetVM() map[string]any {
	return map[string]any{
		"id":              "aws-us-west-2-g6-xlarge-l4-20260707",
		"source":          "recorded_vm_validation_evidence",
		"evidence_status": "collected",
		"provider":        "aws",
		"region":          "us-west-2",
		"instance_type":   "g6.xlarge",
		"accelerator":     "gpu",
		"gpu_model":       "NVIDIA L4",
		"gpu_memory_mib":  23034,
		"performance": map[string]any{
			"status": "not_measured",
		},
	}
}

func callAndValidateEndpoint(client *http.Client, baseURL string, outputDir string, index int, spec apiEndpointSpec) apiEndpointValidation {
	result := apiEndpointValidation{
		Name:   spec.Name,
		Method: spec.Method,
		Path:   spec.Path,
		Checks: []string{},
	}
	body := spec.Body
	if spec.BodyFunc != nil {
		body = spec.BodyFunc()
	}
	var requestBody io.Reader
	if body != nil {
		bodyBytes, err := json.Marshal(body)
		if err != nil {
			result.Error = err.Error()
			return result
		}
		requestBody = bytes.NewReader(bodyBytes)
	}
	request, err := http.NewRequest(spec.Method, baseURL+spec.Path, requestBody)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	if body != nil {
		request.Header.Set("content-type", "application/json")
	}
	response, err := client.Do(request)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	defer func() {
		_ = response.Body.Close()
	}()
	result.StatusCode = response.StatusCode
	responseBytes, err := io.ReadAll(response.Body)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	rawPath := filepath.Join(outputDir, fmt.Sprintf("%02d_%s_response.json", index, strings.ReplaceAll(spec.Name, "-", "_")))
	result.RawPath = rawPath
	if writeErr := os.WriteFile(rawPath, append(responseBytes, '\n'), 0o644); writeErr != nil {
		result.Error = writeErr.Error()
		return result
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		result.Error = fmt.Sprintf("unexpected HTTP status %d", response.StatusCode)
		return result
	}
	decoded := map[string]any{}
	if err := json.Unmarshal(responseBytes, &decoded); err != nil {
		result.Error = err.Error()
		return result
	}
	result.Checks = spec.Check(decoded)
	result.Valid = len(result.Checks) == 0
	if !result.Valid {
		result.Error = strings.Join(result.Checks, "; ")
	}
	return result
}

func requirePresent(response map[string]any, keys ...string) []string {
	missing := []string{}
	for _, key := range keys {
		if _, ok := response[key]; !ok {
			missing = append(missing, "missing "+key)
		}
	}
	return missing
}

func requireValues(response map[string]any, expected map[string]any) []string {
	failures := []string{}
	for key, expectedValue := range expected {
		actualValue, ok := response[key]
		if !ok {
			failures = append(failures, "missing "+key)
			continue
		}
		if expectedValue == nil {
			continue
		}
		if fmt.Sprint(actualValue) != fmt.Sprint(expectedValue) {
			failures = append(failures, fmt.Sprintf("%s expected %v got %v", key, expectedValue, actualValue))
		}
	}
	return failures
}

func requireArrayMin(response map[string]any, key string, minimum int) []string {
	value, ok := response[key]
	if !ok {
		return []string{"missing " + key}
	}
	array, ok := value.([]any)
	if !ok {
		return []string{key + " is not an array"}
	}
	if len(array) < minimum {
		return []string{fmt.Sprintf("%s length expected >= %d got %d", key, minimum, len(array))}
	}
	return nil
}
