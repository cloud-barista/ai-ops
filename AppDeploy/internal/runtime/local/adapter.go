package local

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
)

type Adapter struct {
	mu       sync.RWMutex
	workRoot string
	jobs     map[string]*job
}

type job struct {
	cmd       *exec.Cmd
	status    string
	stopping  bool
	logs      []model.DeploymentLog
	output    safeBuffer
	requestID string
}

func New(workRoot string) *Adapter {
	if strings.TrimSpace(workRoot) == "" {
		workRoot = filepath.Join("tmp", "local-runtime")
	}
	return &Adapter{workRoot: workRoot, jobs: map[string]*job{}}
}

func (a *Adapter) ValidateTarget(ctx context.Context, target model.TargetProfile) error {
	if target.CSP != "local" || target.Runtime.RuntimeType != "local" {
		return apperrors.New(model.ErrTargetProfileInvalid, "local process adapter requires csp=local and runtime_type=local", http.StatusBadRequest, false)
	}
	if target.Runtime.OperatingMode != "" && target.Runtime.OperatingMode != "local_process" && target.Runtime.OperatingMode != "local_mock" {
		return apperrors.New(model.ErrTargetProfileInvalid, "local process adapter requires operating_mode=local_process", http.StatusBadRequest, false)
	}
	return nil
}

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeConfig, target model.TargetProfile) error {
	if profile.AdapterType != "local_process" || profile.RuntimeType != "local" {
		return apperrors.New(model.ErrRuntimeConfigInvalid, "local process adapter requires runtime_type=local and adapter_type=local_process", http.StatusBadRequest, false)
	}
	if err := a.ValidateTarget(ctx, target); err != nil {
		return err
	}
	return os.MkdirAll(a.workRoot, 0o755)
}

func (a *Adapter) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	if err := a.ValidateTarget(ctx, target); err != nil {
		return nil, err
	}
	artifactPath, err := localArtifactPath(app.AppSpec.Artifact.URI)
	if err != nil {
		return nil, apperrors.New(model.ErrAppArtifactNotFound, "local application artifact URI is invalid", http.StatusBadRequest, false)
	}
	if _, err := os.Stat(artifactPath); err != nil {
		return nil, apperrors.New(model.ErrAppArtifactNotFound, "local application artifact was not found", http.StatusNotFound, false)
	}
	workingDir := a.workingDirectory(app, target, artifactPath)
	if err := os.MkdirAll(workingDir, 0o755); err != nil {
		return nil, apperrors.New(model.ErrStorageUnavailable, "local runtime working directory could not be created", http.StatusInternalServerError, false)
	}
	return &runtime.PrepareResult{ArtifactPath: artifactPath, Message: "local application working directory prepared"}, nil
}

func (a *Adapter) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	if plan.Target.Runtime.RuntimeType != "local" {
		return nil, apperrors.New(model.ErrRuntimeConfigInvalid, "local process adapter requires a local target", http.StatusBadRequest, false)
	}
	command, args := executionCommand(plan)
	if strings.TrimSpace(command) == "" {
		return nil, apperrors.New(model.ErrEntrypointInvalid, "local deployment command is empty", http.StatusBadRequest, false)
	}
	artifactPath, err := localArtifactPath(plan.App.AppSpec.Artifact.URI)
	if err != nil {
		return nil, apperrors.New(model.ErrAppArtifactNotFound, "local application artifact URI is invalid", http.StatusBadRequest, false)
	}
	workingDir := a.workingDirectory(plan.App, plan.Target, artifactPath)
	if err := os.MkdirAll(workingDir, 0o755); err != nil {
		return nil, apperrors.New(model.ErrStorageUnavailable, "local runtime working directory could not be created", http.StatusInternalServerError, false)
	}
	if !filepath.IsAbs(command) && (strings.Contains(command, string(filepath.Separator)) || strings.Contains(command, "/")) {
		command = filepath.Join(workingDir, filepath.FromSlash(command))
	}
	// Deployment outlives the HTTP request that accepted it; Stop owns process
	// termination after the request context is canceled.
	cmd := exec.CommandContext(context.WithoutCancel(ctx), command, args...)
	cmd.Dir = workingDir
	item := &job{cmd: cmd, status: model.StatusDeploying, requestID: plan.RequestID}
	cmd.Stdout = &item.output
	cmd.Stderr = &item.output
	if err := cmd.Start(); err != nil {
		return nil, apperrors.New(model.ErrDeploymentFailed, "local application process could not be started", http.StatusBadRequest, false)
	}
	a.mu.Lock()
	a.jobs[plan.DeploymentID] = item
	item.status = model.StatusRunning
	a.mu.Unlock()
	go a.wait(plan.DeploymentID, item, plan)
	return &runtime.DeployResult{RuntimeID: fmt.Sprintf("local-process-%d", cmd.Process.Pid), Message: "local application process is running"}, nil
}

func (a *Adapter) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	a.mu.RLock()
	item := a.jobs[deploymentID]
	if item == nil {
		a.mu.RUnlock()
		return &runtime.RuntimeStatus{Status: model.StatusUnknown, Message: "local process was not found"}, nil
	}
	status := item.status
	a.mu.RUnlock()
	return &runtime.RuntimeStatus{Status: status, Message: "local process status checked"}, nil
}

