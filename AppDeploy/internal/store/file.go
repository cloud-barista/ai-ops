package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/khu/ai-app-deployer/internal/credentialref"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type File struct {
	mu   sync.RWMutex
	path string
	data fileData
}

type fileData struct {
	AppsByID        map[string]model.AppResponse                   `json:"apps_by_id"`
	AppsByVersionID map[string]model.AppResponse                   `json:"apps_by_version_id"`
	AppNameVersion  map[string]string                              `json:"app_name_version"`
	OriginalApps    map[string]string                              `json:"original_applications,omitempty"`
	Targets         map[string]model.TargetProfile                 `json:"targets"`
	Deployments     map[string]model.DeploymentResponse            `json:"deployments"`
	Events          map[string][]model.DeploymentEvent             `json:"events"`
	Inventory       map[string]model.ResourceInventory             `json:"inventory"`
	Metrics         map[string][]model.InferenceMetricRecord       `json:"metrics"`
	Reservations    map[string]map[string]model.ResourceAllocation `json:"reservations"`
}

func NewFile(path string) (*File, error) {
	store := &File{
		path: path,
		data: newFileData(),
	}
	if err := store.load(); err != nil {
		return nil, err
	}
	return store, nil
}

func newFileData() fileData {
	return fileData{
		AppsByID:        map[string]model.AppResponse{},
		AppsByVersionID: map[string]model.AppResponse{},
		AppNameVersion:  map[string]string{},
		OriginalApps:    map[string]string{},
		Targets:         map[string]model.TargetProfile{},
		Deployments:     map[string]model.DeploymentResponse{},
		Events:          map[string][]model.DeploymentEvent{},
		Inventory:       map[string]model.ResourceInventory{},
		Metrics:         map[string][]model.InferenceMetricRecord{},
		Reservations:    map[string]map[string]model.ResourceAllocation{},
	}
}

func (f *File) load() error {
	if f.path == "" {
		return errors.New("store path is empty")
	}
	raw, err := os.ReadFile(f.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if len(raw) == 0 {
		return nil
	}
	if err := json.Unmarshal(raw, &f.data); err != nil {
		return err
	}
	f.ensureMaps()
	for appID, raw := range f.data.OriginalApps {
		app, ok := f.data.AppsByID[appID]
		if !ok {
			continue
		}
		app.OriginalApplication = json.RawMessage(raw)
		f.data.AppsByID[appID] = app
		f.data.AppsByVersionID[app.AppVersionID] = app
	}
	for _, target := range f.data.Targets {
		if !credentialref.Valid(target.VM.CredentialRef) {
			return errors.New("stored target profile contains an invalid credential_ref")
		}
	}
	return nil
}

func (f *File) ensureMaps() {
	ensureMap(&f.data.AppsByID)
	ensureMap(&f.data.AppsByVersionID)
	ensureMap(&f.data.AppNameVersion)
	ensureMap(&f.data.OriginalApps)
	ensureMap(&f.data.Targets)
	ensureMap(&f.data.Deployments)
	ensureMap(&f.data.Events)
	ensureMap(&f.data.Inventory)
	ensureMap(&f.data.Metrics)
	ensureMap(&f.data.Reservations)
}

func (f *File) saveLocked() error {
	f.ensureMaps()
	f.data.OriginalApps = make(map[string]string, len(f.data.AppsByID))
	for appID, app := range f.data.AppsByID {
		if len(app.OriginalApplication) > 0 {
			f.data.OriginalApps[appID] = string(app.OriginalApplication)
		}
	}
	raw, err := json.MarshalIndent(f.data, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(f.path), 0o755); err != nil {
		return err
	}
	tmp := f.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, f.path)
}

func (f *File) CreateApp(ctx context.Context, app model.AppResponse) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	key := appNameVersionKey(app.Name, app.Version)
	if _, ok := f.data.AppNameVersion[key]; ok {
		return apperrors.New(model.ErrAppSpecInvalid, "app name/version already exists", 400, false)
	}
	f.data.AppsByID[app.AppID] = app
	f.data.AppsByVersionID[app.AppVersionID] = app
	f.data.AppNameVersion[key] = app.AppVersionID
	return f.saveLocked()
}

func (f *File) ListApps(ctx context.Context) ([]model.AppResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValues(f.data.AppsByVersionID), nil
}

func (f *File) GetApp(ctx context.Context, appID string) (model.AppResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValue(f.data.AppsByID, appID, "app not found")
}

func (f *File) GetAppByVersionID(ctx context.Context, appVersionID string) (model.AppResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValue(f.data.AppsByVersionID, appVersionID, "app version not found")
}

func (f *File) ExistsNameVersion(ctx context.Context, name, version string) (bool, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	_, ok := f.data.AppNameVersion[appNameVersionKey(name, version)]
	return ok, nil
}

func (f *File) DeleteApp(ctx context.Context, appID string) (model.AppResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	app, err := mapValue(f.data.AppsByID, appID, "app not found")
	if err != nil {
		return model.AppResponse{}, err
	}

	if err := validateAppDeletion(f.data.Deployments, app); err != nil {
		return model.AppResponse{}, err
	}

	key := appNameVersionKey(app.Name, app.Version)
	nameVersionID, hadNameVersion := f.data.AppNameVersion[key]
	delete(f.data.AppsByID, app.AppID)
	delete(f.data.AppsByVersionID, app.AppVersionID)
	delete(f.data.AppNameVersion, key)
	if err := f.saveLocked(); err != nil {
		f.data.AppsByID[app.AppID] = app
		f.data.AppsByVersionID[app.AppVersionID] = app
		if hadNameVersion {
			f.data.AppNameVersion[key] = nameVersionID
		}
		return model.AppResponse{}, fmt.Errorf("persist app deletion: %w", err)
	}
	return app, nil
}

