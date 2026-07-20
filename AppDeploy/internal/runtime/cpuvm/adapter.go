package cpuvm

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

type Command = vmprocess.Command

type Result = vmprocess.Result

type Runner = vmprocess.Runner

type Adapter struct {
	runner  Runner
	process *vmprocess.Process
}

func New(runner Runner) *Adapter {
	return &Adapter{
		runner: runner,
		process: vmprocess.New(runner, vmprocess.Config{
			RuntimeType:     "cpu",
			DisplayName:     "cpu vm",
			Component:       "cpu-vm-adapter",
			RuntimeIDPrefix: "cpuvm-",
		}),
	}
}

func (a *Adapter) ValidateTarget(ctx context.Context, target model.TargetProfile) error {
	if target.Runtime.RuntimeType != "cpu" {
		return apperrors.New(model.ErrTargetProfileInvalid, "cpu vm adapter requires target runtime_type=cpu", http.StatusBadRequest, false)
	}
	if strings.TrimSpace(target.VM.Host) == "" {
		return apperrors.New(model.ErrTargetProfileInvalid, "cpu vm target requires vm.host", http.StatusBadRequest, false)
	}
	if strings.TrimSpace(target.VM.CredentialRef) == "" {
		return apperrors.New(model.ErrTargetProfileInvalid, "cpu vm target requires vm.credential_ref", http.StatusBadRequest, false)
	}
	if target.Storage == nil || strings.TrimSpace(target.Storage.ArtifactDir) == "" || strings.TrimSpace(target.Storage.LogDir) == "" {
		return apperrors.New(model.ErrStorageUnavailable, "cpu vm target requires storage.artifact_dir and storage.log_dir", http.StatusBadRequest, false)
	}
	return nil
}

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeConfig, target model.TargetProfile) error {
	if profile.RuntimeType != "cpu" || profile.AdapterType != "cpu_vm" {
		return apperrors.New(model.ErrRuntimeConfigInvalid, "cpu vm adapter requires runtime_type=cpu and adapter_type=cpu_vm", http.StatusBadRequest, false)
	}
	if err := a.ValidateTarget(ctx, target); err != nil {
		return err
	}
	_, err := a.runner.Run(ctx, target, Command{
		Stage: model.StatusValidating,
		Name:  "uname",
		Args:  []string{"-s"},
	})
	if err != nil {
		return apperrors.New(model.ErrCSPVMUnreachable, fmt.Sprintf("cpu vm readiness check failed: %v", err), http.StatusBadRequest, true)
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

type DryRunRunner struct{}

func NewDryRunRunner() *DryRunRunner {
	return &DryRunRunner{}
}

func (r *DryRunRunner) Run(ctx context.Context, target model.TargetProfile, command Command) (Result, error) {
	if strings.TrimSpace(target.VM.Host) == "" {
		return Result{}, fmt.Errorf("target vm host is empty")
	}
	return Result{
		Output: fmt.Sprintf("dry-run %s accepted", command.Name),
	}, nil
}
