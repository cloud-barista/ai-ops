package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/server"
)

func TestTokenizePreservesQuotedWindowsPath(t *testing.T) {
	args, err := tokenize(`apps add "C:\work folder\app.json"`)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"apps", "add", `C:\work folder\app.json`}
	if len(args) != len(want) {
		t.Fatalf("args = %#v", args)
	}
	for index := range want {
		if args[index] != want[index] {
			t.Fatalf("args[%d] = %q, want %q", index, args[index], want[index])
		}
	}
}

func TestInteractiveShellStatusAndExit(t *testing.T) {
	api := newCLIAPI(t)
	input := strings.NewReader("status\nexit\n")
	var output bytes.Buffer
	shell := New(api, input, &output)
	if err := shell.Run(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	text := output.String()
	for _, expected := range []string{"AI APP DEPLOYER", "현재 상태", "READY", "CLI를 종료합니다."} {
		if !strings.Contains(text, expected) {
			t.Fatalf("output does not contain %q:\n%s", expected, text)
		}
	}
}

func TestBufferedOutputDoesNotContainANSISequences(t *testing.T) {
	api := newCLIAPI(t)
	var output bytes.Buffer
	shell := New(api, strings.NewReader("exit\n"), &output)
	if err := shell.Run(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), "\033[") {
		t.Fatalf("buffered output contains ANSI escape sequences:\n%s", output.String())
	}
}

func TestColorizedInteractiveOutputWhenEnabled(t *testing.T) {
	api := newCLIAPI(t)
	var output bytes.Buffer
	shell := New(api, strings.NewReader("help\nexit\n"), &output)
	shell.color = true
	if err := shell.Run(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	text := output.String()
	if !strings.Contains(text, "\033[") {
		t.Fatal("interactive color output does not contain ANSI sequences")
	}
	if !strings.Contains(text, "현재 상태") || !strings.Contains(text, "등록 및 실행 환경") {
		t.Fatalf("interactive overview or grouped help is missing:\n%s", text)
	}
	if !strings.Contains(text, "packages build") || !strings.Contains(text, "deploy package") {
		t.Fatalf("package commands are missing from help:\n%s", text)
	}
}

func TestPackageBuildPresetUsesJSONRequest(t *testing.T) {
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/artifacts/packages" || r.Method != http.MethodPost {
			t.Fatalf("request = %s %s", r.Method, r.URL.Path)
		}
		if contentType := r.Header.Get("Content-Type"); !strings.HasPrefix(contentType, "application/json") {
			t.Fatalf("Content-Type = %q", contentType)
		}
		var request model.PackageBuildRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.Preset != "aiops-geon-service-control" || request.AppVersion != "cli-1" || request.ServicePort != 18089 {
			t.Fatalf("unexpected request: %+v", request)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		if err := json.NewEncoder(w).Encode(packageResponseForTest("aiops-geon-service-control")); err != nil {
			t.Fatal(err)
		}
	})

	output := runCommand(t, api, "packages", "build", "--type", "aiops-geon-service-control", "--version", "cli-1", "--port", "18089")
	var result model.PackageBuildResponse
	if err := json.Unmarshal([]byte(output), &result); err != nil {
		t.Fatalf("decode package output: %v\n%s", err, output)
	}
	if result.PackageType != "aiops-geon-service-control" {
		t.Fatalf("package type = %q", result.PackageType)
	}
}

