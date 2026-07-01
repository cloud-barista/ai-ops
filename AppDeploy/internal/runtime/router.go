package runtime

import (
	"context"
	"net/http"
	"sync"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type Router struct {
	mu                 sync.RWMutex
	defaultAdapter     Adapter
	byAdapterType      map[string]Adapter
	byRuntimeType      map[string]Adapter
	deploymentAdapters map[string]Adapter
}

func NewRouter(defaultAdapter Adapter) *Router {
	return &Router{
		defaultAdapter:     defaultAdapter,
		byAdapterType:      map[string]Adapter{},
		byRuntimeType:      map[string]Adapter{},
		deploymentAdapters: map[string]Adapter{},
	}
}

func (r *Router) RegisterAdapterType(adapterType string, adapter Adapter) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byAdapterType[adapterType] = adapter
}

func (r *Router) RegisterRuntimeType(runtimeType string, adapter Adapter) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byRuntimeType[runtimeType] = adapter
}

func (r *Router) ValidateTarget(ctx context.Context, target model.TargetProfile) error {
	adapter, err := r.adapterForTarget(target)
	if err != nil {
		return err
	}
	return adapter.ValidateTarget(ctx, target)
}

func (r *Router) HealthCheck(ctx context.Context, profile model.RuntimeProfile, target model.TargetProfile) error {
	adapter, err := r.adapterForProfile(profile, target)
	if err != nil {
		return err
	}
	return adapter.HealthCheck(ctx, profile, target)
}

func (r *Router) Prepare(ctx context.Context, app model.AppResponse, target model.TargetProfile) (*PrepareResult, error) {
	adapter, err := r.adapterForTarget(target)
	if err != nil {
		return nil, err
	}
	return adapter.Prepare(ctx, app, target)
}

func (r *Router) Deploy(ctx context.Context, plan DeploymentPlan) (*DeployResult, error) {
	adapter, err := r.adapterForProfile(plan.Runtime, plan.Target)
	if err != nil {
		return nil, err
	}
	result, err := adapter.Deploy(ctx, plan)
	if err != nil {
		return nil, err
	}
	r.mu.Lock()
	r.deploymentAdapters[plan.DeploymentID] = adapter
	r.mu.Unlock()
	return result, nil
}

func (r *Router) GetStatus(ctx context.Context, deploymentID string) (*RuntimeStatus, error) {
	return r.adapterForDeployment(deploymentID).GetStatus(ctx, deploymentID)
}

func (r *Router) GetLogs(ctx context.Context, deploymentID string, opt LogQuery) ([]model.DeploymentLog, error) {
	return r.adapterForDeployment(deploymentID).GetLogs(ctx, deploymentID, opt)
}

func (r *Router) Stop(ctx context.Context, plan StopPlan) error {
	adapter, err := r.adapterForProfile(plan.Runtime, plan.Target)
	if err != nil {
		return err
	}
	if err := adapter.Stop(ctx, plan); err != nil {
		return err
	}
	r.mu.Lock()
	r.deploymentAdapters[plan.DeploymentID] = adapter
	r.mu.Unlock()
	return nil
}

func (r *Router) adapterForProfile(profile model.RuntimeProfile, target model.TargetProfile) (Adapter, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if adapter := r.byAdapterType[profile.AdapterType]; adapter != nil {
		return adapter, nil
	}
	if adapter := r.byRuntimeType[profile.RuntimeType]; adapter != nil {
		return adapter, nil
	}
	if adapter := r.byRuntimeType[target.Runtime.RuntimeType]; adapter != nil {
		return adapter, nil
	}
	if r.defaultAdapter != nil {
		return r.defaultAdapter, nil
	}
	return nil, noAdapter(profile.RuntimeType)
}

func (r *Router) adapterForTarget(target model.TargetProfile) (Adapter, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if adapter := r.byRuntimeType[target.Runtime.RuntimeType]; adapter != nil {
		return adapter, nil
	}
	if r.defaultAdapter != nil {
		return r.defaultAdapter, nil
	}
	return nil, noAdapter(target.Runtime.RuntimeType)
}

func (r *Router) adapterForDeployment(deploymentID string) Adapter {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if adapter := r.deploymentAdapters[deploymentID]; adapter != nil {
		return adapter
	}
	return r.defaultAdapter
}

func noAdapter(runtimeType string) error {
	return apperrors.WithDetails(model.ErrRuntimeProfileInvalid, "runtime adapter is not registered", http.StatusBadRequest, false, map[string]any{
		"runtime_type": runtimeType,
	})
}
