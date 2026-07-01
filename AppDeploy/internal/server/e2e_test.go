package server_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/server"
)

func TestSwaggerDocsRoutes(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	openapi := request(t, e, http.MethodGet, "/openapi.yaml", nil)
	if openapi.Code != http.StatusOK {
		t.Fatalf("openapi status = %d body = %s", openapi.Code, openapi.Body.String())
	}
	if !strings.Contains(openapi.Body.String(), "KHU AI App Deployer API") {
		t.Fatal("openapi response does not contain API title")
	}

	swagger := request(t, e, http.MethodGet, "/swagger", nil)
	if swagger.Code != http.StatusOK {
		t.Fatalf("swagger status = %d body = %s", swagger.Code, swagger.Body.String())
	}
	if !strings.Contains(strings.ToLower(swagger.Body.String()), "<html") {
		t.Fatal("swagger response is not html")
	}
}

func TestMockRuntimeE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	appVersionID := createApp(t, e, validMockApp())
	createMockRuntimeProfile(t, e)
	createMockTargetProfile(t, e)

	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-mock-001",
		TargetProfileID:  "target-mock-001",
	})
	if deployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %s, want %s", deployment.Status, model.StatusRunning)
	}

	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	if len(logs.Items) < 6 {
		t.Fatalf("log count = %d, want at least 6", len(logs.Items))
	}
	seen := map[string]bool{}
	for _, item := range logs.Items {
		seen[item.Stage] = true
		if item.RequestID == "" && item.Component != "runtime-adapter" {
			t.Fatalf("missing request_id in log %+v", item)
		}
	}
	for _, stage := range []string{model.StatusRequested, model.StatusValidating, model.StatusValidated, model.StatusScheduling, model.StatusDeploying, model.StatusRunning} {
		if !seen[stage] {
			t.Fatalf("missing deployment stage log %s", stage)
		}
	}

	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stopped status = %s, want %s", stopped.Status, model.StatusStopped)
	}
}

func TestCPUVMRuntimeE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	appVersionID := createApp(t, e, validCPUApp())
	createCPURuntimeProfile(t, e)
	createCPUTargetProfile(t, e)

	check := postJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{
		TargetProfileID: "target-cpu-001",
	})
	if check.Status != "available" {
		t.Fatalf("resource check status = %s, want available", check.Status)
	}

	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-cpu-001",
		TargetProfileID:  "target-cpu-001",
	})
	if deployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %s, want %s", deployment.Status, model.StatusRunning)
	}

	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	var sawCPUAdapter bool
	for _, item := range logs.Items {
		if item.Component == "cpu-vm-adapter" {
			sawCPUAdapter = true
		}
	}
	if !sawCPUAdapter {
		t.Fatal("missing cpu-vm-adapter log")
	}

	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stopped status = %s, want %s", stopped.Status, model.StatusStopped)
	}
}

func TestGPUVMRuntimeE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	appVersionID := createApp(t, e, validGPUApp())
	createGPURuntimeProfile(t, e)
	createGPUTargetProfile(t, e)

	check := postJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{
		TargetProfileID: "target-gpu-001",
	})
	if check.Status != "available" {
		t.Fatalf("resource check status = %s, want available; checks=%v", check.Status, check.Checks)
	}

	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-gpu-001",
		TargetProfileID:  "target-gpu-001",
	})
	if deployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %s, want %s", deployment.Status, model.StatusRunning)
	}

	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	var sawGPUAdapter bool
	for _, item := range logs.Items {
		if item.Component == "gpu-vm-adapter" {
			sawGPUAdapter = true
		}
	}
	if !sawGPUAdapter {
		t.Fatal("missing gpu-vm-adapter log")
	}

	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stopped status = %s, want %s", stopped.Status, model.StatusStopped)
	}
}

