package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestControlRunAPIManifestLifecycleWithoutAppDeploy(t *testing.T) {
	provider, closeProvider := automationProvider(t, validManifestJSON("appver-001"))
	defer closeProvider()

	config := NewServerConfig()
	config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
	config.AppDeployBaseURL = ""
	server := NewServer(config)

	create := performJSONRequest(t, server, http.MethodPost, "/api/v1/control-runs", `{
		"natural_language_request":"Deploy an inference application with CPU 2 and memory 4Gi.",
		"app_version_id":"appver-001",
		"candidate_id":"decision-model",
		"requested_by":"ai-ops-geon-planner"
	}`)
	if create.Code != http.StatusCreated {
		t.Fatalf("create ControlRun: code=%d body=%s", create.Code, create.Body.String())
	}
	created := decodeObject(t, create.Body.Bytes())
	runID, _ := created["run_id"].(string)
	if runID == "" || created["status"] != "MANIFEST_APPROVED" {
		t.Fatalf("unexpected create response: %#v", created)
	}

	list := performJSONRequest(t, server, http.MethodGet, "/api/v1/control-runs", "")
	if list.Code != http.StatusOK || !containsJSONValue(t, list.Body.Bytes(), runID) {
		t.Fatalf("list ControlRuns: code=%d body=%s", list.Code, list.Body.String())
	}

	detail := performJSONRequest(t, server, http.MethodGet, "/api/v1/control-runs/"+runID, "")
	if detail.Code != http.StatusOK || !containsJSONValue(t, detail.Body.Bytes(), "manifest_guard") {
		t.Fatalf("get ControlRun: code=%d body=%s", detail.Code, detail.Body.String())
	}

	deleted := performJSONRequest(t, server, http.MethodDelete, "/api/v1/control-runs/"+runID, "")
	if deleted.Code != http.StatusOK || !containsJSONValue(t, deleted.Body.Bytes(), runID) {
		t.Fatalf("delete ControlRun: code=%d body=%s", deleted.Code, deleted.Body.String())
	}
	missing := performJSONRequest(t, server, http.MethodGet, "/api/v1/control-runs/"+runID, "")
	if missing.Code != http.StatusNotFound {
		t.Fatalf("expected deleted Run to be missing: code=%d body=%s", missing.Code, missing.Body.String())
	}
}

func TestControlRunAPIMapsGuardRejections(t *testing.T) {
	tests := []struct {
		name       string
		configure  func(*testing.T, *ServerConfig) func()
		request    string
		wantCode   int
		wantStatus string
	}{
		{
			name: "request guard",
			configure: func(t *testing.T, config *ServerConfig) func() {
				t.Helper()
				provider, closeProvider := automationProvider(t, validManifestJSON("appver-001"))
				config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
				return closeProvider
			},
			request: `{
				"natural_language_request":"Deploy this to Kubernetes with kubectl.",
				"app_version_id":"appver-001",
				"candidate_id":"decision-model",
				"requested_by":"ai-ops-geon-planner"
			}`,
			wantCode:   http.StatusBadRequest,
			wantStatus: "REQUEST_REJECTED",
		},
		{
			name: "agent registry",
			configure: func(t *testing.T, config *ServerConfig) func() {
				t.Helper()
				provider, closeProvider := automationProvider(t, validManifestJSON("appver-001"))
				config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
				config.RepoRoot = writePlannerRegistryRoot(t, AgentProfile{
					Name:           "AIApplicationAutomationAgent",
					Enabled:        false,
					Capabilities:   []string{capabilityDeploymentManifestPlanning},
					BoundedActions: []string{actionGenerateDeploymentManifest},
				})
				return closeProvider
			},
			request: `{
				"natural_language_request":"Deploy an inference application.",
				"app_version_id":"appver-001",
				"candidate_id":"decision-model",
				"requested_by":"ai-ops-geon-planner"
			}`,
			wantCode:   http.StatusForbidden,
			wantStatus: "AGENT_REJECTED",
		},
		{
			name: "manifest guard",
			configure: func(t *testing.T, config *ServerConfig) func() {
				t.Helper()
				provider, closeProvider := automationProvider(t, `{
					"schema_version":"deployment.khu.ai/v1alpha1",
					"kind":"DeploymentManifest",
					"spec":{
						"app_version_id":"appver-001",
						"accelerator":"none",
						"resources":{"cpu":"2","memory":"4Gi","gpu":"1","storage":"10Gi"}
					}
				}`)
				config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
				return closeProvider
			},
			request: `{
				"natural_language_request":"Deploy an inference application.",
				"app_version_id":"appver-001",
				"candidate_id":"decision-model",
				"requested_by":"ai-ops-geon-planner"
			}`,
			wantCode:   http.StatusUnprocessableEntity,
			wantStatus: "MANIFEST_REJECTED",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := NewServerConfig()
			closeProvider := test.configure(t, &config)
			defer closeProvider()
			server := NewServer(config)

			response := performJSONRequest(t, server, http.MethodPost, "/api/v1/control-runs", test.request)
			if response.Code != test.wantCode {
				t.Fatalf("unexpected code=%d body=%s", response.Code, response.Body.String())
			}
			result := decodeObject(t, response.Body.Bytes())
			if result["status"] != test.wantStatus || result["run_id"] == "" {
				t.Fatalf("Guard evidence was not preserved: %#v", result)
			}
		})
	}
}

func TestControlRunAPIClearAll(t *testing.T) {
	provider, closeProvider := automationProvider(t, validManifestJSON("appver-001"))
	defer closeProvider()
	config := NewServerConfig()
	config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
	server := NewServer(config)

	for index := 0; index < 2; index++ {
		response := performJSONRequest(t, server, http.MethodPost, "/api/v1/control-runs", fmt.Sprintf(`{
			"natural_language_request":"Deploy inference application %d.",
			"app_version_id":"appver-001",
			"candidate_id":"decision-model",
			"requested_by":"ai-ops-geon-planner"
		}`, index))
		if response.Code != http.StatusCreated {
			t.Fatalf("create ControlRun %d: code=%d body=%s", index, response.Code, response.Body.String())
		}
	}

	cleared := performJSONRequest(t, server, http.MethodDelete, "/api/v1/control-runs", "")
	if cleared.Code != http.StatusOK || !containsJSONValue(t, cleared.Body.Bytes(), float64(2)) {
		t.Fatalf("clear ControlRuns: code=%d body=%s", cleared.Code, cleared.Body.String())
	}
}

func validManifestJSON(appVersionID string) string {
	return fmt.Sprintf(`{
		"schema_version":"deployment.khu.ai/v1alpha1",
		"kind":"DeploymentManifest",
		"spec":{
			"app_version_id":%q,
			"accelerator":"none",
			"resources":{"cpu":"2","memory":"4Gi","gpu":"0","storage":"10Gi"}
		}
	}`, appVersionID)
}

func containsJSONValue(t *testing.T, content []byte, target any) bool {
	t.Helper()
	var value any
	if err := json.Unmarshal(content, &value); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	return walkJSONValue(value, target)
}

func walkJSONValue(value any, target any) bool {
	if value == target {
		return true
	}
	switch typed := value.(type) {
	case map[string]any:
		for _, child := range typed {
			if walkJSONValue(child, target) {
				return true
			}
		}
	case []any:
		for _, child := range typed {
			if walkJSONValue(child, target) {
				return true
			}
		}
	}
	return false
}
