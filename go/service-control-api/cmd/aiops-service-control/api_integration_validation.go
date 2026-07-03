package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
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
	Name   string
	Method string
	Path   string
	Body   map[string]any
	Check  func(map[string]any) []string
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
			Name:   "apps-placement",
			Method: http.MethodPost,
			Path:   "/api/v1/apps/placement",
			Body:   map[string]any{"workload": "llm-chat-inference"},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{"valid": true})
				checks = append(checks, requirePresent(response, "selected_resource", "action", "ranked_candidates", "rejected_resources")...)
				return checks
			},
		},
		{
			Name:   "apps-deployment-plan",
			Method: http.MethodPost,
			Path:   "/api/v1/apps/deployment-plan",
			Body:   map[string]any{"workload": "llm-chat-inference"},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{"valid": true})
				checks = append(checks, requirePresent(response, "selected_resource", "deployment_plan")...)
				return checks
			},
		},
		{
			Name:   "service-operations-run",
			Method: http.MethodPost,
			Path:   "/api/v1/service-operations/run",
			Body: map[string]any{
				"llm_policy":          "quality_first",
				"workload":            "llm-chat-inference",
				"recovery_namespace":  "aiops-demo",
				"recovery_deployment": "aiops-service",
				"mode":                "mock",
				"guard_backend":       "go",
			},
			Check: func(response map[string]any) []string {
				checks := requireValues(response, map[string]any{
					"valid":                 true,
					"deployment_dry_run":    response["deployment_dry_run"],
					"kubernetes_live_apply": false,
				})
				checks = append(checks, requirePresent(response,
					"benchmark_status",
					"selected_resource",
					"deployment_plan",
					"deployment_manifest",
					"deployment_dry_run",
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

func callAndValidateEndpoint(client *http.Client, baseURL string, outputDir string, index int, spec apiEndpointSpec) apiEndpointValidation {
	result := apiEndpointValidation{
		Name:   spec.Name,
		Method: spec.Method,
		Path:   spec.Path,
		Checks: []string{},
	}
	var requestBody io.Reader
	if spec.Body != nil {
		bodyBytes, err := json.Marshal(spec.Body)
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
	if spec.Body != nil {
		request.Header.Set("content-type", "application/json")
	}
	response, err := client.Do(request)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	defer response.Body.Close()
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
