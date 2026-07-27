package provider

import (
	"context"

	"github.com/khu/ai-app-deployer/internal/model"
	runtimepkg "github.com/khu/ai-app-deployer/internal/runtime"
)

// ResourceInformationProvider is the control-plane boundary for VM/node
// metadata. Local and ETRI implementations can be swapped without changing
// deployment orchestration.
type ResourceInformationProvider interface {
	ListResources(ctx context.Context, filter model.ResourceFilter) ([]model.NodeProfile, error)
	GetResource(ctx context.Context, id string) (model.NodeProfile, error)
}

// PlacementProvider decides where a deployment should run. A provider may
// reserve capacity before returning a decision, as LocalPlacementProvider does.
type PlacementProvider interface {
	Place(ctx context.Context, req model.PlacementRequest) (model.PlacementDecision, error)
}

// ResourceReservationProvider is intentionally separate from PlacementProvider
// so remote placement decisions do not need to implement local bookkeeping.
type ResourceReservationProvider interface {
	ReleaseDeployment(ctx context.Context, decision model.PlacementDecision, deploymentID string) error
}

// RuntimeAdapter names the existing runtime adapter contract at the provider
// boundary. The concrete method set remains in internal/runtime for backward
// compatibility with CPU/GPU/Mock/ETRI adapters already in the repository.
type RuntimeAdapter = runtimepkg.Adapter
