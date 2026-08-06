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

func TestLocalFaultBurstAPIEndToEnd(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_FAULT_BURST_HELPER") == "1" {
		time.Sleep(30 * time.Second)
		return
	}

	artifactDir := t.TempDir()
	e, err := server.NewWithConfig(config.Settings{
		ResourceProvider:    "local",
		PlacementProvider:   "local",
		LocalRuntimeWorkDir: t.TempDir(),
		LocalFaultRate:      0.75,
		LocalFaultSeed:      42,
		LocalFaultMax:       8,
		LocalFaultCodes: []string{
			"GPU_OOM",
			"CUDA_MISMATCH",
			"RESOURCE_UNAVAILABLE",
			"TRANSIENT_DEPLOYMENT_FAILURE",
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	artifactURI := (&url.URL{Scheme: "file", Path: filepath.ToSlash(artifactDir)}).String()
	app := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{
		AppSpec: model.AppSpec{
			SchemaVersion: "appspec.khu.ai/v1alpha1",
			Kind:          "AIApp",
			Metadata:      model.Metadata{Name: "local-fault-burst-app", Version: "0.1.0"},
			Artifact:      model.Artifact{Type: "script", URI: artifactURI},
			Entrypoint:    model.Entrypoint{Command: os.Args[0], Args: []string{"-test.run=TestLocalFaultBurstAPIChild"}},
			Runtime:       model.AppRuntime{Type: "cpu"},
			Resources:     model.Resources{CPU: "1", Memory: "128Mi", Storage: "1Gi"},
		},
	})
	postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID:   "local-fault-burst-node",
		CSP:               "local",
		VMType:            "Local",
		Status:            model.NodeStatusReady,
		Runtime:           model.TargetRuntime{RuntimeType: "local", OperatingMode: "local_process"},
		SupportedRuntimes: []string{"cpu"},
		Capacity:          model.ResourceCapacity{CPUCores: 2, MemoryBytes: 1 << 30, StorageBytes: 4 << 30},
	})

	t.Setenv("AI_APP_LOCAL_FAULT_BURST_HELPER", "1")
	deploymentRequest := model.DeploymentCreateRequest{
		AppVersionID: app.AppVersionID,
		Requirements: &model.DeploymentRequirements{
			Runtime:   "cpu",
			Resources: model.Resources{CPU: "1", Memory: "128Mi", Storage: "1Gi"},
		},
	}
	faults := map[string]int{}
	successes := 0
	for i := 0; i < 12; i++ {
		response := request(t, e, http.MethodPost, "/api/v1/deployments", deploymentRequest)
		if response.Code >= 400 {
			var errorResponse model.ErrorResponse
			if err := json.Unmarshal(response.Body.Bytes(), &errorResponse); err != nil {
				t.Fatalf("burst deployment %d error response: %v body=%s", i, err, response.Body.String())
			}
			if !knownLocalFaultCode(errorResponse.Error.Code) {
				t.Fatalf("burst deployment %d fault code = %q", i, errorResponse.Error.Code)
			}
			faults[errorResponse.Error.Code]++
			continue
		}

		deployment := decodeJSON[model.DeploymentResponse](t, response.Body.Bytes())
		if deployment.Status != model.StatusRunning || deployment.RuntimeID == "" {
			t.Fatalf("burst deployment %d response = %+v", i, deployment)
		}
		stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
		if stopped.Status != model.StatusStopped {
			t.Fatalf("burst deployment %d stop = %+v", i, stopped)
		}
		successes++
	}

	deployments := getJSON[struct {
		Items []model.DeploymentResponse `json:"items"`
	}](t, e, "/api/v1/deployments")
	if len(faults) == 0 || successes == 0 || len(deployments.Items) != 12 {
		t.Fatalf("burst results faults=%v successes=%d deployments=%d", faults, successes, len(deployments.Items))
	}
	t.Logf("fault burst API E2E: 12 deployments, faults=%v, successes=%d, max_faults=8", faults, successes)
}

func knownLocalFaultCode(code string) bool {
	switch code {
	case "GPU_OOM", "CUDA_MISMATCH", "RESOURCE_UNAVAILABLE", "TRANSIENT_DEPLOYMENT_FAILURE":
		return true
	default:
		return false
	}
}

func TestLocalFaultBurstAPIChild(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_FAULT_BURST_HELPER") != "1" {
		return
	}
	time.Sleep(30 * time.Second)
}
