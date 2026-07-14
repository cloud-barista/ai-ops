package store

import (
	"context"

	"github.com/khu/ai-app-deployer/internal/model"
)

type AppRepository interface {
	CreateApp(ctx context.Context, app model.AppResponse) error
	ListApps(ctx context.Context) ([]model.AppResponse, error)
	GetApp(ctx context.Context, appID string) (model.AppResponse, error)
	GetAppByVersionID(ctx context.Context, appVersionID string) (model.AppResponse, error)
	ExistsNameVersion(ctx context.Context, name, version string) (bool, error)
	DeleteApp(ctx context.Context, appID string) (model.AppResponse, error)
}

type ProfileRepository interface {
	CreateRuntimeProfile(ctx context.Context, profile model.RuntimeProfile) error
	ListRuntimeProfiles(ctx context.Context) ([]model.RuntimeProfile, error)
	GetRuntimeProfile(ctx context.Context, id string) (model.RuntimeProfile, error)
	DeleteRuntimeProfile(ctx context.Context, id string) (model.RuntimeProfile, error)
	CreateTargetProfile(ctx context.Context, profile model.TargetProfile) error
	ListTargetProfiles(ctx context.Context) ([]model.TargetProfile, error)
	GetTargetProfile(ctx context.Context, id string) (model.TargetProfile, error)
	DeleteTargetProfile(ctx context.Context, id string) (profile model.TargetProfile, inventoryDeleted bool, err error)
}

type DeploymentRepository interface {
	CreateDeployment(ctx context.Context, deployment model.DeploymentResponse) error
	UpdateDeployment(ctx context.Context, deployment model.DeploymentResponse) error
	ListDeployments(ctx context.Context) ([]model.DeploymentResponse, error)
	GetDeployment(ctx context.Context, id string) (model.DeploymentResponse, error)
	AddEvent(ctx context.Context, event model.DeploymentEvent) error
	ListEvents(ctx context.Context, deploymentID, stage string) ([]model.DeploymentEvent, error)
	SaveInventory(ctx context.Context, inventory model.ResourceInventory) error
	ListInventory(ctx context.Context) ([]model.ResourceInventory, error)
}

type MetricRepository interface {
	AddMetric(ctx context.Context, metric model.InferenceMetricRecord) error
	ListMetrics(ctx context.Context, deploymentID string) ([]model.InferenceMetricRecord, error)
	ListAllMetrics(ctx context.Context) ([]model.InferenceMetricRecord, error)
}
