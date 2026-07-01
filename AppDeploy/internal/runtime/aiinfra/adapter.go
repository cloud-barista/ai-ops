package aiinfra

import (
	"context"
	"net/http"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/external"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
)

type Adapter struct {
	client external.Client
}

func New(client external.Client) *Adapter {
	return &Adapter{client: client}
}

func (a *Adapter) ValidateTarget(ctx context.Context, target model.TargetProfile) error {
	if target.Runtime.RuntimeType != "aiinfra" {
		return apperrors.New(model.ErrTargetProfileInvalid, "ai-infra adapter requires target runtime_type=aiinfra", http.StatusBadRequest, false)
	}
	if target.Runtime.OperatingMode != "" && target.Runtime.OperatingMode != "remote_api" && target.Runtime.OperatingMode != "local_mock" {
		return apperrors.New(model.ErrTargetProfileInvalid, "ai-infra adapter requires operating_mode=remote_api or local_mock", http.StatusBadRequest, false)
	}
	return nil
}

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeProfile, target model.TargetProfile) error {
	if profile.RuntimeType != "aiinfra" || profile.AdapterType != "etri_aiinfra" {
		return apperrors.New(model.ErrRuntimeProfileInvalid, "ai-infra adapter requires runtime_type=aiinfra and adapter_type=etri_aiinfra", http.StatusBadRequest, false)
	}
	if profile.OperatingMode != "" && profile.OperatingMode != "remote_api" && profile.OperatingMode != "local_mock" {
		return apperrors.New(model.ErrRuntimeProfileInvalid, "ai-infra adapter requires operating_mode=remote_api or local_mock", http.StatusBadRequest, false)
	}
	if err := a.ValidateTarget(ctx, target); err != nil {
		return err
	}
	client, err := a.ensureClient()
	if err != nil {
		return err
	}
	_, err = client.Check(ctx, target)
	return external.NormalizeError(client.Provider(), err)
}

func (a *Adapter) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	if err := a.ValidateTarget(ctx, target); err != nil {
		return nil, err
	}
	client, err := a.ensureClient()
	if err != nil {
		return nil, err
	}
	result, err := client.Prepare(ctx, external.PrepareRequest{
		App:    app,
		Target: target,
	})
	if err != nil {
		return nil, external.NormalizeError(client.Provider(), err)
	}
	return &runtime.PrepareResult{
		ArtifactPath: result.ArtifactRef,
		Message:      result.Message,
	}, nil
}

func (a *Adapter) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	appRuntime := plan.App.AppSpec.Runtime.Type
	if appRuntime != "aiinfra" && appRuntime != "gpu" {
		return nil, apperrors.New(model.ErrRuntimeProfileInvalid, "ai-infra adapter can deploy only aiinfra or gpu apps", http.StatusBadRequest, false)
	}
	client, err := a.ensureClient()
	if err != nil {
		return nil, err
	}
	result, err := client.Deploy(ctx, external.DeployRequest{
		DeploymentID: plan.DeploymentID,
		RequestID:    plan.RequestID,
		App:          plan.App,
		Runtime:      plan.Runtime,
		Target:       plan.Target,
		Parameters:   plan.Parameters,
	})
	if err != nil {
		return nil, external.NormalizeError(client.Provider(), err)
	}
	return &runtime.DeployResult{
		RuntimeID: result.ExternalDeploymentID,
		Message:   result.Message,
	}, nil
}

func (a *Adapter) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	client, err := a.ensureClient()
	if err != nil {
		return nil, err
	}
	result, err := client.Status(ctx, deploymentID)
	if err != nil {
		return nil, external.NormalizeError(client.Provider(), err)
	}
	return &runtime.RuntimeStatus{Status: result.Status, Message: result.Message}, nil
}

func (a *Adapter) GetLogs(ctx context.Context, deploymentID string, opt runtime.LogQuery) ([]model.DeploymentLog, error) {
	client, err := a.ensureClient()
	if err != nil {
		return nil, err
	}
	logs, err := client.Logs(ctx, deploymentID, opt.Stage)
	if err != nil {
		return nil, external.NormalizeError(client.Provider(), err)
	}
	return logs, nil
}

func (a *Adapter) Stop(ctx context.Context, plan runtime.StopPlan) error {
	client, err := a.ensureClient()
	if err != nil {
		return err
	}
	return external.NormalizeError(client.Provider(), client.Stop(ctx, plan.DeploymentID))
}

func (a *Adapter) ensureClient() (external.Client, error) {
	if a.client == nil {
		return nil, apperrors.New(model.ErrAIInfraAPIFailed, "ai-infra external client is not configured", http.StatusBadGateway, true)
	}
	return a.client, nil
}
