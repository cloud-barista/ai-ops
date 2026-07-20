package gpuvm

import (
	"context"
	"fmt"
	"net/http"
	"strings"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/runtime/vmprocess"
)

type Adapter struct {
	runner  vmprocess.Runner
	process *vmprocess.Process
}

func New(runner vmprocess.Runner) *Adapter {
	return &Adapter{
		runner: runner,
		process: vmprocess.New(runner, vmprocess.Config{
			RuntimeType:     "gpu",
			DisplayName:     "gpu vm",
			Component:       "gpu-vm-adapter",
			RuntimeIDPrefix: "gpuvm-",
		}),
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

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeConfig, target model.TargetProfile) error {
	if profile.RuntimeType != "gpu" || profile.AdapterType != "gpu_vm" {
		return apperrors.New(model.ErrRuntimeConfigInvalid, "gpu vm adapter requires runtime_type=gpu and adapter_type=gpu_vm", http.StatusBadRequest, false)
	}
	if profile.Accelerator != "" && profile.Accelerator != "nvidia" {
		return apperrors.New(model.ErrRuntimeConfigInvalid, "gpu vm adapter requires nvidia accelerator", http.StatusBadRequest, false)
	}
	if err := a.ValidateTarget(ctx, target); err != nil {
		return err
	}
	_, err := a.runner.Run(ctx, target, vmprocess.Command{
		Stage: model.StatusValidating,
		Name:  "nvidia-smi",
		Args:  []string{"--query-gpu=name,driver_version", "--format=csv,noheader"},
	})
	if err != nil {
		return apperrors.New(model.ErrNvidiaDriverNotFound, fmt.Sprintf("nvidia-smi check failed: %v", err), http.StatusBadRequest, false)
	}
	return nil
}

func (a *Adapter) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	if err := a.ValidateTarget(ctx, target); err != nil {
		return nil, err
	}
	return a.process.Prepare(ctx, app, target)
}

func (a *Adapter) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	return a.process.Deploy(ctx, plan)
}

func (a *Adapter) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	return a.process.GetStatus(ctx, deploymentID)
}

func (a *Adapter) GetLogs(ctx context.Context, deploymentID string, opt runtime.LogQuery) ([]model.DeploymentLog, error) {
	return a.process.GetLogs(ctx, deploymentID, opt)
}

func (a *Adapter) Stop(ctx context.Context, plan runtime.StopPlan) error {
	return a.process.Stop(ctx, plan)
}

func NewDryRunRunner() vmprocess.Runner {
	return &gpuDryRunRunner{}
}

type gpuDryRunRunner struct{}

func (r *gpuDryRunRunner) Run(ctx context.Context, target model.TargetProfile, command vmprocess.Command) (vmprocess.Result, error) {
	if strings.TrimSpace(target.VM.Host) == "" {
		return vmprocess.Result{}, fmt.Errorf("target vm host is empty")
	}
	if command.Name == "nvidia-smi" {
		return vmprocess.Result{Output: "NVIDIA Mock GPU, 535.00"}, nil
	}
	return vmprocess.Result{Output: fmt.Sprintf("dry-run %s accepted", command.Name)}, nil
}
