package server_test

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/server"
)

func TestLocalFaultInjectionAPIEndToEnd(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_FAULT_API_HELPER") == "1" {
		time.Sleep(30 * time.Second)
		return
	}

	artifactDir := t.TempDir()
	e, err := server.NewWithConfig(config.Settings{
		ResourceProvider:    "local",
		PlacementProvider:   "local",
		LocalRuntimeWorkDir: t.TempDir(),
		LocalFaultRate:      1,
		LocalFaultSeed:      42,
		LocalFaultMax:       1,
		LocalFaultCodes:     []string{"TRANSIENT_DEPLOYMENT_FAILURE"},
	})
	if err != nil {
		t.Fatal(err)
	}

	artifactURI := (&url.URL{Scheme: "file", Path: filepath.ToSlash(artifactDir)}).String()
	app := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{
		AppSpec: model.AppSpec{
			SchemaVersion: "appspec.khu.ai/v1alpha1",
			Kind:          "AIApp",
			Metadata:      model.Metadata{Name: "local-fault-api-app", Version: "0.1.0"},
			Artifact:      model.Artifact{Type: "script", URI: artifactURI},
			Entrypoint:    model.Entrypoint{Command: os.Args[0], Args: []string{"-test.run=TestLocalFaultInjectionAPIChild"}},
			Runtime:       model.AppRuntime{Type: "cpu"},
			Resources:     model.Resources{CPU: "1", Memory: "128Mi", Storage: "1Gi"},
		},
	})
	postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID:   "local-fault-api-node",
		CSP:               "local",
		VMType:            "Local",
		Status:            model.NodeStatusReady,
		Runtime:           model.TargetRuntime{RuntimeType: "local", OperatingMode: "local_process"},
		SupportedRuntimes: []string{"cpu"},
		Capacity:          model.ResourceCapacity{CPUCores: 2, MemoryBytes: 1 << 30, StorageBytes: 4 << 30},
	})

	deploymentRequest := model.DeploymentCreateRequest{
		AppVersionID: app.AppVersionID,
		Requirements: &model.DeploymentRequirements{
			Runtime:   "cpu",
			Resources: model.Resources{CPU: "1", Memory: "128Mi", Storage: "1Gi"},
		},
	}
	failed := request(t, e, http.MethodPost, "/api/v1/deployments", deploymentRequest)
	if failed.Code != http.StatusServiceUnavailable {
		t.Fatalf("injected deployment status = %d body=%s, want %d", failed.Code, failed.Body.String(), http.StatusServiceUnavailable)
	}
	var errorResponse model.ErrorResponse
	if err := json.Unmarshal(failed.Body.Bytes(), &errorResponse); err != nil {
		t.Fatal(err)
	}
	if errorResponse.Error.Code != "TRANSIENT_DEPLOYMENT_FAILURE" || !errorResponse.Error.Retryable {
		t.Fatalf("injected error = %+v", errorResponse.Error)
	}
	if deploymentID, ok := errorResponse.Error.Details["deployment_id"].(string); !ok || deploymentID == "" {
		t.Fatalf("injected error did not expose deployment_id: %+v", errorResponse.Error.Details)
	}

	deployments := getJSON[struct {
		Items []model.DeploymentResponse `json:"items"`
	}](t, e, "/api/v1/deployments")
	if len(deployments.Items) != 1 || deployments.Items[0].Status != model.StatusDeploymentFailed {
		t.Fatalf("failed deployment history = %+v", deployments.Items)
	}
	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployments.Items[0].DeploymentID+"/logs")
	if !hasLocalFaultLog(logs.Items, "TRANSIENT_DEPLOYMENT_FAILURE") {
		t.Fatalf("fault code was not recorded in logs: %+v", logs.Items)
	}

	t.Setenv("AI_APP_LOCAL_FAULT_API_HELPER", "1")
	recovered := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", deploymentRequest)
	if recovered.Status != model.StatusRunning {
		t.Fatalf("deployment after max fault = %+v, want RUNNING", recovered)
	}
	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+recovered.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stopped deployment = %+v, want STOPPED", stopped)
	}
	t.Logf("fault injection API E2E: first deployment failed with retryable TRANSIENT_DEPLOYMENT_FAILURE; next deployment recovered after max_faults=1")
}

func hasLocalFaultLog(logs []model.DeploymentLog, code string) bool {
	for _, item := range logs {
		if item.ErrorCode == code {
			return true
		}
	}
	return false
}

func TestLocalFaultInjectionAPIChild(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_FAULT_API_HELPER") != "1" {
		return
	}
	time.Sleep(30 * time.Second)
}
