package runtime

import (
	"context"

	"github.com/khu/ai-app-deployer/internal/model"
)

type Adapter interface {
	ValidateTarget(ctx context.Context, target model.TargetProfile) error
	HealthCheck(ctx context.Context, runtime model.RuntimeProfile, target model.TargetProfile) error
	Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*PrepareResult, error)
	Deploy(ctx context.Context, plan DeploymentPlan) (*DeployResult, error)
	GetStatus(ctx context.Context, deploymentID string) (*RuntimeStatus, error)
	GetLogs(ctx context.Context, deploymentID string, opt LogQuery) ([]model.DeploymentLog, error)
	Stop(ctx context.Context, plan StopPlan) error
}

type PrepareResult struct {
	ArtifactPath string
	Message      string
}

type DeployResult struct {
	RuntimeID string
	Message   string
}

type RuntimeStatus struct {
	Status  string
	Message string
}

type LogQuery struct {
	Stage string
}

type DeploymentPlan struct {
	DeploymentID string
	RequestID    string
	App          model.AppResponse
	Runtime      model.RuntimeProfile
	Target       model.TargetProfile
	Parameters   map[string]any
}

type StopPlan struct {
	DeploymentID string
	RequestID    string
	App          model.AppResponse
	Runtime      model.RuntimeProfile
	Target       model.TargetProfile
}
