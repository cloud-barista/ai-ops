package cli

import (
	"bytes"
	"context"
	"encoding/json"
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
	for _, expected := range []string{"AI App Deployer CLI", `"status": "ready"`, "종료합니다."} {
		if !strings.Contains(text, expected) {
			t.Fatalf("output does not contain %q:\n%s", expected, text)
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
		CPUVMRunner: "dry-run",
		GPUVMRunner: "dry-run",
	})
	if err != nil {
		t.Fatal(err)
	}
	return api
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
