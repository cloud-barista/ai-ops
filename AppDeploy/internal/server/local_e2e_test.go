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

func TestLocalRuntimeAPIEndToEnd(t *testing.T) {
	workDir := t.TempDir()
	artifactDir := filepath.Join(t.TempDir(), "original-application")
	if err := os.MkdirAll(artifactDir, 0o755); err != nil {
		t.Fatal(err)
	}
	e, err := server.NewWithConfig(config.Settings{
		ResourceProvider:    "local",
		PlacementProvider:   "local",
		LocalRuntimeWorkDir: workDir,
		CPUVMRunner:         "dry-run",
		GPUVMRunner:         "dry-run",
	})
	if err != nil {
		t.Fatal(err)
	}

	artifactURI := (&url.URL{Scheme: "file", Path: filepath.ToSlash(artifactDir)}).String()
	spec := model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata:      model.Metadata{Name: "local-api-app", Version: "0.1.0"},
		Artifact:      model.Artifact{Type: "script", URI: artifactURI},
		Entrypoint:    model.Entrypoint{Command: os.Args[0], Args: []string{"-test.run=TestLocalRuntimeChild"}},
		Runtime:       model.AppRuntime{Type: "cpu"},
		Resources:     model.Resources{CPU: "1", Memory: "128Mi", Storage: "1Gi"},
	}
	raw, err := json.Marshal(model.AppCreateRequest{AppSpec: spec})
	if err != nil {
		t.Fatal(err)
	}
	app := postRawJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", raw)
	if string(app.OriginalApplication) != string(raw) {
		t.Fatalf("original application changed: got %s want %s", app.OriginalApplication, raw)
	}

	postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID:   "local-api-node",
		CSP:               "local",
		VMType:            "Local",
		Status:            model.NodeStatusReady,
		Runtime:           model.TargetRuntime{RuntimeType: "local", OperatingMode: "local_process"},
		SupportedRuntimes: []string{"cpu"},
		Capacity:          model.ResourceCapacity{CPUCores: 2, MemoryBytes: 1 << 30, StorageBytes: 4 << 30},
	})

	t.Setenv("AI_APP_LOCAL_CHILD", "1")
	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID: app.AppVersionID,
		Requirements: &model.DeploymentRequirements{Runtime: "cpu", Resources: model.Resources{CPU: "1", Memory: "128Mi", Storage: "1Gi"}},
	})
	if deployment.Status != model.StatusRunning || deployment.Placement == nil || deployment.Placement.TargetVMID != "local-api-node" || deployment.RuntimeID == "" {
		t.Fatalf("unexpected local deployment: %+v", deployment)
	}

	status := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+deployment.DeploymentID)
	if status.Status != model.StatusRunning {
		t.Fatalf("status = %s, want RUNNING", status.Status)
	}
	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	if len(logs.Items) == 0 {
		t.Fatal("expected deployment logs")
	}

	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stop status = %s, want STOPPED", stopped.Status)
	}
	for attempt := 0; attempt < 20; attempt++ {
		logs = getJSON[struct {
			Items []model.DeploymentLog `json:"items"`
		}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
		for _, item := range logs.Items {
			if item.Component == "local-process-adapter" {
				targets := getJSON[struct {
					Items []model.TargetProfile `json:"items"`
				}](t, e, "/api/v1/target-profiles")
				if len(targets.Items) != 1 || targets.Items[0].Allocated.CPUCores != 0 {
					t.Fatalf("reservation was not released: %+v", targets.Items)
				}
				return
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("local runtime stop log was not collected")
}

func TestLocalRuntimeChild(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_CHILD") != "1" {
		return
	}
	_, _ = os.Stdout.WriteString("local runtime child started\n")
	time.Sleep(30 * time.Second)
}
