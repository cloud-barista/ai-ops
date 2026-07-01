package store

import (
	"context"
	"sync"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type AppRepository interface {
	CreateApp(ctx context.Context, app model.AppResponse) error
	ListApps(ctx context.Context) ([]model.AppResponse, error)
	GetApp(ctx context.Context, appID string) (model.AppResponse, error)
	GetAppByVersionID(ctx context.Context, appVersionID string) (model.AppResponse, error)
	ExistsNameVersion(ctx context.Context, name, version string) (bool, error)
}

type ProfileRepository interface {
	CreateRuntimeProfile(ctx context.Context, profile model.RuntimeProfile) error
	ListRuntimeProfiles(ctx context.Context) ([]model.RuntimeProfile, error)
	GetRuntimeProfile(ctx context.Context, id string) (model.RuntimeProfile, error)
	CreateTargetProfile(ctx context.Context, profile model.TargetProfile) error
	ListTargetProfiles(ctx context.Context) ([]model.TargetProfile, error)
	GetTargetProfile(ctx context.Context, id string) (model.TargetProfile, error)
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

type Memory struct {
	mu              sync.RWMutex
	appsByID        map[string]model.AppResponse
	appsByVersionID map[string]model.AppResponse
	appNameVersion  map[string]string
	runtimes        map[string]model.RuntimeProfile
	targets         map[string]model.TargetProfile
	deployments     map[string]model.DeploymentResponse
	events          map[string][]model.DeploymentEvent
	inventory       map[string]model.ResourceInventory
	metrics         map[string][]model.InferenceMetricRecord
}

func NewMemory() *Memory {
	return &Memory{
		appsByID:        map[string]model.AppResponse{},
		appsByVersionID: map[string]model.AppResponse{},
		appNameVersion:  map[string]string{},
		runtimes:        map[string]model.RuntimeProfile{},
		targets:         map[string]model.TargetProfile{},
		deployments:     map[string]model.DeploymentResponse{},
		events:          map[string][]model.DeploymentEvent{},
		inventory:       map[string]model.ResourceInventory{},
		metrics:         map[string][]model.InferenceMetricRecord{},
	}
}

func (m *Memory) CreateApp(ctx context.Context, app model.AppResponse) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	key := app.Name + ":" + app.Version
	if _, ok := m.appNameVersion[key]; ok {
		return apperrors.New(model.ErrAppSpecInvalid, "app name/version already exists", 400, false)
	}
	m.appsByID[app.AppID] = app
	m.appsByVersionID[app.AppVersionID] = app
	m.appNameVersion[key] = app.AppVersionID
	return nil
}

func (m *Memory) ListApps(ctx context.Context) ([]model.AppResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	items := make([]model.AppResponse, 0, len(m.appsByVersionID))
	for _, app := range m.appsByVersionID {
		items = append(items, app)
	}
	return items, nil
}

func (m *Memory) GetApp(ctx context.Context, appID string) (model.AppResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	app, ok := m.appsByID[appID]
	if !ok {
		return model.AppResponse{}, apperrors.New("NOT_FOUND", "app not found", 404, false)
	}
	return app, nil
}

func (m *Memory) GetAppByVersionID(ctx context.Context, appVersionID string) (model.AppResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	app, ok := m.appsByVersionID[appVersionID]
	if !ok {
		return model.AppResponse{}, apperrors.New("NOT_FOUND", "app version not found", 404, false)
	}
	return app, nil
}

func (m *Memory) ExistsNameVersion(ctx context.Context, name, version string) (bool, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	_, ok := m.appNameVersion[name+":"+version]
	return ok, nil
}

func (m *Memory) CreateRuntimeProfile(ctx context.Context, profile model.RuntimeProfile) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.runtimes[profile.RuntimeProfileID] = profile
	return nil
}

func (m *Memory) ListRuntimeProfiles(ctx context.Context) ([]model.RuntimeProfile, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	items := make([]model.RuntimeProfile, 0, len(m.runtimes))
	for _, item := range m.runtimes {
		items = append(items, item)
	}
	return items, nil
}