func TestPackageBuildUploadUsesMultipartRequest(t *testing.T) {
	dir := t.TempDir()
	sourceDir := filepath.Join(dir, "source folder")
	if err := os.MkdirAll(sourceDir, 0o755); err != nil {
		t.Fatal(err)
	}
	sourcePath := filepath.Join(sourceDir, "run.sh")
	if err := os.WriteFile(sourcePath, []byte("#!/usr/bin/env bash\necho ok\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if contentType := r.Header.Get("Content-Type"); !strings.HasPrefix(contentType, "multipart/form-data;") {
			t.Fatalf("Content-Type = %q", contentType)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Fatal(err)
		}
		for field, expected := range map[string]string{
			"package_type":     "script",
			"app_name":         "cli-upload",
			"app_version":      "1.2.3",
			"entrypoint":       "run.sh",
			"runtime_type":     "gpu",
			"service_port":     "18080",
			"healthcheck_path": "/ready",
		} {
			if actual := r.FormValue(field); actual != expected {
				t.Fatalf("%s = %q, want %q", field, actual, expected)
			}
		}
		file, header, err := r.FormFile("source")
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()
		if header.Filename != "run.sh" {
			t.Fatalf("source filename = %q", header.Filename)
		}
		raw, err := io.ReadAll(file)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(raw), "echo ok") {
			t.Fatalf("source body = %q", raw)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		if err := json.NewEncoder(w).Encode(packageResponseForTest("script")); err != nil {
			t.Fatal(err)
		}
	})

	output := runCommand(t, api, "packages", "build",
		"--type", "script",
		"--source", sourcePath,
		"--name", "cli-upload",
		"--version", "1.2.3",
		"--entrypoint", "run.sh",
		"--runtime", "gpu",
		"--port", "18080",
		"--health-path", "/ready",
	)
	if !strings.Contains(output, `"package_type": "script"`) {
		t.Fatalf("package output:\n%s", output)
	}
}

func TestPackageBuildRejectsSourceOverConfiguredLimitBeforeAPI(t *testing.T) {
	sourcePath := filepath.Join(t.TempDir(), "run.sh")
	if err := os.WriteFile(sourcePath, []byte("12345"), 0o600); err != nil {
		t.Fatal(err)
	}
	called := false
	api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	})
	var output bytes.Buffer
	shell := NewWithPackageLimit(api, strings.NewReader(""), &output, 4)
	err := shell.Run(context.Background(), []string{"packages", "build", "--type", "script", "--source", sourcePath})
	if err == nil || !strings.Contains(err.Error(), "최대 4 bytes") {
		t.Fatalf("error = %v", err)
	}
	if called {
		t.Fatal("API was called for an oversized source")
	}
}

func TestPackageDeployBuildsRegistersChecksAndDeploys(t *testing.T) {
	api := newCLIAPI(t)
	runCommand(t, api, "runtimes", "add", writeJSON(t, t.TempDir(), "runtime.json", model.RuntimeProfile{
		RuntimeProfileID: "rt-cli-package-cpu",
		Name:             "CLI package CPU",
		RuntimeType:      "cpu",
		Accelerator:      "none",
		AdapterType:      "cpu_vm",
		OperatingMode:    "vm_process",
	}))
	runCommand(t, api, "targets", "add", writeJSON(t, t.TempDir(), "target.json", model.TargetProfile{
		TargetProfileID: "target-cli-package-cpu",
		Name:            "CLI package CPU target",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "cpu-vm.example.internal",
			SSHPort:       22,
			CredentialRef: "cred://local/cli-package-cpu",
		},
		Runtime: model.TargetRuntime{RuntimeType: "cpu", Accelerator: "none", OperatingMode: "vm_process"},
		Storage: &model.Storage{
			ArtifactDir: "/tmp/aiapp/artifacts",
			ModelDir:    "/tmp/aiapp/models",
			LogDir:      "/tmp/aiapp/logs",
		},
	}))

	sourcePath := filepath.Join(t.TempDir(), "run.sh")
	if err := os.WriteFile(sourcePath, []byte("#!/usr/bin/env bash\necho cli-package\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	output := runCommand(t, api, "deploy", "package",
		"--type", "script",
		"--source", sourcePath,
		"--name", "cli-package-app",
		"--version", "0.1.0",
		"--entrypoint", "run.sh",
		"--runtime", "cpu",
		"--runtime-id", "rt-cli-package-cpu",
		"--target-id", "target-cli-package-cpu",
	)
	var result packageDeployOutput
	if err := json.Unmarshal([]byte(output), &result); err != nil {
		t.Fatalf("decode package deploy output: %v\n%s", err, output)
	}
	if result.Package.PackageType != "script" || result.App.AppVersionID == "" {
		t.Fatalf("unexpected package/App result: %+v", result)
	}
	if result.ResourceCheck.Status != "available" {
		t.Fatalf("resource status = %q", result.ResourceCheck.Status)
	}
	if result.Deployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %q, want %q", result.Deployment.Status, model.StatusRunning)
	}
	if result.App.AppSpec.Artifact.Checksum == "" || result.App.AppSpec.Artifact.Checksum != result.Package.Checksum {
		t.Fatalf("checksum was not preserved: package=%q app=%q", result.Package.Checksum, result.App.AppSpec.Artifact.Checksum)
	}
}

