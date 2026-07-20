package appdeploy

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClientUsesAppDeployDeploymentContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("content-type", "application/json")
		switch {
		case request.Method == http.MethodPost && request.URL.Path == "/api/v1/deployments":
			content, err := io.ReadAll(request.Body)
			if err != nil {
				t.Fatalf("read create request: %v", err)
			}
			if strings.Contains(string(content), "runtime_profile_id") {
				t.Fatalf("removed runtime_profile_id must not be sent: %s", content)
			}
			var envelope map[string]json.RawMessage
			if err := json.Unmarshal(content, &envelope); err != nil {
				t.Fatalf("decode create request envelope: %v", err)
			}
			if len(envelope) != 1 || envelope["manifest"] == nil {
				t.Fatalf("planner handoff must contain only manifest: %s", content)
			}
			var body DeploymentCreateRequest
			if err := json.Unmarshal(content, &body); err != nil {
				t.Fatalf("decode create request: %v", err)
			}
			if body.Manifest.Spec.AppVersionID != "appver-test" {
				t.Fatalf("unexpected manifest: %#v", body.Manifest)
			}
			if body.Manifest.Spec.TargetProfileID != "" {
				t.Fatalf("target selection must remain with AppDeploy: %#v", body.Manifest.Spec)
			}
			writer.WriteHeader(http.StatusAccepted)
			_, _ = writer.Write([]byte(`{"request_id":"req-1","deployment_id":"dep-1","status":"REQUESTED"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v1/deployments/dep-1":
			_, _ = writer.Write([]byte(`{"request_id":"req-2","deployment_id":"dep-1","status":"RUNNING","target_profile_id":"target-gpu-001"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v1/deployments/dep-1/logs":
			_, _ = writer.Write([]byte(`{"request_id":"req-3","deployment_id":"dep-1","items":[{"stage":"RUNNING","message":"ready"}]}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	defer server.Close()

	client, err := NewClient(server.URL+"/api/v1", server.Client())
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	manifest := validManifest()
	manifest.Spec.TargetProfileID = ""
	created, err := client.CreateDeployment(context.Background(), manifest)
	if err != nil || created.DeploymentID != "dep-1" || created.Status != "REQUESTED" {
		t.Fatalf("unexpected create result: %#v err=%v", created, err)
	}
	status, err := client.GetDeployment(context.Background(), "dep-1")
	if err != nil || status.Status != "RUNNING" || status.TargetProfileID != "target-gpu-001" {
		t.Fatalf("unexpected status result: %#v err=%v", status, err)
	}
	logs, err := client.GetDeploymentLogs(context.Background(), "dep-1")
	if err != nil || len(logs.Items) != 1 || logs.Items[0].Message != "ready" {
		t.Fatalf("unexpected logs result: %#v err=%v", logs, err)
	}
}

func TestClientDecodesRetryableAppDeployError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("content-type", "application/json")
		writer.WriteHeader(http.StatusServiceUnavailable)
		_, _ = writer.Write([]byte(`{
			"request_id":"req-error",
			"error":{"code":"AI_INFRA_API_TIMEOUT","message":"temporary failure","retryable":true}
		}`))
	}))
	defer server.Close()

	client, err := NewClient(server.URL+"/api/v1", server.Client())
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	_, err = client.CreateDeployment(context.Background(), validManifest())
	apiError, ok := err.(*APIError)
	if !ok {
		t.Fatalf("expected APIError, got %T %v", err, err)
	}
	if !apiError.Retryable || apiError.Code != "AI_INFRA_API_TIMEOUT" || apiError.RequestID != "req-error" {
		t.Fatalf("unexpected API error: %#v", apiError)
	}
	if strings.Contains(apiError.Error(), "temporary failure") {
		t.Fatalf("public client error must not expose remote raw message: %v", apiError)
	}
}

func TestClientRejectsUnsafeBaseURL(t *testing.T) {
	for _, baseURL := range []string{"", "file:///tmp/appdeploy", "https://user:pass@example.com/api/v1", "https://example.com/api/v1?token=x"} {
		if _, err := NewClient(baseURL, nil); err == nil {
			t.Fatalf("expected unsafe base URL rejection for %q", baseURL)
		}
	}
}
