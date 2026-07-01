package mock

import (
	"context"
	"sync"
	"time"

	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
)

type Adapter struct {
	mu     sync.RWMutex
	status map[string]string
	logs   map[string][]model.DeploymentLog
}

func New() *Adapter {
	return &Adapter{
		status: map[string]string{},
		logs:   map[string][]model.DeploymentLog{},
	}
}

func (a *Adapter) ValidateTarget(ctx context.Context, target model.TargetProfile) error {
	return nil
}

func (a *Adapter) HealthCheck(ctx context.Context, profile model.RuntimeProfile, target model.TargetProfile) error {
	return nil
}

func (a *Adapter) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*runtime.PrepareResult, error) {
	return &runtime.PrepareResult{
		ArtifactPath: app.AppSpec.Artifact.URI,
		Message:      "mock runtime prepared artifact and model references",
	}, nil
}

func (a *Adapter) Deploy(ctx context.Context, plan runtime.DeploymentPlan) (*runtime.DeployResult, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.status[plan.DeploymentID] = model.StatusRunning
	a.logs[plan.DeploymentID] = append(a.logs[plan.DeploymentID], model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    plan.RequestID,
		DeploymentID: plan.DeploymentID,
		Component:    "runtime-adapter",
		Stage:        model.StatusDeploying,
		Message:      "mock runtime accepted deployment request",
	})
	return &runtime.DeployResult{
		RuntimeID: "mock-" + plan.DeploymentID,
		Message:   "mock runtime deployment is running",
	}, nil
}

func (a *Adapter) GetStatus(ctx context.Context, deploymentID string) (*runtime.RuntimeStatus, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	status := a.status[deploymentID]
	if status == "" {
		status = model.StatusUnknown
	}
	return &runtime.RuntimeStatus{Status: status, Message: "mock runtime status checked"}, nil
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
	a.mu.Lock()
	defer a.mu.Unlock()
	a.status[plan.DeploymentID] = model.StatusStopped
	a.logs[plan.DeploymentID] = append(a.logs[plan.DeploymentID], model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    plan.RequestID,
		DeploymentID: plan.DeploymentID,
		Component:    "runtime-adapter",
		Stage:        model.StatusStopped,
		Message:      "mock runtime stopped deployment",
	})
	return nil
}