func TestAIInfraRuntimeSkeletonE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	appVersionID := createApp(t, e, validAIInfraApp())
	createAIInfraRuntimeProfile(t, e)
	createAIInfraTargetProfile(t, e)

	check := postJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{
		TargetProfileID: "target-aiinfra-001",
	})
	if check.Status != "available" {
		t.Fatalf("resource check status = %s, want available; checks=%v", check.Status, check.Checks)
	}

	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-aiinfra-001",
		TargetProfileID:  "target-aiinfra-001",
	})
	if deployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %s, want %s", deployment.Status, model.StatusRunning)
	}

	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	var sawAIInfraClient bool
	for _, item := range logs.Items {
		if item.Component == "etri-aiinfra-client" {
			sawAIInfraClient = true
		}
	}
	if !sawAIInfraClient {
		t.Fatal("missing etri-aiinfra-client log")
	}

	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stopped status = %s, want %s", stopped.Status, model.StatusStopped)
	}
}

func TestMonitoringSummaryE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	appVersionID := createApp(t, e, validCPUApp())
	createCPURuntimeProfile(t, e)
	createCPUTargetProfile(t, e)

	postJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{
		TargetProfileID: "target-cpu-001",
	})
	postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-cpu-001",
		TargetProfileID:  "target-cpu-001",
	})

	failed := request(t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     "missing-app-version",
		RuntimeProfileID: "rt-cpu-001",
		TargetProfileID:  "target-cpu-001",
	})
	if failed.Code != http.StatusBadRequest {
		t.Fatalf("failed deployment status = %d, want %d", failed.Code, http.StatusBadRequest)
	}

	summary := getJSON[model.MonitoringSummaryResponse](t, e, "/api/v1/monitoring/summary")
	if summary.Status != "degraded" {
		t.Fatalf("monitoring status = %s, want degraded", summary.Status)
	}
	if summary.Deployments.Total != 2 {
		t.Fatalf("deployment total = %d, want 2", summary.Deployments.Total)
	}
	if summary.Deployments.ByStatus[model.StatusRunning] != 1 || summary.Deployments.ByStatus[model.StatusValidationFailed] != 1 {
		t.Fatalf("unexpected deployment status counts: %+v", summary.Deployments.ByStatus)
	}
	if len(summary.RuntimeHealth) != 1 || summary.RuntimeHealth[0].TargetProfileID != "target-cpu-001" {
		t.Fatalf("unexpected runtime health: %+v", summary.RuntimeHealth)
	}
	if !hasAlarm(summary.Alarms, model.ErrAppSpecInvalid) {
		t.Fatalf("missing %s alarm in %+v", model.ErrAppSpecInvalid, summary.Alarms)
	}

	runtimeHealth := getJSON[struct {
		Items []model.RuntimeHealthSnapshot `json:"items"`
	}](t, e, "/api/v1/monitoring/runtime-health")
	if len(runtimeHealth.Items) != 1 {
		t.Fatalf("runtime health item count = %d, want 1", len(runtimeHealth.Items))
	}

	alarms := getJSON[struct {
		Items []model.DeploymentAlarmSummary `json:"items"`
	}](t, e, "/api/v1/monitoring/alarms")
	if !hasAlarm(alarms.Items, model.ErrAppSpecInvalid) {
		t.Fatalf("missing %s alarm in %+v", model.ErrAppSpecInvalid, alarms.Items)
	}
}

