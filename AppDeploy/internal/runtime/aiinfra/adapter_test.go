package aiinfra

import (
	"context"
	"errors"
	"testing"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/external"
	"github.com/khu/ai-app-deployer/internal/external/etri"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
)

func TestETRIMockClientDeploymentFlow(t *testing.T) {
	adapter := New(etri.NewMockClient())
	ctx := context.Background()
	target := validTarget()
	profile := validRuntimeProfile()
	app := validApp()

	if err := adapter.HealthCheck(ctx, profile, target); err != nil {
		t.Fatal(err)
	}
	prepare, err := adapter.Prepare(ctx, app, target)
	if err != nil {
		t.Fatal(err)
	}
	if prepare.ArtifactPath != app.AppSpec.Artifact.URI {
		t.Fatalf("artifact path = %s, want %s", prepare.ArtifactPath, app.AppSpec.Artifact.URI)
	}

	_, err = adapter.Deploy(ctx, runtime.DeploymentPlan{
		DeploymentID: "dep-aiinfra-001",
		RequestID:    "req-aiinfra-001",
		App:          app,
		Runtime:      profile,
		Target:       target,
	})
	if err != nil {
		t.Fatal(err)
	}

	status, err := adapter.GetStatus(ctx, "dep-aiinfra-001")
	if err != nil {
		t.Fatal(err)
	}
	if status.Status != model.StatusRunning {
		t.Fatalf("status = %s, want %s", status.Status, model.StatusRunning)
	}
	logs, err := adapter.GetLogs(ctx, "dep-aiinfra-001", runtime.LogQuery{Stage: model.StatusDeploying})
	if err != nil {
		t.Fatal(err)
	}
	if len(logs) != 1 || logs[0].Component != "etri-aiinfra-client" {
		t.Fatalf("unexpected logs: %+v", logs)
	}
	if err := adapter.Stop(ctx, runtime.StopPlan{DeploymentID: "dep-aiinfra-001"}); err != nil {
		t.Fatal(err)
	}
	status, err = adapter.GetStatus(ctx, "dep-aiinfra-001")
	if err != nil {
		t.Fatal(err)
	}
	if status.Status != model.StatusStopped {
		t.Fatalf("status = %s, want %s", status.Status, model.StatusStopped)
	}
}

func TestETRIMockClientFailureMapping(t *testing.T) {
	ctx := context.Background()
	client := etri.NewMockClient()
	client.SetFailure(etri.MockStepCheck, external.NewError(external.ProviderETRI, external.ErrorKindTimeout, "etri fixture timeout", true))
	adapter := New(client)

	err := adapter.HealthCheck(ctx, validRuntimeProfile(), validTarget())
	appErr := mustAppError(t, err)
	if appErr.Code != model.ErrAIInfraAPITimeout {
		t.Fatalf("code = %s, want %s", appErr.Code, model.ErrAIInfraAPITimeout)
	}
	if !appErr.Retryable {
		t.Fatal("timeout fixture should be retryable")
	}

	client.SetFailure(etri.MockStepCheck, external.NewError(external.ProviderETRI, external.ErrorKindAuthFailed, "etri fixture auth failed", false))
	err = adapter.HealthCheck(ctx, validRuntimeProfile(), validTarget())
	appErr = mustAppError(t, err)
	if appErr.Code != model.ErrGatewayAuthFailed {
		t.Fatalf("code = %s, want %s", appErr.Code, model.ErrGatewayAuthFailed)
	}
	if appErr.Retryable {
		t.Fatal("auth fixture should not be retryable")
	}
}

func TestETRIMockClientDeployFailureMapping(t *testing.T) {
	ctx := context.Background()
	client := etri.NewMockClient()
	client.SetFailure(etri.MockStepDeploy, external.NewError(external.ProviderETRI, external.ErrorKindProviderFailed, "etri fixture provider failed", true))
	adapter := New(client)

	_, err := adapter.Deploy(ctx, runtime.DeploymentPlan{
		DeploymentID: "dep-aiinfra-001",
		RequestID:    "req-aiinfra-001",
		App:          validApp(),
		Runtime:      validRuntimeProfile(),
		Target:       validTarget(),
	})
	appErr := mustAppError(t, err)
	if appErr.Code != model.ErrAIInfraAPIFailed {
		t.Fatalf("code = %s, want %s", appErr.Code, model.ErrAIInfraAPIFailed)
	}
}

func mustAppError(t *testing.T, err error) *apperrors.AppError {
	t.Helper()
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		t.Fatalf("expected AppError, got %T", err)
	}
	return appErr
}

func validRuntimeProfile() model.RuntimeProfile {
	return model.RuntimeProfile{
		RuntimeProfileID: "rt-aiinfra-001",
		RuntimeType:      "aiinfra",
		Accelerator:      "none",
		AdapterType:      "etri_aiinfra",
		OperatingMode:    "remote_api",
	}
}

func validTarget() model.TargetProfile {
	return model.TargetProfile{
		TargetProfileID: "target-aiinfra-001",
		CSP:             "etri",
		VM: model.VMProfile{
			Host: "aiinfra-gateway.example.internal",
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   "aiinfra",
			Accelerator:   "none",
			OperatingMode: "remote_api",
		},
	}
}

func validApp() model.AppResponse {
	spec := model.AppSpec{
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
	return model.AppResponse{
		AppID:        "app-aiinfra-001",
		AppVersionID: "appver-aiinfra-001",
		Name:         spec.Metadata.Name,
		Version:      spec.Metadata.Version,
		AppSpec:      spec,
	}
}