func (f *File) CreateTargetProfile(ctx context.Context, profile model.TargetProfile) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Targets[profile.TargetProfileID] = profile
	return f.saveLocked()
}

func (f *File) ReserveResources(ctx context.Context, targetProfileID, deploymentID string, allocation model.ResourceAllocation) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	target, err := mapValue(f.data.Targets, targetProfileID, "target profile not found")
	if err != nil {
		return err
	}
	if f.data.Reservations[targetProfileID] == nil {
		f.data.Reservations[targetProfileID] = map[string]model.ResourceAllocation{}
	}
	if _, exists := f.data.Reservations[targetProfileID][deploymentID]; exists {
		return nil
	}
	if !allocationFits(target, allocation) {
		return apperrors.New(model.ErrResourceInsufficient, "target profile capacity is already allocated", 409, true)
	}
	target.Allocated = addAllocation(target.Allocated, allocation)
	f.data.Targets[targetProfileID] = target
	f.data.Reservations[targetProfileID][deploymentID] = allocation
	return f.saveLocked()
}

func (f *File) ReleaseResources(ctx context.Context, targetProfileID, deploymentID string) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	target, err := mapValue(f.data.Targets, targetProfileID, "target profile not found")
	if err != nil {
		return err
	}
	allocation, exists := f.data.Reservations[targetProfileID][deploymentID]
	if !exists {
		return nil
	}
	target.Allocated = subtractAllocation(target.Allocated, allocation)
	f.data.Targets[targetProfileID] = target
	delete(f.data.Reservations[targetProfileID], deploymentID)
	return f.saveLocked()
}

func (f *File) ListTargetProfiles(ctx context.Context) ([]model.TargetProfile, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValues(f.data.Targets), nil
}

func (f *File) GetTargetProfile(ctx context.Context, id string) (model.TargetProfile, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValue(f.data.Targets, id, "target profile not found")
}

func (f *File) DeleteTargetProfile(ctx context.Context, id string) (model.TargetProfile, bool, error) {
	if err := contextError(ctx); err != nil {
		return model.TargetProfile{}, false, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := contextError(ctx); err != nil {
		return model.TargetProfile{}, false, err
	}

	profile, err := mapValue(f.data.Targets, id, "target profile not found")
	if err != nil {
		return model.TargetProfile{}, false, err
	}
	if err := validateTargetProfileDeletion(f.data.Deployments, id); err != nil {
		return model.TargetProfile{}, false, err
	}
	if err := contextError(ctx); err != nil {
		return model.TargetProfile{}, false, err
	}

	inventory, inventoryDeleted := f.data.Inventory[id]
	reservations, hadReservations := f.data.Reservations[id]
	delete(f.data.Targets, id)
	delete(f.data.Inventory, id)
	delete(f.data.Reservations, id)
	if err := f.saveLocked(); err != nil {
		f.data.Targets[id] = profile
		if inventoryDeleted {
			f.data.Inventory[id] = inventory
		}
		if hadReservations {
			f.data.Reservations[id] = reservations
		}
		return model.TargetProfile{}, false, fmt.Errorf("persist target profile deletion: %w", err)
	}
	return profile, inventoryDeleted, nil
}

func (f *File) CreateDeployment(ctx context.Context, deployment model.DeploymentResponse) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Deployments[deployment.DeploymentID] = deployment
	return f.saveLocked()
}

func (f *File) UpdateDeployment(ctx context.Context, deployment model.DeploymentResponse) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.data.Deployments[deployment.DeploymentID]; !ok {
		return apperrors.New("NOT_FOUND", "deployment not found", 404, false)
	}
	f.data.Deployments[deployment.DeploymentID] = deployment
	return f.saveLocked()
}

func (f *File) ListDeployments(ctx context.Context) ([]model.DeploymentResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValues(f.data.Deployments), nil
}

func (f *File) GetDeployment(ctx context.Context, id string) (model.DeploymentResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValue(f.data.Deployments, id, "deployment not found")
}

func (f *File) AddEvent(ctx context.Context, event model.DeploymentEvent) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Events[event.DeploymentID] = append(f.data.Events[event.DeploymentID], event)
	return f.saveLocked()
}

func (f *File) ListEvents(ctx context.Context, deploymentID, stage string) ([]model.DeploymentEvent, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return filterEvents(f.data.Events[deploymentID], stage), nil
}

func (f *File) SaveInventory(ctx context.Context, inventory model.ResourceInventory) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Inventory[inventory.TargetProfileID] = inventory
	return f.saveLocked()
}

func (f *File) ListInventory(ctx context.Context) ([]model.ResourceInventory, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return mapValues(f.data.Inventory), nil
}

func (f *File) AddMetric(ctx context.Context, metric model.InferenceMetricRecord) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Metrics[metric.DeploymentID] = append(f.data.Metrics[metric.DeploymentID], metric)
	return f.saveLocked()
}

func (f *File) ListMetrics(ctx context.Context, deploymentID string) ([]model.InferenceMetricRecord, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return cloneSlice(f.data.Metrics[deploymentID]), nil
}

func (f *File) ListAllMetrics(ctx context.Context) ([]model.InferenceMetricRecord, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return flattenSlices(f.data.Metrics), nil
}