func TestInferenceMetricsE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	appVersionID := createApp(t, e, validCPUApp())
	createCPURuntimeProfile(t, e)
	createCPUTargetProfile(t, e)
	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-cpu-001",
		TargetProfileID:  "target-cpu-001",
	})

	metric := postJSON[model.InferenceMetricRecord](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/metrics", model.InferenceMetricCreateRequest{
		LatencyMS:     42.5,
		ThroughputRPS: 12.25,
		QualityScore:  0.97,
		RequestCount:  100,
		ErrorCount:    1,
		Metadata: map[string]any{
			"model": "sample-cpu-model",
		},
	})
	if metric.MetricID == "" || metric.DeploymentID != deployment.DeploymentID {
		t.Fatalf("unexpected metric response: %+v", metric)
	}
	if metric.LatencyMS != 42.5 || metric.ThroughputRPS != 12.25 || metric.QualityScore != 0.97 {
		t.Fatalf("unexpected metric values: %+v", metric)
	}

	deploymentMetrics := getJSON[struct {
		Items []model.InferenceMetricRecord `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/metrics")
	if len(deploymentMetrics.Items) != 1 {
		t.Fatalf("deployment metric count = %d, want 1", len(deploymentMetrics.Items))
	}

	allMetrics := getJSON[struct {
		Items []model.InferenceMetricRecord `json:"items"`
	}](t, e, "/api/v1/monitoring/metrics")
	if len(allMetrics.Items) != 1 {
		t.Fatalf("monitoring metric count = %d, want 1", len(allMetrics.Items))
	}
}

func TestExternalInterfaceExamplesE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()

	cpuApp := postRawJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", readInterfaceRequest(t, "app-create-cpu.json"))
	if cpuApp.RequestID == "" || cpuApp.AppVersionID == "" {
		t.Fatalf("unexpected cpu app response: %+v", cpuApp)
	}

	gpuApp := postRawJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", readInterfaceRequest(t, "app-create-gpu.json"))
	if gpuApp.RequestID == "" || gpuApp.AppVersionID == "" {
		t.Fatalf("unexpected gpu app response: %+v", gpuApp)
	}

	invalid := requestRaw(t, e, http.MethodPost, "/api/v1/apps", readInterfaceRequest(t, "app-create-invalid-container.json"))
	if invalid.Code != http.StatusBadRequest {
		t.Fatalf("invalid app status = %d body = %s", invalid.Code, invalid.Body.String())
	}
	var invalidResp model.ErrorResponse
	if err := json.Unmarshal(invalid.Body.Bytes(), &invalidResp); err != nil {
		t.Fatal(err)
	}
	if invalidResp.RequestID == "" || invalidResp.Error.Code != model.ErrAppSpecInvalid {
		t.Fatalf("unexpected invalid app response: %+v", invalidResp)
	}

	runtimeProfile := postRawJSON[map[string]any](t, e, http.MethodPost, "/api/v1/runtime-profiles", readInterfaceRequest(t, "runtime-profile-gpu-vm.json"))
	if runtimeProfile["request_id"] == "" || runtimeProfile["profile_id"] != "runtime-gpu-vm-001" {
		t.Fatalf("unexpected runtime profile response: %+v", runtimeProfile)
	}

	targetProfile := postRawJSON[map[string]any](t, e, http.MethodPost, "/api/v1/target-profiles", readInterfaceRequest(t, "target-profile-aws-gpu.json"))
	if targetProfile["request_id"] == "" || targetProfile["profile_id"] != "target-aws-gpu-001" {
		t.Fatalf("unexpected target profile response: %+v", targetProfile)
	}

	resourceCheck := postRawJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", readInterfaceRequest(t, "resource-check-gpu.json"))
	if resourceCheck.RequestID == "" || resourceCheck.RuntimeProfileID != "runtime-gpu-vm-001" || resourceCheck.Status != "available" {
		t.Fatalf("unexpected resource check response: %+v", resourceCheck)
	}

	deploymentBody := decodeJSON[map[string]any](t, readInterfaceRequest(t, "deployment-create-gpu.json"))
	deploymentBody["app_version_id"] = gpuApp.AppVersionID
	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", deploymentBody)
	if deployment.RequestID == "" || deployment.DeploymentID == "" || deployment.AppID != gpuApp.AppID || deployment.Status != model.StatusRunning {
		t.Fatalf("unexpected deployment response: %+v", deployment)
	}

	logs := getJSON[struct {
		RequestID    string                `json:"request_id"`
		DeploymentID string                `json:"deployment_id"`
		Items        []model.DeploymentLog `json:"items"`
		Logs         []model.DeploymentLog `json:"logs"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	if logs.RequestID == "" || logs.DeploymentID != deployment.DeploymentID || len(logs.Items) == 0 || len(logs.Logs) == 0 {
		t.Fatalf("unexpected logs response: %+v", logs)
	}

	metric := postJSON[model.InferenceMetricRecord](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/metrics", model.InferenceMetricCreateRequest{
		LatencyMS:     12.5,
		ThroughputRPS: 4.5,
		QualityScore:  0.91,
		RequestCount:  20,
	})
	if metric.RequestID == "" || metric.MetricID == "" {
		t.Fatalf("unexpected metric response: %+v", metric)
	}

	stopped := postRawJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", readInterfaceRequest(t, "deployment-stop.json"))
	if stopped.RequestID == "" || stopped.Status != model.StatusStopped {
		t.Fatalf("unexpected stop response: %+v", stopped)
	}
}

