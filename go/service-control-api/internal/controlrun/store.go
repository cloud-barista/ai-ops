package controlrun

import (
	"fmt"
	"sync"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type Store struct {
	mu    sync.RWMutex
	runs  map[string]Run
	order []string
}

func NewStore() *Store {
	return &Store{runs: map[string]Run{}}
}

func (store *Store) Create(input CreateInput) Run {
	store.mu.Lock()
	defer store.mu.Unlock()

	now := time.Now().UTC()
	run := Run{
		RunID:     input.RunID,
		Status:    StatusReceived,
		CreatedAt: now,
		UpdatedAt: now,
		Request:   input.Request,
		Stages:    []Stage{},
	}
	store.runs[run.RunID] = cloneRun(run)
	store.order = append(store.order, run.RunID)
	return cloneRun(run)
}

func (store *Store) Get(runID string) (Run, bool) {
	store.mu.RLock()
	defer store.mu.RUnlock()

	run, ok := store.runs[runID]
	return cloneRun(run), ok
}

func (store *Store) List() []Run {
	store.mu.RLock()
	defer store.mu.RUnlock()

	runs := make([]Run, 0, len(store.order))
	for index := len(store.order) - 1; index >= 0; index-- {
		runID := store.order[index]
		if run, ok := store.runs[runID]; ok {
			runs = append(runs, cloneRun(run))
		}
	}
	return runs
}

func (store *Store) Update(runID string, mutate func(*Run) error) (Run, error) {
	store.mu.Lock()
	defer store.mu.Unlock()

	current, ok := store.runs[runID]
	if !ok {
		return Run{}, fmt.Errorf("control run not found: %s", runID)
	}
	next := cloneRun(current)
	if err := mutate(&next); err != nil {
		return Run{}, err
	}
	next.UpdatedAt = time.Now().UTC()
	store.runs[runID] = cloneRun(next)
	return cloneRun(next), nil
}

func (store *Store) Delete(runID string) (Run, bool) {
	store.mu.Lock()
	defer store.mu.Unlock()

	run, ok := store.runs[runID]
	if !ok {
		return Run{}, false
	}
	delete(store.runs, runID)
	for index, orderedID := range store.order {
		if orderedID == runID {
			store.order = append(store.order[:index], store.order[index+1:]...)
			break
		}
	}
	return cloneRun(run), true
}

func (store *Store) Clear() int {
	store.mu.Lock()
	defer store.mu.Unlock()

	count := len(store.runs)
	store.runs = map[string]Run{}
	store.order = nil
	return count
}

func cloneRun(source Run) Run {
	result := source
	result.RequestGuard.Checks = append([]plannerguard.Check(nil), source.RequestGuard.Checks...)
	result.Generation.Manifest = cloneManifest(source.Generation.Manifest)
	result.Manifest = cloneManifest(source.Manifest)
	if source.Application != nil {
		application := *source.Application
		application.AppSpec = append([]byte(nil), source.Application.AppSpec...)
		if source.Application.Package != nil {
			packageResult := *source.Application.Package
			packageResult.AppSpec = append([]byte(nil), source.Application.Package.AppSpec...)
			application.Package = &packageResult
		}
		if source.Application.Registration != nil {
			registration := *source.Application.Registration
			registration.AppSpec = append([]byte(nil), source.Application.Registration.AppSpec...)
			application.Registration = &registration
		}
		result.Application = &application
	}
	if source.PartialResult != nil {
		partialResult := *source.PartialResult
		result.PartialResult = &partialResult
	}
	if source.Execution != nil {
		execution := *source.Execution
		execution.Proposal = cloneMap(source.Execution.Proposal)
		execution.Result = cloneMap(source.Execution.Result)
		execution.Evidence = cloneMap(source.Execution.Evidence)
		result.Execution = &execution
	}
	if source.Deployment != nil {
		deployment := *source.Deployment
		deployment.Manifest = cloneManifest(source.Deployment.Manifest)
		result.Deployment = &deployment
	}
	if source.Polling != nil {
		polling := *source.Polling
		result.Polling = &polling
	}
	result.Logs = append([]appdeploy.DeploymentLog(nil), source.Logs...)
	result.CorrelationIDs = append([]string(nil), source.CorrelationIDs...)
	result.Stages = make([]Stage, len(source.Stages))
	for index, stage := range source.Stages {
		result.Stages[index] = stage
		if stage.EndedAt != nil {
			endedAt := *stage.EndedAt
			result.Stages[index].EndedAt = &endedAt
		}
		result.Stages[index].Details = cloneMap(stage.Details)
	}
	return result
}

func cloneManifest(source appdeploy.DeploymentManifest) appdeploy.DeploymentManifest {
	result := source
	result.Spec.Parameters = cloneMap(source.Spec.Parameters)
	return result
}

func cloneMap(source map[string]any) map[string]any {
	if source == nil {
		return nil
	}
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = cloneValue(value)
	}
	return result
}

func cloneValue(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		return cloneMap(typed)
	case []any:
		result := make([]any, len(typed))
		for index, item := range typed {
			result[index] = cloneValue(item)
		}
		return result
	case []string:
		return append([]string(nil), typed...)
	default:
		return typed
	}
}
