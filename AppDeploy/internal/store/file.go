package store

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type File struct {
	mu   sync.RWMutex
	path string
	data fileData
}

type fileData struct {
	AppsByID        map[string]model.AppResponse             `json:"apps_by_id"`
	AppsByVersionID map[string]model.AppResponse             `json:"apps_by_version_id"`
	AppNameVersion  map[string]string                        `json:"app_name_version"`
	Runtimes        map[string]model.RuntimeProfile          `json:"runtimes"`
	Targets         map[string]model.TargetProfile           `json:"targets"`
	Deployments     map[string]model.DeploymentResponse      `json:"deployments"`
	Events          map[string][]model.DeploymentEvent       `json:"events"`
	Inventory       map[string]model.ResourceInventory       `json:"inventory"`
	Metrics         map[string][]model.InferenceMetricRecord `json:"metrics"`
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
		Runtimes:        map[string]model.RuntimeProfile{},
		Targets:         map[string]model.TargetProfile{},
		Deployments:     map[string]model.DeploymentResponse{},
		Events:          map[string][]model.DeploymentEvent{},
		Inventory:       map[string]model.ResourceInventory{},
		Metrics:         map[string][]model.InferenceMetricRecord{},
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
	return nil
}

func (f *File) ensureMaps() {
	if f.data.AppsByID == nil {
		f.data.AppsByID = map[string]model.AppResponse{}
	}
	if f.data.AppsByVersionID == nil {
		f.data.AppsByVersionID = map[string]model.AppResponse{}
	}
	if f.data.AppNameVersion == nil {
		f.data.AppNameVersion = map[string]string{}
	}
	if f.data.Runtimes == nil {
		f.data.Runtimes = map[string]model.RuntimeProfile{}
	}
	if f.data.Targets == nil {
		f.data.Targets = map[string]model.TargetProfile{}
	}
	if f.data.Deployments == nil {
		f.data.Deployments = map[string]model.DeploymentResponse{}
	}
	if f.data.Events == nil {
		f.data.Events = map[string][]model.DeploymentEvent{}
	}
	if f.data.Inventory == nil {
		f.data.Inventory = map[string]model.ResourceInventory{}
	}
	if f.data.Metrics == nil {
		f.data.Metrics = map[string][]model.InferenceMetricRecord{}
	}
}

func (f *File) saveLocked() error {
	f.ensureMaps()
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
	key := app.Name + ":" + app.Version
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
	items := make([]model.AppResponse, 0, len(f.data.AppsByVersionID))
	for _, app := range f.data.AppsByVersionID {
		items = append(items, app)
	}
	return items, nil
}

func (f *File) GetApp(ctx context.Context, appID string) (model.AppResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	app, ok := f.data.AppsByID[appID]
	if !ok {
		return model.AppResponse{}, apperrors.New("NOT_FOUND", "app not found", 404, false)
	}
	return app, nil
}

func (f *File) GetAppByVersionID(ctx context.Context, appVersionID string) (model.AppResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	app, ok := f.data.AppsByVersionID[appVersionID]
	if !ok {
		return model.AppResponse{}, apperrors.New("NOT_FOUND", "app version not found", 404, false)
	}
	return app, nil
}

func (f *File) ExistsNameVersion(ctx context.Context, name, version string) (bool, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	_, ok := f.data.AppNameVersion[name+":"+version]
	return ok, nil
}

func (f *File) CreateRuntimeProfile(ctx context.Context, profile model.RuntimeProfile) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Runtimes[profile.RuntimeProfileID] = profile
	return f.saveLocked()
}

func (f *File) ListRuntimeProfiles(ctx context.Context) ([]model.RuntimeProfile, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	items := make([]model.RuntimeProfile, 0, len(f.data.Runtimes))
	for _, item := range f.data.Runtimes {
		items = append(items, item)
	}
	return items, nil
}

func (f *File) GetRuntimeProfile(ctx context.Context, id string) (model.RuntimeProfile, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	profile, ok := f.data.Runtimes[id]
	if !ok {
		return model.RuntimeProfile{}, apperrors.New("NOT_FOUND", "runtime profile not found", 404, false)
	}
	return profile, nil
}

func (f *File) CreateTargetProfile(ctx context.Context, profile model.TargetProfile) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.data.Targets[profile.TargetProfileID] = profile
	return f.saveLocked()
}

func (f *File) ListTargetProfiles(ctx context.Context) ([]model.TargetProfile, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	items := make([]model.TargetProfile, 0, len(f.data.Targets))
	for _, item := range f.data.Targets {
		items = append(items, item)
	}
	return items, nil
}

func (f *File) GetTargetProfile(ctx context.Context, id string) (model.TargetProfile, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	profile, ok := f.data.Targets[id]
	if !ok {
		return model.TargetProfile{}, apperrors.New("NOT_FOUND", "target profile not found", 404, false)
	}
	return profile, nil
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
	items := make([]model.DeploymentResponse, 0, len(f.data.Deployments))
	for _, item := range f.data.Deployments {
		items = append(items, item)
	}
	return items, nil
}

func (f *File) GetDeployment(ctx context.Context, id string) (model.DeploymentResponse, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	deployment, ok := f.data.Deployments[id]
	if !ok {
		return model.DeploymentResponse{}, apperrors.New("NOT_FOUND", "deployment not found", 404, false)
	}
	return deployment, nil
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
	events := f.data.Events[deploymentID]
	items := make([]model.DeploymentEvent, 0, len(events))
	for _, event := range events {
		if stage == "" || event.Stage == stage {
			items = append(items, event)
		}
	}
	return items, nil
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
	items := make([]model.ResourceInventory, 0, len(f.data.Inventory))
	for _, item := range f.data.Inventory {
		items = append(items, item)
	}
	return items, nil
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
	source := f.data.Metrics[deploymentID]
	items := make([]model.InferenceMetricRecord, len(source))
	copy(items, source)
	return items, nil
}

func (f *File) ListAllMetrics(ctx context.Context) ([]model.InferenceMetricRecord, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	var count int
	for _, source := range f.data.Metrics {
		count += len(source)
	}
	items := make([]model.InferenceMetricRecord, 0, count)
	for _, source := range f.data.Metrics {
		items = append(items, source...)
	}
	return items, nil
}