func (m *Memory) GetRuntimeProfile(ctx context.Context, id string) (model.RuntimeProfile, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	profile, ok := m.runtimes[id]
	if !ok {
		return model.RuntimeProfile{}, apperrors.New("NOT_FOUND", "runtime profile not found", 404, false)
	}
	return profile, nil
}

func (m *Memory) CreateTargetProfile(ctx context.Context, profile model.TargetProfile) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.targets[profile.TargetProfileID] = profile
	return nil
}

func (m *Memory) ListTargetProfiles(ctx context.Context) ([]model.TargetProfile, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	items := make([]model.TargetProfile, 0, len(m.targets))
	for _, item := range m.targets {
		items = append(items, item)
	}
	return items, nil
}

func (m *Memory) GetTargetProfile(ctx context.Context, id string) (model.TargetProfile, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	profile, ok := m.targets[id]
	if !ok {
		return model.TargetProfile{}, apperrors.New("NOT_FOUND", "target profile not found", 404, false)
	}
	return profile, nil
}

func (m *Memory) CreateDeployment(ctx context.Context, deployment model.DeploymentResponse) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.deployments[deployment.DeploymentID] = deployment
	return nil
}

func (m *Memory) UpdateDeployment(ctx context.Context, deployment model.DeploymentResponse) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.deployments[deployment.DeploymentID]; !ok {
		return apperrors.New("NOT_FOUND", "deployment not found", 404, false)
	}
	m.deployments[deployment.DeploymentID] = deployment
	return nil
}

func (m *Memory) ListDeployments(ctx context.Context) ([]model.DeploymentResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	items := make([]model.DeploymentResponse, 0, len(m.deployments))
	for _, item := range m.deployments {
		items = append(items, item)
	}
	return items, nil
}

func (m *Memory) GetDeployment(ctx context.Context, id string) (model.DeploymentResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	deployment, ok := m.deployments[id]
	if !ok {
		return model.DeploymentResponse{}, apperrors.New("NOT_FOUND", "deployment not found", 404, false)
	}
	return deployment, nil
}

func (m *Memory) AddEvent(ctx context.Context, event model.DeploymentEvent) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.events[event.DeploymentID] = append(m.events[event.DeploymentID], event)
	return nil
}

func (m *Memory) ListEvents(ctx context.Context, deploymentID, stage string) ([]model.DeploymentEvent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	events := m.events[deploymentID]
	items := make([]model.DeploymentEvent, 0, len(events))
	for _, event := range events {
		if stage == "" || event.Stage == stage {
			items = append(items, event)
		}
	}
	return items, nil
}

func (m *Memory) SaveInventory(ctx context.Context, inventory model.ResourceInventory) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.inventory[inventory.TargetProfileID] = inventory
	return nil
}

func (m *Memory) ListInventory(ctx context.Context) ([]model.ResourceInventory, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	items := make([]model.ResourceInventory, 0, len(m.inventory))
	for _, item := range m.inventory {
		items = append(items, item)
	}
	return items, nil
}

func (m *Memory) AddMetric(ctx context.Context, metric model.InferenceMetricRecord) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.metrics[metric.DeploymentID] = append(m.metrics[metric.DeploymentID], metric)
	return nil
}

func (m *Memory) ListMetrics(ctx context.Context, deploymentID string) ([]model.InferenceMetricRecord, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	source := m.metrics[deploymentID]
	items := make([]model.InferenceMetricRecord, len(source))
	copy(items, source)
	return items, nil
}

func (m *Memory) ListAllMetrics(ctx context.Context) ([]model.InferenceMetricRecord, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	var count int
	for _, source := range m.metrics {
		count += len(source)
	}
	items := make([]model.InferenceMetricRecord, 0, count)
	for _, source := range m.metrics {
		items = append(items, source...)
	}
	return items, nil
}
