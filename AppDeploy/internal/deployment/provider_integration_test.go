package deployment

import (
	"context"
	"errors"
	"testing"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/provider"
	"github.com/khu/ai-app-deployer/internal/resource"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/store"
)

func TestDeploymentFailureReturnsReservedResources(t *testing.T) {
	ctx := context.Background()
	repo := store.NewMemory()
	app := model.AppResponse{AppID: "app-1", AppVersionID: "appver-1", Name: "worker", Version: "1", AppSpec: model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1", Kind: "AIApp",
		Metadata:   model.Metadata{Name: "worker", Version: "1"},
		Artifact:   model.Artifact{Type: "script", URI: "file:///tmp/worker.sh"},
		Entrypoint: model.Entrypoint{Command: "worker"}, Runtime: model.AppRuntime{Type: "cpu"},
		Resources: model.Resources{CPU: "1", Memory: "1Gi", Storage: "1Gi"},
	}}
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	if err := repo.CreateTargetProfile(ctx, model.TargetProfile{
		TargetProfileID: "node-1", CSP: "local", Status: model.NodeStatusReady,
		Runtime:  model.TargetRuntime{RuntimeType: "cpu", OperatingMode: "dry_run"},
		Capacity: model.ResourceCapacity{CPUCores: 2, MemoryBytes: 4 << 30, StorageBytes: 10 << 30},
	}); err != nil {
		t.Fatal(err)
	}
	placement, err := provider.NewLocalPlacementProvider(repo)
	if err != nil {
		t.Fatal(err)
	}
	service := NewServiceWithProviders(repo, repo, repo, resource.NewMatcher(), failingAdapter{}, placement, placement)
	_, err = service.Create(ctx, model.DeploymentCreateRequest{AppVersionID: app.AppVersionID})
	if err == nil {
		t.Fatal("expected deployment to fail")
	}
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) || appErr.Code != model.ErrDeploymentFailed {
		t.Fatalf("error = %v, want deployment failure", err)
	}
	node, err := placement.GetResource(ctx, "node-1")
	if err != nil {
		t.Fatal(err)
	}
	if node.Allocated.CPUCores != 0 || node.Allocated.MemoryBytes != 0 {
		t.Fatalf("allocation after failure = %+v, want zero", node.Allocated)
	}
}

type failingAdapter struct{}

func (failingAdapter) ValidateTarget(context.Context, model.TargetProfile) error { return nil }
func (failingAdapter) HealthCheck(context.Context, model.RuntimeConfig, model.TargetProfile) error {
	return nil
}
func (failingAdapter) Prepare(context.Context, model.AppResponse, model.TargetProfile) (*runtime.PrepareResult, error) {
	return nil, apperrors.New(model.ErrDeploymentFailed, "test runtime rejected deployment", 400, false)
}
func (failingAdapter) Deploy(context.Context, runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	return nil, apperrors.New(model.ErrDeploymentFailed, "test runtime rejected deployment", 400, false)
}
func (failingAdapter) GetStatus(context.Context, string) (*runtime.RuntimeStatus, error) {
	return &runtime.RuntimeStatus{Status: model.StatusUnknown}, nil
}
func (failingAdapter) GetLogs(context.Context, string, runtime.LogQuery) ([]model.DeploymentLog, error) {
	return nil, nil
}
func (failingAdapter) Stop(context.Context, runtime.StopPlan) error { return nil }
