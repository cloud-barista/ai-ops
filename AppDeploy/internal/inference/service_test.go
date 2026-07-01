package inference

import (
	"context"
	"strings"
	"testing"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
	"github.com/khu/ai-app-deployer/internal/store"
)

type fakeRunner struct {
	command cpuvm.Command
	output  string
	err     error
}

func (r *fakeRunner) Run(ctx context.Context, target model.TargetProfile, command cpuvm.Command) (cpuvm.Result, error) {
	r.command = command
	if r.err != nil {
		return cpuvm.Result{}, r.err
	}
	return cpuvm.Result{Output: r.output}, nil
}

func TestInvokeProxiesToDeploymentAppPort(t *testing.T) {
	ctx := context.Background()
	repo := store.NewMemory()
	createInferenceFixture(t, ctx, repo, model.StatusRunning)
	runner := &fakeRunner{output: `{"output":"ok"}
__AIAPP_HTTP_STATUS__:200`}
	service := NewService(repo, repo, repo, runner)

	resp, err := service.Invoke(ctx, "dep-qwen", model.InferenceInvokeRequest{
		Path: "/generate",
		Body: []byte(`{"prompt":"hello"}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 200 || resp.Port != 18081 || resp.Path != "/generate" {
		t.Fatalf("unexpected response: %+v", resp)
	}
	if runner.command.Name != "curl" {
		t.Fatalf("command = %s, want curl", runner.command.Name)
	}
	joined := strings.Join(runner.command.Args, " ")
	if !strings.Contains(joined, "http://127.0.0.1:18081/generate") {
		t.Fatalf("curl args did not target app port: %s", joined)
	}
	if body, ok := resp.Body.(map[string]any); !ok || body["output"] != "ok" {
		t.Fatalf("body = %#v", resp.Body)
	}
}

func TestInvokeRequiresRunningDeployment(t *testing.T) {
	ctx := context.Background()
	repo := store.NewMemory()
	createInferenceFixture(t, ctx, repo, model.StatusStopped)
	service := NewService(repo, repo, repo, &fakeRunner{})

	_, err := service.Invoke(ctx, "dep-qwen", model.InferenceInvokeRequest{Path: "/generate"})
	if err == nil {
		t.Fatal("expected error")
	}
	appErr, ok := err.(*apperrors.AppError)
	if !ok || appErr.Code != model.ErrRuntimeFailed {
		t.Fatalf("unexpected error: %#v", err)
	}
}

func createInferenceFixture(t *testing.T, ctx context.Context, repo *store.Memory, status string) {
	t.Helper()
	app := model.AppResponse{
		AppID:        "app-qwen",
		AppVersionID: "appver-qwen",
		Name:         "qwen-0-5b-gpu",
		Version:      "0.1.0",
		AppSpec: model.AppSpec{
			SchemaVersion: "appspec.khu.ai/v1alpha1",
			Kind:          "AIApp",
			Metadata: model.Metadata{
				Name:    "qwen-0-5b-gpu",
				Version: "0.1.0",
			},
			Artifact: model.Artifact{Type: "script", URI: "file:///tmp/run.sh"},
			Entrypoint: model.Entrypoint{
				Command: "bash",
				Args:    []string{"run.sh"},
			},
			Runtime:   model.AppRuntime{Type: "gpu", Accelerator: "nvidia"},
			Resources: model.Resources{GPU: "1"},
			Network: &model.Network{Ports: []model.Port{
				{Name: "http", AppPort: 18081, Protocol: "TCP"},
			}},
		},
		CreatedAt: time.Now().UTC(),
	}
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	if err := repo.CreateTargetProfile(ctx, model.TargetProfile{
		TargetProfileID: "target-gpu",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "127.0.0.1",
			SSHPort:       22,
			CredentialRef: "cred://test/gpu",
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   "gpu",
			Accelerator:   "nvidia",
			OperatingMode: "vm_process",
		},
	}); err != nil {
		t.Fatal(err)
	}
	if err := repo.CreateDeployment(ctx, model.DeploymentResponse{
		DeploymentID:     "dep-qwen",
		AppID:            "app-qwen",
		AppVersionID:     "appver-qwen",
		RuntimeProfileID: "rt-gpu",
		TargetProfileID:  "target-gpu",
		Status:           status,
		CreatedAt:        time.Now().UTC(),
		UpdatedAt:        time.Now().UTC(),
	}); err != nil {
		t.Fatal(err)
	}
}
