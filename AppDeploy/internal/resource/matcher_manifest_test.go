package resource

import (
	"context"
	"errors"
	"testing"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

func TestMatchManifestUsesPlannerGPURequirement(t *testing.T) {
	matcher := NewMatcher()
	app := model.AppResponse{AppSpec: model.AppSpec{
		Runtime:   model.AppRuntime{Type: "gpu", Accelerator: "nvidia"},
		Resources: model.Resources{GPU: "1"},
	}}
	runtimeProfile := model.RuntimeProfile{RuntimeType: "gpu", Accelerator: "nvidia"}
	target := model.TargetProfile{
		Runtime: model.TargetRuntime{RuntimeType: "gpu", Accelerator: "nvidia"},
		GPU:     &model.GPUProfile{Vendor: "nvidia", Count: 1},
	}
	err := matcher.MatchManifest(context.Background(), model.DeploymentManifest{
		Spec: model.DeploymentManifestSpec{
			Accelerator: "nvidia",
			Resources:   model.Resources{GPU: "2"},
		},
	}, app, runtimeProfile, target)
	if err == nil {
		t.Fatal("expected planner GPU requirement to fail against one-GPU target")
	}
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) || appErr.Code != model.ErrResourceInsufficient {
		t.Fatalf("error = %v, want RESOURCE_INSUFFICIENT", err)
	}
}

func TestMatchManifestRejectsUnavailableAccelerator(t *testing.T) {
	matcher := NewMatcher()
	err := matcher.MatchManifest(context.Background(), model.DeploymentManifest{
		Spec: model.DeploymentManifestSpec{Accelerator: "nvidia", Resources: model.Resources{GPU: "1"}},
	}, model.AppResponse{AppSpec: model.AppSpec{Runtime: model.AppRuntime{Type: "cpu", Accelerator: "none"}}}, model.RuntimeProfile{RuntimeType: "cpu", Accelerator: "none"}, model.TargetProfile{Runtime: model.TargetRuntime{RuntimeType: "cpu", Accelerator: "none"}})
	if err == nil {
		t.Fatal("expected unavailable accelerator to fail")
	}
	if err.Error() == "" {
		t.Fatal("expected actionable matcher error")
	}
}
