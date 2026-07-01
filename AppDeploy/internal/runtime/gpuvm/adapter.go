package gpuvm

import (
	"context"
	"fmt"
	"net/http"
	"path"
	"strings"
	"sync"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
)

type Adapter struct {
	mu     sync.RWMutex
	runner cpuvm.Runner
	status map[string]string
	logs   map[string][]model.DeploymentLog
}

func New(runner cpuvm.Runner) *Adapter {
	return &Adapter{
		runner: runner,
		status: map[string]string{},
		logs:   map[string][]model.DeploymentLog{},
	}
}

func (a *Adapter) ValidateTarget(ctx context.Context, target model.TargetProfile) error {
	if target.Runtime.RuntimeType != "gpu" {
		return apperrors.New(model.ErrTargetProfileInvalid, "gpu vm adapter requires target runtime_type=gpu", http.StatusBadRequest, false)
	}
	if target.Runtime.Accelerator != "" && target.Runtime.Accelerator != "nvidia" {
		return apperrors.New(model.ErrTargetProfileInvalid, "gpu vm adapter requires nvidia accelerator", http.StatusBadRequest, false)
	}
	if strings.TrimSpace(target.VM.Host) == "" {
		return apperrors.New(model.ErrTargetProfileInvalid, "gpu vm target requires vm.host", http.StatusBadRequest, false)
	}
	if strings.TrimSpace(target.VM.CredentialRef) == "" {
		return apperrors.New(model.ErrTargetProfileInvalid, "gpu vm target requires vm.credential_ref", http.StatusBadRequest, false)
	}
	if target.Storage == nil || strings.TrimSpace(target.Storage.ArtifactDir) == "" || strings.TrimSpace(target.Storage.LogDir) == "" {
		return apperrors.New(model.ErrStorageUnavailable, "gpu vm target requires storage.artifact_dir and storage.log_dir", http.StatusBadRequest, false)
	}
	if target.GPU == nil || target.GPU.Count < 1 {
		return apperrors.New(model.ErrGPURuntimeNotFound, "gpu vm target requires gpu.count >= 1", http.StatusBadRequest, false)
	}
	return nil
}

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeProfile, target model.TargetProfile) error {
	if profile.RuntimeType != "gpu" || profile.AdapterType != "gpu_vm" {
		return apperrors.New(model.ErrRuntimeProfileInvalid, "gpu vm adapter requires runtime_type=gpu and adapter_type=gpu_vm", http.StatusBadRequest, false)
	}
	if profile.Accelerator != "" && profile.Accelerator != "nvidia" {
		return apperrors.New(model.ErrRuntimeProfileInvalid, "gpu vm adapter requires nvidia accelerator", http.StatusBadRequest, false)
	}
	if err := a.ValidateTarget(ctx, target); err != nil {
		return err
	}
	_, err := a.runner.Run(ctx, target, cpuvm.Command{
		Stage: model.StatusValidating,
		Name:  "nvidia-smi",
		Args:  []string{"--query-gpu=name,driver_version", "--format=csv,noheader"},
	})
	if err != nil {
		return apperrors.New(model.ErrNvidiaDriverNotFound, err.Error(), http.StatusBadRequest, false)
	}
	return nil
}

func (a *Adapter) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	if err := a.ValidateTarget(ctx, target); err != nil {
		return nil, err
	}
	artifactPath := path.Join(target.Storage.ArtifactDir, app.Name, app.Version)
	_, err := a.runner.Run(ctx, target, cpuvm.Command{
		Stage: model.StatusDeploying,
		Name:  "prepare-artifact",
		Args:  []string{app.AppSpec.Artifact.URI, artifactPath},
	})
	if err != nil {
		return nil, apperrors.New(model.ErrAppArtifactNotFound, err.Error(), http.StatusBadRequest, false)
	}
	return &runtime.PrepareResult{
		ArtifactPath: artifactPath,
		Message:      "gpu vm artifact preparation completed",
	}, nil
}

