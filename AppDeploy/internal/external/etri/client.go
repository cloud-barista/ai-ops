package etri

import (
	"context"
	"sync"
	"time"

	"github.com/khu/ai-app-deployer/internal/external"
	"github.com/khu/ai-app-deployer/internal/model"
)

type MockClient struct {
	mu     sync.RWMutex
	status map[string]string
	logs   map[string][]model.DeploymentLog
	fail   map[string]error
}

const (
	MockStepCheck   = "check"
	MockStepPrepare = "prepare"
	MockStepDeploy  = "deploy"
	MockStepStatus  = "status"
	MockStepLogs    = "logs"
	MockStepStop    = "stop"
)

func NewClient() external.Client {
	return external.NewNotConfiguredClient(external.ProviderETRI)
}

func NewMockClient() *MockClient {
	return &MockClient{
		status: map[string]string{},
		logs:   map[string][]model.DeploymentLog{},
		fail:   map[string]error{},
	}
}

func (c *MockClient) Provider() external.Provider {
	return external.ProviderETRI
}

func (c *MockClient) SetFailure(step string, err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err == nil {
		delete(c.fail, step)
		return
	}
	c.fail[step] = err
}

func (c *MockClient) Check(ctx context.Context, target model.TargetProfile) (*external.CheckResult, error) {
	if err := c.failure(MockStepCheck); err != nil {
		return nil, err
	}
	return &external.CheckResult{
		Status:  "available",
		Message: "etri ai-infra mock readiness ok",
		Details: map[string]string{
			"target_profile_id": target.TargetProfileID,
		},
	}, nil
}

func (c *MockClient) Prepare(ctx context.Context, req external.PrepareRequest) (*external.PrepareResult, error) {
	if err := c.failure(MockStepPrepare); err != nil {
		return nil, err
	}
	return &external.PrepareResult{
		ArtifactRef: req.App.AppSpec.Artifact.URI,
		Message:     "etri ai-infra mock accepted artifact reference",
	}, nil
}

func (c *MockClient) Deploy(ctx context.Context, req external.DeployRequest) (*external.DeployResult, error) {
	if err := c.failure(MockStepDeploy); err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.status[req.DeploymentID] = model.StatusRunning
	c.logs[req.DeploymentID] = append(c.logs[req.DeploymentID], model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		RequestID:    req.RequestID,
		DeploymentID: req.DeploymentID,
		Component:    "etri-aiinfra-client",
		Stage:        model.StatusDeploying,
		Message:      "etri ai-infra mock accepted deployment request",
	})
	return &external.DeployResult{
		ExternalDeploymentID: "etri-aiinfra-" + req.DeploymentID,
		Status:               model.StatusRunning,
		Message:              "etri ai-infra mock deployment is running",
	}, nil
}

func (c *MockClient) Status(ctx context.Context, deploymentID string) (*external.StatusResult, error) {
	if err := c.failure(MockStepStatus); err != nil {
		return nil, err
	}
	c.mu.RLock()
	defer c.mu.RUnlock()
	status := c.status[deploymentID]
	if status == "" {
		status = model.StatusUnknown
	}
	return &external.StatusResult{Status: status, Message: "etri ai-infra mock status checked"}, nil
}

func (c *MockClient) Logs(ctx context.Context, deploymentID, stage string) ([]model.DeploymentLog, error) {
	if err := c.failure(MockStepLogs); err != nil {
		return nil, err
	}
	c.mu.RLock()
	defer c.mu.RUnlock()
	source := c.logs[deploymentID]
	items := make([]model.DeploymentLog, 0, len(source))
	for _, item := range source {
		if stage == "" || item.Stage == stage {
			items = append(items, item)
		}
	}
	return items, nil
}

func (c *MockClient) Stop(ctx context.Context, deploymentID string) error {
	if err := c.failure(MockStepStop); err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.status[deploymentID] = model.StatusStopped
	c.logs[deploymentID] = append(c.logs[deploymentID], model.DeploymentLog{
		Timestamp:    time.Now().UTC(),
		Level:        "INFO",
		DeploymentID: deploymentID,
		Component:    "etri-aiinfra-client",
		Stage:        model.StatusStopped,
		Message:      "etri ai-infra mock stop requested",
	})
	return nil
}

func (c *MockClient) failure(step string) error {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.fail[step]
}