func TestPackageDeployStopsWhenResourceIsUnavailable(t *testing.T) {
	deploymentCalls := 0
	packageResult := packageResponseForTest("aiops-geon-service-control")
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/artifacts/packages":
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(packageResult)
		case "/api/v1/apps":
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(model.AppResponse{
				AppID:        "app-partial",
				AppVersionID: "appver-partial",
				Name:         packageResult.AppSpec.Metadata.Name,
				Version:      packageResult.AppSpec.Metadata.Version,
				AppSpec:      packageResult.AppSpec,
			})
		case "/api/v1/resources/check":
			_ = json.NewEncoder(w).Encode(model.ResourceCheckResponse{
				RuntimeProfileID: "rt-unavailable",
				TargetProfileID:  "target-unavailable",
				Status:           "unavailable",
				Checks:           map[string]string{"target": "unreachable"},
			})
		case "/api/v1/deployments":
			deploymentCalls++
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(model.DeploymentResponse{DeploymentID: "dep-should-not-exist"})
		default:
			http.NotFound(w, r)
		}
	})

	var output bytes.Buffer
	shell := New(api, strings.NewReader(""), &output)
	err := shell.Run(context.Background(), []string{
		"packages", "deploy",
		"--type", "aiops-geon-service-control",
		"--runtime-id", "rt-unavailable",
		"--target-id", "target-unavailable",
	})
	if err == nil {
		t.Fatal("expected unavailable resource error")
	}
	for _, expected := range []string{"appver-partial", "cli-package.tar.gz", "unavailable", "Deployment는 생성하지 않았습니다"} {
		if !strings.Contains(err.Error(), expected) {
			t.Fatalf("error does not contain %q: %v", expected, err)
		}
	}
	if deploymentCalls != 0 {
		t.Fatalf("deployment endpoint called %d times", deploymentCalls)
	}
}

func TestInteractiveErrorKeepsWorkflowContext(t *testing.T) {
	var output bytes.Buffer
	shell := New(http.NotFoundHandler(), strings.NewReader(""), &output)
	shell.printError(fmt.Errorf("package archive.tar.gz 및 App appver-partial 생성 후 배포 실패: %w", &apiError{
		code:      "RESOURCE_INSUFFICIENT",
		message:   "target is unavailable",
		requestID: "req-partial",
	}))
	for _, expected := range []string{"archive.tar.gz", "appver-partial", "RESOURCE_INSUFFICIENT", "target is unavailable", "req-partial"} {
		if !strings.Contains(output.String(), expected) {
			t.Fatalf("output does not contain %q:\n%s", expected, output.String())
		}
	}
}