func (a *Adapter) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	if plan.App.AppSpec.Runtime.Type != "gpu" {
		return nil, apperrors.New(model.ErrRuntimeProfileInvalid, "gpu vm adapter can deploy only gpu apps", http.StatusBadRequest, false)
	}
	workingDir := plan.App.AppSpec.Entrypoint.WorkingDir
	if workingDir == "" {
		workingDir = path.Join(plan.Target.Storage.ArtifactDir, plan.App.Name, plan.App.Version)
	}
	result, err := a.runner.Run(ctx, plan.Target, cpuvm.Command{
		Stage:      model.StatusDeploying,
		Name:       plan.App.AppSpec.Entrypoint.Command,
		Args:       plan.App.AppSpec.Entrypoint.Args,
		WorkingDir: workingDir,
	})
	if err != nil {
		return nil, apperrors.New(model.ErrDeploymentFailed, err.Error(), http.StatusBadRequest, false)
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	a.status[plan.DeploymentID] = model.StatusRunning
	a.logs[plan.DeploymentID] = append(a.logs[plan.DeploymentID], model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    plan.RequestID,
		DeploymentID: plan.DeploymentID,
		Component:    "gpu-vm-adapter",
		Stage:        model.StatusDeploying,
		Message:      maskSensitive("gpu vm command accepted: " + result.Output),
	})
	return &runtime.DeployResult{
		RuntimeID: "gpuvm-" + plan.DeploymentID,
		Message:   "gpu vm deployment is running",
	}, nil
}

func (a *Adapter) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	status := a.status[deploymentID]
	if status == "" {
		status = model.StatusUnknown
	}
	return &runtime.RuntimeStatus{Status: status, Message: "gpu vm status checked"}, nil
}

func (a *Adapter) GetLogs(ctx context.Context, deploymentID string, opt runtime.LogQuery) ([]model.DeploymentLog, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	source := a.logs[deploymentID]
	items := make([]model.DeploymentLog, 0, len(source))
	for _, item := range source {
		if opt.Stage == "" || item.Stage == opt.Stage {
			items = append(items, item)
		}
	}
	return items, nil
}

func (a *Adapter) Stop(ctx context.Context, plan runtime.StopPlan) error {
	workingDir := plan.App.AppSpec.Entrypoint.WorkingDir
	if workingDir == "" {
		workingDir = path.Join(plan.Target.Storage.ArtifactDir, plan.App.Name, plan.App.Version)
	}
	result, err := a.runner.Run(ctx, plan.Target, cpuvm.Command{
		Stage: model.StatusStopping,
		Name:  "stop-process",
		Args:  []string{workingDir},
	})
	if err != nil {
		return apperrors.New(model.ErrRuntimeFailed, err.Error(), http.StatusBadRequest, true)
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	a.status[plan.DeploymentID] = model.StatusStopped
	a.logs[plan.DeploymentID] = append(a.logs[plan.DeploymentID], model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    plan.RequestID,
		DeploymentID: plan.DeploymentID,
		Component:    "gpu-vm-adapter",
		Stage:        model.StatusStopped,
		Message:      maskSensitive("gpu vm process stop requested: " + result.Output),
	})
	return nil
}

func maskSensitive(value string) string {
	value = strings.ReplaceAll(value, "password", "[masked]")
	value = strings.ReplaceAll(value, "token", "[masked]")
	value = strings.ReplaceAll(value, "secret", "[masked]")
	return value
}

func NewDryRunRunner() cpuvm.Runner {
	return &gpuDryRunRunner{}
}

type gpuDryRunRunner struct{}

func (r *gpuDryRunRunner) Run(ctx context.Context, target model.TargetProfile, command cpuvm.Command) (cpuvm.Result, error) {
	if strings.TrimSpace(target.VM.Host) == "" {
		return cpuvm.Result{}, fmt.Errorf("target vm host is empty")
	}
	if command.Name == "nvidia-smi" {
		return cpuvm.Result{Output: "NVIDIA Mock GPU, 535.00"}, nil
	}
	return cpuvm.NewDryRunRunner().Run(ctx, target, command)
}
