package local

import (
	"context"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
)

func TestLocalArtifactPathAcceptsWindowsFileURI(t *testing.T) {
	path := filepath.Join(t.TempDir(), "app")
	for _, uri := range []string{
		(&url.URL{Scheme: "file", Path: filepath.ToSlash(path)}).String(),
		"file:///" + filepath.ToSlash(path),
	} {
		got, err := localArtifactPath(uri)
		if err != nil {
			t.Fatal(err)
		}
		if filepath.Clean(got) != filepath.Clean(path) {
			t.Fatalf("file URI path = %q, want %q (uri %q)", got, path, uri)
		}
	}
}

func TestLocalProcessAdapterRunsAndStopsRealProcess(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_HELPER") == "1" {
		time.Sleep(30 * time.Second)
		return
	}
	workDir := t.TempDir()
	adapter := New(workDir)
	target := model.TargetProfile{TargetProfileID: "local-node", CSP: "local", Runtime: model.TargetRuntime{RuntimeType: "local", OperatingMode: "local_process"}}
	app := model.AppResponse{
		Name: "local-process", Version: "test", AppSpec: model.AppSpec{
			Artifact:   model.Artifact{Type: "script", URI: filepath.ToSlash(workDir)},
			Entrypoint: model.Entrypoint{Command: os.Args[0], Args: []string{"-test.run=TestLocalProcessAdapterRunsAndStopsRealProcess"}},
			Runtime:    model.AppRuntime{Type: "cpu"},
		},
	}
	t.Setenv("AI_APP_LOCAL_HELPER", "1")
	ctx := context.Background()
	if err := adapter.HealthCheck(ctx, model.RuntimeConfig{RuntimeType: "local", AdapterType: "local_process"}, target); err != nil {
		t.Fatal(err)
	}
	if _, err := adapter.Prepare(ctx, app, target); err != nil {
		t.Fatal(err)
	}
	plan := runtime.DeploymentPlan{DeploymentID: "dep-local-process", RequestID: "req-local", App: app, Runtime: model.RuntimeConfig{RuntimeType: "local", AdapterType: "local_process"}, Target: target}
	result, err := adapter.Deploy(ctx, plan)
	if err != nil || result.RuntimeID == "" {
		t.Fatalf("Deploy() result=%+v err=%v", result, err)
	}
	status, err := adapter.GetStatus(ctx, plan.DeploymentID)
	if err != nil || status.Status != model.StatusRunning {
		t.Fatalf("initial status=%+v err=%v", status, err)
	}
	if err := adapter.Stop(ctx, runtime.StopPlan{DeploymentID: plan.DeploymentID, RequestID: plan.RequestID, App: app, Target: target}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		status, _ = adapter.GetStatus(ctx, plan.DeploymentID)
		if status.Status == model.StatusStopped {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if status.Status != model.StatusStopped {
		t.Fatalf("final status=%s, want %s", status.Status, model.StatusStopped)
	}
	logs, err := adapter.GetLogs(ctx, plan.DeploymentID, runtime.LogQuery{})
	if err != nil || len(logs) == 0 {
		t.Fatalf("local process logs = %+v, err=%v", logs, err)
	}
}