func TestContainerArtifactRejected(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := server.New()
	body := model.AppCreateRequest{AppSpec: validMockApp()}
	body.AppSpec.Artifact.Type = "container"
	rec := request(t, e, http.MethodPost, "/api/v1/apps", body)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
	}
	var errResp model.ErrorResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &errResp); err != nil {
		t.Fatal(err)
	}
	if errResp.Error.Code != model.ErrAppSpecInvalid {
		t.Fatalf("error code = %s, want %s", errResp.Error.Code, model.ErrAppSpecInvalid)
	}
}

func hasAlarm(items []model.DeploymentAlarmSummary, code string) bool {
	for _, item := range items {
		if item.ErrorCode == code {
			return true
		}
	}
	return false
}

func createApp(t *testing.T, e http.Handler, spec model.AppSpec) string {
	t.Helper()
	resp := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: spec})
	if resp.AppVersionID == "" {
		t.Fatal("empty app_version_id")
	}
	return resp.AppVersionID
}

func createMockRuntimeProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.RuntimeProfile](t, e, http.MethodPost, "/api/v1/runtime-profiles", model.RuntimeProfile{
		RuntimeProfileID: "rt-mock-001",
		Name:             "mock-runtime",
		RuntimeType:      "mock",
		Accelerator:      "none",
		AdapterType:      "mock",
		OperatingMode:    "local_mock",
	})
	if resp.RuntimeProfileID != "rt-mock-001" {
		t.Fatalf("runtime profile id = %s", resp.RuntimeProfileID)
	}
}

func createMockTargetProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID: "target-mock-001",
		Name:            "mock-target",
		CSP:             "mock",
		VM:              model.VMProfile{},
		Runtime: model.TargetRuntime{
			RuntimeType:   "mock",
			Accelerator:   "none",
			OperatingMode: "local_mock",
		},
	})
	if resp.TargetProfileID != "target-mock-001" {
		t.Fatalf("target profile id = %s", resp.TargetProfileID)
	}
}

func createCPURuntimeProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.RuntimeProfile](t, e, http.MethodPost, "/api/v1/runtime-profiles", model.RuntimeProfile{
		RuntimeProfileID: "rt-cpu-001",
		Name:             "cpu-vm-runtime",
		RuntimeType:      "cpu",
		Accelerator:      "none",
		AdapterType:      "cpu_vm",
		OperatingMode:    "vm_process",
	})
	if resp.RuntimeProfileID != "rt-cpu-001" {
		t.Fatalf("runtime profile id = %s", resp.RuntimeProfileID)
	}
}

func createCPUTargetProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID: "target-cpu-001",
		Name:            "cpu-vm-target",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "cpu-vm.example.internal",
			SSHPort:       22,
			CredentialRef: "cred://local/cpu-vm-001",
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   "cpu",
			Accelerator:   "none",
			OperatingMode: "vm_process",
		},
		Storage: &model.Storage{
			ArtifactDir: "/opt/aiapp/artifacts",
			ModelDir:    "/opt/aiapp/models",
			LogDir:      "/var/log/aiapp",
		},
	})
	if resp.TargetProfileID != "target-cpu-001" {
		t.Fatalf("target profile id = %s", resp.TargetProfileID)
	}
}

func createGPURuntimeProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.RuntimeProfile](t, e, http.MethodPost, "/api/v1/runtime-profiles", model.RuntimeProfile{
		RuntimeProfileID: "rt-gpu-001",
		Name:             "gpu-vm-runtime",
		RuntimeType:      "gpu",
		Accelerator:      "nvidia",
		AdapterType:      "gpu_vm",
		OperatingMode:    "vm_process",
	})
	if resp.RuntimeProfileID != "rt-gpu-001" {
		t.Fatalf("runtime profile id = %s", resp.RuntimeProfileID)
	}
}

func createGPUTargetProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID: "target-gpu-001",
		Name:            "gpu-vm-target",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "gpu-vm.example.internal",
			SSHPort:       22,
			CredentialRef: "cred://local/gpu-vm-001",
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   "gpu",
			Accelerator:   "nvidia",
			OperatingMode: "vm_process",
		},
		GPU: &model.GPUProfile{
			Vendor:         "nvidia",
			Count:          1,
			DriverRequired: true,
		},
		Storage: &model.Storage{
			ArtifactDir: "/opt/aiapp/artifacts",
			ModelDir:    "/opt/aiapp/models",
			LogDir:      "/var/log/aiapp",
		},
	})
	if resp.TargetProfileID != "target-gpu-001" {
		t.Fatalf("target profile id = %s", resp.TargetProfileID)
	}
}

func createAIInfraRuntimeProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.RuntimeProfile](t, e, http.MethodPost, "/api/v1/runtime-profiles", model.RuntimeProfile{
		RuntimeProfileID: "rt-aiinfra-001",
		Name:             "etri-aiinfra-runtime",
		RuntimeType:      "aiinfra",
		Accelerator:      "none",
		AdapterType:      "etri_aiinfra",
		OperatingMode:    "remote_api",
	})
	if resp.RuntimeProfileID != "rt-aiinfra-001" {
		t.Fatalf("runtime profile id = %s", resp.RuntimeProfileID)
	}
}

func createAIInfraTargetProfile(t *testing.T, e http.Handler) {
	t.Helper()
	resp := postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID: "target-aiinfra-001",
		Name:            "etri-aiinfra-target",
		CSP:             "etri",
		VM: model.VMProfile{
			Host: "aiinfra-gateway.example.internal",
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   "aiinfra",
			Accelerator:   "none",
			OperatingMode: "remote_api",
		},
	})
	if resp.TargetProfileID != "target-aiinfra-001" {
		t.Fatalf("target profile id = %s", resp.TargetProfileID)
	}
}

func validMockApp() model.AppSpec {
	return model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:    "sample-mock-app",
			Version: "0.1.0",
		},
		Artifact: model.Artifact{
			Type: "script",
			URI:  "file:///opt/examples/sample-mock-app/run.sh",
		},
		Entrypoint: model.Entrypoint{
			Command: "bash",
			Args:    []string{"run.sh"},
		},
		Runtime: model.AppRuntime{
			Type:        "mock",
			Accelerator: "none",
		},
		Resources: model.Resources{
			CPU:     "1",
			Memory:  "1Gi",
			GPU:     "0",
			Storage: "1Gi",
		},
	}
}

func validCPUApp() model.AppSpec {
	return model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:    "sample-cpu-app",
			Version: "0.1.0",
		},
		Artifact: model.Artifact{
			Type: "script",
			URI:  "file:///opt/examples/sample-cpu-app/run.sh",
		},
		Entrypoint: model.Entrypoint{
			Command: "bash",
			Args:    []string{"run.sh", "--port=18080"},
		},
		Runtime: model.AppRuntime{
			Type:        "cpu",
			Accelerator: "none",
		},
		Resources: model.Resources{
			CPU:     "2",
			Memory:  "4Gi",
			GPU:     "0",
			Storage: "5Gi",
		},
		Network: &model.Network{
			Ports: []model.Port{
				{Name: "http", AppPort: 18080, Protocol: "TCP"},
			},
		},
		Healthcheck: &model.Healthcheck{
			Type: "http",
			Path: "/health",
		},
	}
}