func (a *Adapter) GetLogs(ctx context.Context, deploymentID string, opt runtime.LogQuery) ([]model.DeploymentLog, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	a.mu.RLock()
	defer a.mu.RUnlock()
	item := a.jobs[deploymentID]
	if item == nil {
		return nil, nil
	}
	logs := make([]model.DeploymentLog, 0, len(item.logs))
	for _, entry := range item.logs {
		if opt.Stage == "" || opt.Stage == entry.Stage {
			logs = append(logs, entry)
		}
	}
	if output := strings.TrimSpace(item.output.String()); item.status == model.StatusRunning && output != "" && (opt.Stage == "" || opt.Stage == item.status) {
		logs = append(logs, model.DeploymentLog{
			Timestamp: time.Now().UTC(), Level: "INFO", RequestID: item.requestID,
			DeploymentID: deploymentID, Component: "local-process-adapter", Stage: item.status,
			Message: maskOutput(output),
		})
	}
	return logs, nil
}

func (a *Adapter) Stop(ctx context.Context, plan runtime.StopPlan) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	a.mu.Lock()
	item := a.jobs[plan.DeploymentID]
	if item == nil || item.cmd == nil || item.cmd.Process == nil {
		a.mu.Unlock()
		return nil
	}
	if item.status == model.StatusStopped || item.status == model.StatusCompleted {
		a.mu.Unlock()
		return nil
	}
	item.stopping = true
	process := item.cmd.Process
	a.mu.Unlock()
	if err := process.Kill(); err != nil {
		return apperrors.New(model.ErrRuntimeFailed, "local application process could not be stopped", http.StatusBadRequest, true)
	}
	return nil
}

func (a *Adapter) wait(deploymentID string, item *job, plan runtime.DeploymentPlan) {
	err := item.cmd.Wait()
	a.mu.Lock()
	defer a.mu.Unlock()
	if item.stopping {
		item.status = model.StatusStopped
	} else if err != nil {
		item.status = model.StatusRuntimeFailed
	} else {
		item.status = model.StatusCompleted
	}
	message := strings.TrimSpace(item.output.String())
	if message == "" {
		message = "local process exited"
	}
	if err != nil && !item.stopping {
		message += ": " + err.Error()
	}
	level := "INFO"
	if err != nil && !item.stopping {
		level = "ERROR"
	}
	stage := item.status
	item.logs = append(item.logs, model.DeploymentLog{
		Timestamp: time.Now().UTC(), Level: level, RequestID: plan.RequestID,
		DeploymentID: deploymentID, Component: "local-process-adapter", Stage: stage,
		Message: maskOutput(message),
	})
}

func (a *Adapter) workingDirectory(app model.AppResponse, target model.TargetProfile, artifactPath string) string {
	if app.AppSpec.Entrypoint.WorkingDir != "" {
		return app.AppSpec.Entrypoint.WorkingDir
	}
	if target.Storage != nil && target.Storage.ArtifactDir != "" {
		return target.Storage.ArtifactDir
	}
	info, err := os.Stat(artifactPath)
	if err == nil && !info.IsDir() {
		return filepath.Dir(artifactPath)
	}
	return filepath.Join(a.workRoot, app.Name, app.Version)
}

func executionCommand(plan runtime.DeploymentPlan) (string, []string) {
	command := plan.App.AppSpec.Entrypoint.Command
	args := append([]string(nil), plan.App.AppSpec.Entrypoint.Args...)
	if plan.Manifest != nil && plan.Manifest.Spec.Requirements != nil {
		requirements := plan.Manifest.Spec.Requirements
		if requirements.Command != "" {
			command = requirements.Command
		}
		if requirements.Args != nil {
			args = append([]string(nil), requirements.Args...)
		}
	}
	return command, args
}

func localArtifactPath(uri string) (string, error) {
	uri = strings.TrimSpace(uri)
	if uri == "" {
		return "", fmt.Errorf("artifact URI is empty")
	}
	if strings.HasPrefix(strings.ToLower(uri), "file://") {
		parsed, err := url.Parse(uri)
		if err != nil {
			return "", err
		}
		path := filepath.FromSlash(parsed.Path)
		if parsed.Host != "" && !strings.EqualFold(parsed.Host, "localhost") {
			if len(parsed.Host) == 2 && parsed.Host[1] == ':' {
				path = filepath.FromSlash(parsed.Host + parsed.Path)
			} else {
				path = filepath.FromSlash("//" + parsed.Host + parsed.Path)
			}
		}
		if volume := filepath.VolumeName(path); volume == "" && len(path) > 2 && (path[0] == '/' || path[0] == '\\') && path[2] == ':' {
			path = path[1:]
		}
		return path, nil
	}
	if strings.Contains(uri, "://") {
		return "", fmt.Errorf("unsupported artifact URI scheme")
	}
	return filepath.Clean(uri), nil
}

func maskOutput(value string) string {
	for _, word := range []string{"password", "token", "secret"} {
		value = strings.ReplaceAll(value, word, "[masked]")
	}
	return value
}

type safeBuffer struct {
	mu sync.Mutex
	bytes.Buffer
}

func (b *safeBuffer) Write(value []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.Buffer.Write(value)
}

func (b *safeBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.Buffer.String()
}
