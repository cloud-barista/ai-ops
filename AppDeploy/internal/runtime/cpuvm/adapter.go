package cpuvm

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
)

type Command struct {
	Stage      string
	Name       string
	Args       []string
	WorkingDir string
}

type Result struct {
	Output string
}

type Runner interface {
	Run(ctx context.Context, target model.TargetProfile, command Command) (Result, error)
}

type Adapter struct {
	mu     sync.RWMutex
	runner Runner
	status map[string]string
	logs   map[string][]model.DeploymentLog
}

func New(runner Runner) *Adapter {
	return &Adapter{
		runner: runner,
		status: map[string]string{},
		logs:   map[string][]model.DeploymentLog{},
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

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeProfile, target model.TargetProfile) error {
	if profile.RuntimeType != "cpu" || profile.AdapterType != "cpu_vm" {
		return apperrors.New(model.ErrRuntimeProfileInvalid, "cpu vm adapter requires runtime_type=cpu and adapter_type=cpu_vm", http.StatusBadRequest, false)
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
		return apperrors.New(model.ErrCSPVMUnreachable, err.Error(), http.StatusBadRequest, true)
	}
	return nil
}

func (a *Adapter) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	if err := a.ValidateTarget(ctx, target); err != nil {
		return nil, err
	}
	artifactPath := path.Join(target.Storage.ArtifactDir, app.Name, app.Version)
	_, err := a.runner.Run(ctx, target, Command{
		Stage: model.StatusDeploying,
		Name:  "prepare-artifact",
		Args:  []string{app.AppSpec.Artifact.URI, artifactPath},
	})
	if err != nil {
		return nil, apperrors.New(model.ErrAppArtifactNotFound, err.Error(), http.StatusBadRequest, false)
	}
	return &runtime.PrepareResult{
		ArtifactPath: artifactPath,
		Message:      "cpu vm artifact preparation completed",
	}, nil
}

func (a *Adapter) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	if plan.App.AppSpec.Runtime.Type != "cpu" {
		return nil, apperrors.New(model.ErrRuntimeProfileInvalid, "cpu vm adapter can deploy only cpu apps", http.StatusBadRequest, false)
	}
	workingDir := plan.App.AppSpec.Entrypoint.WorkingDir
	if workingDir == "" {
		workingDir = path.Join(plan.Target.Storage.ArtifactDir, plan.App.Name, plan.App.Version)
	}
	result, err := a.runner.Run(ctx, plan.Target, Command{
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
		Component:    "cpu-vm-adapter",
		Stage:        model.StatusDeploying,
		Message:      maskSensitive("cpu vm command accepted: " + result.Output),
	})
	return &runtime.DeployResult{
		RuntimeID: "cpuvm-" + plan.DeploymentID,
		Message:   "cpu vm deployment is running",
	}, nil
}

func (a *Adapter) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	status := a.status[deploymentID]
	if status == "" {
		status = model.StatusUnknown
	}
	return &runtime.RuntimeStatus{Status: status, Message: "cpu vm status checked"}, nil
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
	result, err := a.runner.Run(ctx, plan.Target, Command{
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
		Component:    "cpu-vm-adapter",
		Stage:        model.StatusStopped,
		Message:      maskSensitive("cpu vm process stop requested: " + result.Output),
	})
	return nil
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
		Output: fmt.Sprintf("dry-run %s on %s", command.Name, target.VM.Host),
	}, nil
}

func maskSensitive(value string) string {
	value = strings.ReplaceAll(value, "password", "[masked]")
	value = strings.ReplaceAll(value, "token", "[masked]")
	value = strings.ReplaceAll(value, "secret", "[masked]")
	return value
}
