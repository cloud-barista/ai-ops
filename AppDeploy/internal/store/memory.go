package store

import (
	"context"
	"sync"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type Memory struct {
	mu              sync.RWMutex
	appsByID        map[string]model.AppResponse
	appsByVersionID map[string]model.AppResponse
	appNameVersion  map[string]string
	targets         map[string]model.TargetProfile
	deployments     map[string]model.DeploymentResponse
	events          map[string][]model.DeploymentEvent
	inventory       map[string]model.ResourceInventory
	metrics         map[string][]model.InferenceMetricRecord
	reservations    map[string]map[string]model.ResourceAllocation
}

func NewMemory() *Memory {
	return &Memory{
		appsByID:        map[string]model.AppResponse{},
		appsByVersionID: map[string]model.AppResponse{},
		appNameVersion:  map[string]string{},
		targets:         map[string]model.TargetProfile{},
		deployments:     map[string]model.DeploymentResponse{},
		events:          map[string][]model.DeploymentEvent{},
		inventory:       map[string]model.ResourceInventory{},
		metrics:         map[string][]model.InferenceMetricRecord{},
		reservations:    map[string]map[string]model.ResourceAllocation{},
	}
}

func (m *Memory) CreateApp(ctx context.Context, app model.AppResponse) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	key := appNameVersionKey(app.Name, app.Version)
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
	return mapValues(m.appsByVersionID), nil
}

func (m *Memory) GetApp(ctx context.Context, appID string) (model.AppResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return mapValue(m.appsByID, appID, "app not found")
}

func (m *Memory) GetAppByVersionID(ctx context.Context, appVersionID string) (model.AppResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return mapValue(m.appsByVersionID, appVersionID, "app version not found")
}

func (m *Memory) ExistsNameVersion(ctx context.Context, name, version string) (bool, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	_, ok := m.appNameVersion[appNameVersionKey(name, version)]
	return ok, nil
}

func (m *Memory) DeleteApp(ctx context.Context, appID string) (model.AppResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	app, err := mapValue(m.appsByID, appID, "app not found")
	if err != nil {
		return model.AppResponse{}, err
	}

	if err := validateAppDeletion(m.deployments, app); err != nil {
		return model.AppResponse{}, err
	}

	delete(m.appsByID, app.AppID)
	delete(m.appsByVersionID, app.AppVersionID)
	delete(m.appNameVersion, appNameVersionKey(app.Name, app.Version))
	return app, nil
}

func (m *Memory) CreateTargetProfile(ctx context.Context, profile model.TargetProfile) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.targets[profile.TargetProfileID] = profile
	return nil
}

func (m *Memory) ReserveResources(ctx context.Context, targetProfileID, deploymentID string, allocation model.ResourceAllocation) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	target, err := mapValue(m.targets, targetProfileID, "target profile not found")
	if err != nil {
		return err
	}
	if m.reservations[targetProfileID] == nil {
		m.reservations[targetProfileID] = map[string]model.ResourceAllocation{}
	}
	if _, exists := m.reservations[targetProfileID][deploymentID]; exists {
		return nil
	}
	if !allocationFits(target, allocation) {
		return apperrors.New(model.ErrResourceInsufficient, "target profile capacity is already allocated", 409, true)
	}
	target.Allocated = addAllocation(target.Allocated, allocation)
	m.targets[targetProfileID] = target
	m.reservations[targetProfileID][deploymentID] = allocation
	return nil
}

func (m *Memory) ReleaseResources(ctx context.Context, targetProfileID, deploymentID string) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	target, err := mapValue(m.targets, targetProfileID, "target profile not found")
	if err != nil {
		return err
	}
	allocation, exists := m.reservations[targetProfileID][deploymentID]
	if !exists {
		return nil
	}
	target.Allocated = subtractAllocation(target.Allocated, allocation)
	m.targets[targetProfileID] = target
	delete(m.reservations[targetProfileID], deploymentID)
	return nil
}

func (m *Memory) ListTargetProfiles(ctx context.Context) ([]model.TargetProfile, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return mapValues(m.targets), nil
}

func (m *Memory) GetTargetProfile(ctx context.Context, id string) (model.TargetProfile, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return mapValue(m.targets, id, "target profile not found")
}

func (m *Memory) DeleteTargetProfile(ctx context.Context, id string) (model.TargetProfile, bool, error) {
	if err := contextError(ctx); err != nil {
		return model.TargetProfile{}, false, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := contextError(ctx); err != nil {
		return model.TargetProfile{}, false, err
	}

	profile, err := mapValue(m.targets, id, "target profile not found")
	if err != nil {
		return model.TargetProfile{}, false, err
	}
	if err := validateTargetProfileDeletion(m.deployments, id); err != nil {
		return model.TargetProfile{}, false, err
	}
	if err := contextError(ctx); err != nil {
		return model.TargetProfile{}, false, err
	}

	_, inventoryDeleted := m.inventory[id]
	delete(m.targets, id)
	delete(m.inventory, id)
	delete(m.reservations, id)
	return profile, inventoryDeleted, nil
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
	return mapValues(m.deployments), nil
}

func (m *Memory) GetDeployment(ctx context.Context, id string) (model.DeploymentResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return mapValue(m.deployments, id, "deployment not found")
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
	return filterEvents(m.events[deploymentID], stage), nil
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
	return mapValues(m.inventory), nil
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
	return cloneSlice(m.metrics[deploymentID]), nil
}

func (m *Memory) ListAllMetrics(ctx context.Context) ([]model.InferenceMetricRecord, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return flattenSlices(m.metrics), nil
}