func validGPUApp() model.AppSpec {
	return model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:    "sample-gpu-app",
			Version: "0.1.0",
		},
		Artifact: model.Artifact{
			Type: "script",
			URI:  "file:///opt/examples/sample-gpu-app/run.sh",
		},
		Entrypoint: model.Entrypoint{
			Command: "bash",
			Args:    []string{"run.sh", "--port=18081"},
		},
		Runtime: model.AppRuntime{
			Type:        "gpu",
			Accelerator: "nvidia",
		},
		Resources: model.Resources{
			CPU:     "4",
			Memory:  "16Gi",
			GPU:     "1",
			Storage: "20Gi",
		},
		Network: &model.Network{
			Ports: []model.Port{
				{Name: "http", AppPort: 18081, Protocol: "TCP"},
			},
		},
		Healthcheck: &model.Healthcheck{
			Type: "http",
			Path: "/health",
		},
	}
}

func validAIInfraApp() model.AppSpec {
	return model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:    "sample-aiinfra-app",
			Version: "0.1.0",
		},
		Artifact: model.Artifact{
			Type: "package",
			URI:  "aiinfra://catalog/sample-aiinfra-app/0.1.0",
		},
		Entrypoint: model.Entrypoint{
			Command: "serve",
		},
		Runtime: model.AppRuntime{
			Type:        "aiinfra",
			Accelerator: "none",
		},
		Resources: model.Resources{
			CPU:     "2",
			Memory:  "4Gi",
			GPU:     "0",
			Storage: "5Gi",
		},
	}
}

func postJSON[T any](t *testing.T, e http.Handler, method, path string, body any) T {
	t.Helper()
	rec := request(t, e, method, path, body)
	if rec.Code < 200 || rec.Code >= 300 {
		t.Fatalf("%s %s status = %d body = %s", method, path, rec.Code, rec.Body.String())
	}
	var out T
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode response: %v body=%s", err, rec.Body.String())
	}
	return out
}

func getJSON[T any](t *testing.T, e http.Handler, path string) T {
	t.Helper()
	return postJSON[T](t, e, http.MethodGet, path, nil)
}

func request(t *testing.T, e http.Handler, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var raw []byte
	if body != nil {
		var err error
		raw, err = json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
	}
	req := httptest.NewRequest(method, path, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Request-ID", "req-test-001")
	rec := httptest.NewRecorder()
	e.ServeHTTP(rec, req)
	return rec
}

func readInterfaceRequest(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(projectTestFile(t, filepath.Join("examples", "interface", "requests", name)))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func postRawJSON[T any](t *testing.T, e http.Handler, method, path string, raw []byte) T {
	t.Helper()
	rec := requestRaw(t, e, method, path, raw)
	if rec.Code < 200 || rec.Code >= 300 {
		t.Fatalf("%s %s status = %d body = %s", method, path, rec.Code, rec.Body.String())
	}
	return decodeJSON[T](t, rec.Body.Bytes())
}

func decodeJSON[T any](t *testing.T, raw []byte) T {
	t.Helper()
	var out T
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode JSON: %v body=%s", err, string(raw))
	}
	return out
}

func requestRaw(t *testing.T, e http.Handler, method, path string, raw []byte) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Request-ID", "req-test-001")
	rec := httptest.NewRecorder()
	e.ServeHTTP(rec, req)
	return rec
}

func projectTestFile(t *testing.T, rel string) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		candidate := filepath.Join(wd, rel)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(wd)
		if parent == wd {
			t.Fatalf("could not find %s from %s", rel, wd)
		}
		wd = parent
	}
}
