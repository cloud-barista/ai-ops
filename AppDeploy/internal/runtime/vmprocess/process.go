package vmprocess

import (
	"context"
	"net/http"
	"path"
	"strings"
	"sync"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/rs/zerolog/log"
)

const (
	CommandPrepareArtifact = "prepare-artifact"
	CommandStopProcess     = "stop-process"
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

type Config struct {
	RuntimeType     string
	DisplayName     string
	Component       string
	RuntimeIDPrefix string
}

type Process struct {
	mu     sync.RWMutex
	runner Runner
	config Config
	status map[string]string
	logs   map[string][]model.DeploymentLog
}

func New(runner Runner, config Config) *Process {
	return &Process{
		runner: runner,
		config: config,
		status: map[string]string{},
		logs:   map[string][]model.DeploymentLog{},
	}
}

func (p *Process) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	artifactPath := path.Join(target.Storage.ArtifactDir, app.Name, app.Version)
	_, err := p.runner.Run(ctx, target, Command{
		Stage: model.StatusDeploying,
		Name:  CommandPrepareArtifact,
		Args:  []string{app.AppSpec.Artifact.URI, artifactPath},
	})
	if err != nil {
		return nil, apperrors.New(model.ErrAppArtifactNotFound, p.config.DisplayName+" artifact preparation failed", http.StatusBadRequest, false)
	}
	return &runtime.PrepareResult{
		ArtifactPath: artifactPath,
		Message:      p.config.DisplayName + " artifact preparation completed",
	}, nil
}

func (p *Process) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	if plan.App.AppSpec.Runtime.Type != p.config.RuntimeType {
		return nil, apperrors.New(model.ErrRuntimeConfigInvalid, p.config.DisplayName+" adapter can deploy only "+p.config.RuntimeType+" apps", http.StatusBadRequest, false)
	}
	workingDir := workingDirectory(plan.App, plan.Target)
	result, err := p.runner.Run(ctx, plan.Target, Command{
		Stage:      model.StatusDeploying,
		Name:       plan.App.AppSpec.Entrypoint.Command,
		Args:       plan.App.AppSpec.Entrypoint.Args,
		WorkingDir: workingDir,
	})
	if err != nil {
		return nil, apperrors.New(model.ErrDeploymentFailed, p.config.DisplayName+" deployment command failed", http.StatusBadRequest, false)
	}

	p.mu.Lock()
	defer p.mu.Unlock()
	p.status[plan.DeploymentID] = model.StatusRunning
	item := model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    plan.RequestID,
		DeploymentID: plan.DeploymentID,
		Component:    p.config.Component,
		Stage:        model.StatusDeploying,
		Message:      maskSensitive(p.config.DisplayName + " command accepted: " + result.Output),
	}
	p.logs[plan.DeploymentID] = append(p.logs[plan.DeploymentID], item)
	logAdapterEvent(item)
	return &runtime.DeployResult{
		RuntimeID: p.config.RuntimeIDPrefix + plan.DeploymentID,
		Message:   p.config.DisplayName + " deployment is running",
	}, nil
}

func (p *Process) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	status := p.status[deploymentID]
	if status == "" {
		status = model.StatusUnknown
	}
	return &runtime.RuntimeStatus{Status: status, Message: p.config.DisplayName + " status checked"}, nil
}

func (p *Process) GetLogs(ctx context.Context, deploymentID string, opt runtime.LogQuery) ([]model.DeploymentLog, error) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	source := p.logs[deploymentID]
	items := make([]model.DeploymentLog, 0, len(source))
	for _, item := range source {
		if opt.Stage == "" || item.Stage == opt.Stage {
			items = append(items, item)
		}
	}
	return items, nil
}

func (p *Process) Stop(ctx context.Context, plan runtime.StopPlan) error {
	workingDir := workingDirectory(plan.App, plan.Target)
	result, err := p.runner.Run(ctx, plan.Target, Command{
		Stage: model.StatusStopping,
		Name:  CommandStopProcess,
		Args:  []string{workingDir},
	})
	if err != nil {
		return apperrors.New(model.ErrRuntimeFailed, p.config.DisplayName+" stop command failed", http.StatusBadRequest, true)
	}

	p.mu.Lock()
	defer p.mu.Unlock()
	p.status[plan.DeploymentID] = model.StatusStopped
	item := model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    plan.RequestID,
		DeploymentID: plan.DeploymentID,
		Component:    p.config.Component,
		Stage:        model.StatusStopped,
		Message:      maskSensitive(p.config.DisplayName + " process stop requested: " + result.Output),
	}
	p.logs[plan.DeploymentID] = append(p.logs[plan.DeploymentID], item)
	logAdapterEvent(item)
	return nil
}

func workingDirectory(app model.AppResponse, target model.TargetProfile) string {
	workingDir := app.AppSpec.Entrypoint.WorkingDir
	if workingDir == "" {
		workingDir = path.Join(target.Storage.ArtifactDir, app.Name, app.Version)
	}
	return workingDir
}

func logAdapterEvent(item model.DeploymentLog) {
	log.Info().
		Str("request_id", item.RequestID).
		Str("deployment_id", item.DeploymentID).
		Str("component", item.Component).
		Str("stage", item.Stage).
		Msg(item.Message)
}

func maskSensitive(value string) string {
	value = strings.ReplaceAll(value, "password", "[masked]")
	value = strings.ReplaceAll(value, "token", "[masked]")
	value = strings.ReplaceAll(value, "secret", "[masked]")
	return value
}