func TestCommandWorkflowFromRegistrationToDeployment(t *testing.T) {
	api := newCLIAPI(t)
	dir := t.TempDir()
	appFile := writeJSON(t, dir, "app.json", model.AppCreateRequest{AppSpec: validMockApp()})
	runtimeFile := writeJSON(t, dir, "runtime.json", model.RuntimeProfile{
		RuntimeProfileID: "rt-cli-mock",
		Name:             "cli-mock-runtime",
		RuntimeType:      "mock",
		Accelerator:      "none",
		AdapterType:      "mock",
		OperatingMode:    "local_mock",
	})
	targetFile := writeJSON(t, dir, "target.json", model.TargetProfile{
		TargetProfileID: "target-cli-mock",
		Name:            "cli-mock-target",
		CSP:             "mock",
		Runtime: model.TargetRuntime{
			RuntimeType:   "mock",
			Accelerator:   "none",
			OperatingMode: "local_mock",
		},
	})

	appOutput := runCommand(t, api, "apps", "add", appFile)
	var app model.AppResponse
	if err := json.Unmarshal([]byte(appOutput), &app); err != nil {
		t.Fatalf("decode app output: %v\n%s", err, appOutput)
	}
	if app.AppVersionID == "" {
		t.Fatal("app version id is empty")
	}
	runCommand(t, api, "runtimes", "add", runtimeFile)
	runCommand(t, api, "targets", "add", targetFile)
	deploymentOutput := runCommand(t, api, "deployments", "create", app.AppVersionID, "rt-cli-mock", "target-cli-mock")
	var deployment model.DeploymentResponse
	if err := json.Unmarshal([]byte(deploymentOutput), &deployment); err != nil {
		t.Fatalf("decode deployment output: %v\n%s", err, deploymentOutput)
	}
	if deployment.Status != model.StatusRunning {
		t.Fatalf("status = %s, want %s", deployment.Status, model.StatusRunning)
	}

	listOutput := runCommand(t, api, "deployments", "list")
	if !strings.Contains(listOutput, deployment.DeploymentID) || !strings.Contains(listOutput, model.StatusRunning) {
		t.Fatalf("deployment list missing result:\n%s", listOutput)
	}
	stopOutput := runCommand(t, api, "deployments", "stop", deployment.DeploymentID)
	if !strings.Contains(stopOutput, `"status": "STOPPED"`) {
		t.Fatalf("stop output:\n%s", stopOutput)
	}
}

func newCLIAPI(t *testing.T) http.Handler {
	t.Helper()
	api, err := server.NewCLIWithConfig(config.Settings{
		CPUVMRunner:      "dry-run",
		GPUVMRunner:      "dry-run",
		PackageOutputDir: filepath.Join(t.TempDir(), "packages"),
		PackageMaxUpload: 5 << 20,
	})
	if err != nil {
		t.Fatal(err)
	}
	return api
}

func packageResponseForTest(packageType string) model.PackageBuildResponse {
	spec := validMockApp()
	spec.Artifact = model.Artifact{Type: "package", URI: "file:///tmp/cli-package.tar.gz", Checksum: "sha256:test"}
	spec.Runtime = model.AppRuntime{Type: "cpu", Accelerator: "none"}
	return model.PackageBuildResponse{
		PackageType: packageType,
		ArtifactURI: spec.Artifact.URI,
		ArchiveName: "cli-package.tar.gz",
		SizeBytes:   128,
		Checksum:    spec.Artifact.Checksum,
		AppSpec:     spec,
	}
}

func runCommand(t *testing.T, api http.Handler, args ...string) string {
	t.Helper()
	var output bytes.Buffer
	shell := New(api, strings.NewReader(""), &output)
	if err := shell.Run(context.Background(), args); err != nil {
		t.Fatalf("run %v: %v", args, err)
	}
	return output.String()
}

func writeJSON(t *testing.T, dir, name string, value any) string {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func validMockApp() model.AppSpec {
	return model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata:      model.Metadata{Name: "cli-mock-app", Version: "0.1.0"},
		Artifact:      model.Artifact{Type: "script", URI: "file:///tmp/cli-mock-app/run.sh"},
		Entrypoint:    model.Entrypoint{Command: "bash", Args: []string{"run.sh"}},
		Runtime:       model.AppRuntime{Type: "mock", Accelerator: "none"},
		Resources:     model.Resources{CPU: "1", Memory: "1Gi", GPU: "0", Storage: "1Gi"},
	}
}
