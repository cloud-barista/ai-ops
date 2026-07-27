package provider

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

func TestLocalPlacementFiltersInsufficientCPUAndGPUType(t *testing.T) {
	repo := store.NewMemory()
	createNode(t, repo, model.TargetProfile{
		TargetProfileID: "cpu-small", CSP: "local", Status: model.NodeStatusReady,
		Runtime:  model.TargetRuntime{RuntimeType: "cpu"},
		Capacity: model.ResourceCapacity{CPUCores: 1, MemoryBytes: 8 << 30, StorageBytes: 20 << 30},
	})
	createNode(t, repo, model.TargetProfile{
		TargetProfileID: "gpu-amd", CSP: "local", Status: model.NodeStatusReady,
		Runtime:  model.TargetRuntime{RuntimeType: "gpu", Accelerator: "nvidia"},
		Capacity: model.ResourceCapacity{CPUCores: 8, MemoryBytes: 32 << 30, GPUCount: 2, GPUType: "amd", StorageBytes: 100 << 30},
	})
	placement, err := NewLocalPlacementProvider(repo)
	if err != nil {
		t.Fatal(err)
	}
	_, err = placement.Place(context.Background(), model.PlacementRequest{
		DeploymentID: "dep-cpu-too-large", Runtime: "cpu",
		Resources: model.Resources{CPU: "2", Memory: "1Gi", Storage: "1Gi"}, CostPolicy: model.CostPolicyMinCost,
	})
	assertResourceError(t, err)
	_, err = placement.Place(context.Background(), model.PlacementRequest{
		DeploymentID: "dep-gpu-type", Runtime: "gpu", Accelerator: "nvidia",
		Resources: model.Resources{CPU: "1", Memory: "1Gi", GPU: "1", Storage: "1Gi"}, CostPolicy: model.CostPolicyMinCost,
	})
	assertResourceError(t, err)
}

func TestLocalPlacementChoosesCheapestThenRemainingThenID(t *testing.T) {
	repo := store.NewMemory()
	createNode(t, repo, model.TargetProfile{TargetProfileID: "expensive", CSP: "local", Status: model.NodeStatusReady, CostWeight: 5, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 8}})
	createNode(t, repo, model.TargetProfile{TargetProfileID: "cheap", CSP: "local", Status: model.NodeStatusReady, CostWeight: 1, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 2}})
	createNode(t, repo, model.TargetProfile{TargetProfileID: "cheap-b", CSP: "local", Status: model.NodeStatusReady, CostWeight: 1, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 2}})
	placement, err := NewLocalPlacementProvider(repo)
	if err != nil {
		t.Fatal(err)
	}
	decision, err := placement.Place(context.Background(), model.PlacementRequest{
		DeploymentID: "dep-cheapest", Runtime: "cpu", Resources: model.Resources{CPU: "1"}, CostPolicy: model.CostPolicyMinCost,
	})
	if err != nil {
		t.Fatal(err)
	}
	if decision.TargetVMID != "cheap" {
		t.Fatalf("selected %q, want cheap", decision.TargetVMID)
	}
	if !strings.Contains(decision.Reason, "min_cost") {
		t.Fatalf("placement reason does not explain policy: %q", decision.Reason)
	}

	if err := placement.ReleaseDeployment(context.Background(), decision, "dep-cheapest"); err != nil {
		t.Fatal(err)
	}
	// Equal cost and equal remaining capacity are resolved by VM ID.
	first, err := placement.Place(context.Background(), model.PlacementRequest{DeploymentID: "dep-id-a", Runtime: "cpu", Resources: model.Resources{CPU: "1"}})
	if err != nil || first.TargetVMID != "cheap" {
		t.Fatalf("deterministic ID tie break selected %+v, err=%v", first, err)
	}
}

func TestLocalPlacementUsesRemainingCapacityAfterCostTie(t *testing.T) {
	repo := store.NewMemory()
	createNode(t, repo, model.TargetProfile{TargetProfileID: "tie-small", CSP: "local", Status: model.NodeStatusReady, CostWeight: 1, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 2}})
	createNode(t, repo, model.TargetProfile{TargetProfileID: "tie-large", CSP: "local", Status: model.NodeStatusReady, CostWeight: 1, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 8}})
	placement, err := NewLocalPlacementProvider(repo)
	if err != nil {
		t.Fatal(err)
	}
	decision, err := placement.Place(context.Background(), model.PlacementRequest{DeploymentID: "dep-remaining", Runtime: "cpu", Resources: model.Resources{CPU: "1"}})
	if err != nil {
		t.Fatal(err)
	}
	if decision.TargetVMID != "tie-large" {
		t.Fatalf("selected %q, want tie-large", decision.TargetVMID)
	}
}

func TestLocalPlacementRejectsBusyAndInsufficientGPUCount(t *testing.T) {
	repo := store.NewMemory()
	createNode(t, repo, model.TargetProfile{TargetProfileID: "busy", CSP: "local", Status: model.NodeStatusBusy, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 8}})
	createNode(t, repo, model.TargetProfile{TargetProfileID: "one-gpu", CSP: "local", Status: model.NodeStatusReady, Runtime: model.TargetRuntime{RuntimeType: "gpu", Accelerator: "nvidia"}, Capacity: model.ResourceCapacity{CPUCores: 8, GPUCount: 1, GPUType: "nvidia"}})
	placement, err := NewLocalPlacementProvider(repo)
	if err != nil {
		t.Fatal(err)
	}
	_, err = placement.Place(context.Background(), model.PlacementRequest{DeploymentID: "dep-busy", Runtime: "cpu", Resources: model.Resources{CPU: "1"}})
	assertResourceError(t, err)
	_, err = placement.Place(context.Background(), model.PlacementRequest{DeploymentID: "dep-gpu-count", Runtime: "gpu", Accelerator: "nvidia", Resources: model.Resources{GPU: "2"}})
	assertResourceError(t, err)
}

func TestLocalPlacementConcurrentReservationAllowsOnlyOneAllocation(t *testing.T) {
	repo := store.NewMemory()
	createNode(t, repo, model.TargetProfile{TargetProfileID: "one-slot", CSP: "local", Status: model.NodeStatusReady, Runtime: model.TargetRuntime{RuntimeType: "cpu"}, Capacity: model.ResourceCapacity{CPUCores: 1}})
	placement, err := NewLocalPlacementProvider(repo)
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	winners := 0
	var winnerDecision model.PlacementDecision
	var winnerDeploymentID string
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			deploymentID := "dep-concurrent-" + string(rune('a'+index))
			decision, placeErr := placement.Place(context.Background(), model.PlacementRequest{DeploymentID: deploymentID, Runtime: "cpu", Resources: model.Resources{CPU: "1"}})
			if placeErr == nil {
				mu.Lock()
				winners++
				winnerDecision = decision
				winnerDeploymentID = deploymentID
				mu.Unlock()
			}
		}(i)
	}
	wg.Wait()
	if winners != 1 {
		t.Fatalf("successful concurrent reservations = %d, want 1", winners)
	}
	if err := placement.ReleaseDeployment(context.Background(), winnerDecision, winnerDeploymentID); err != nil {
		t.Fatal(err)
	}
	node, err := placement.GetResource(context.Background(), "one-slot")
	if err != nil {
		t.Fatal(err)
	}
	if node.Allocated.CPUCores != 0 {
		t.Fatalf("allocated CPU after release = %v, want 0", node.Allocated.CPUCores)
	}
}

func TestETRIProviderRequiresExplicitEndpoints(t *testing.T) {
	if _, err := NewETRIResourceMetadataAdapter(ETRIConfig{}); err == nil || !strings.Contains(err.Error(), "configuration") {
		t.Fatalf("missing ETRI resource endpoint error = %v", err)
	}
	if _, err := NewETRIPlacementAdapter(ETRIConfig{}); err == nil || !strings.Contains(err.Error(), "configuration") {
		t.Fatalf("missing ETRI placement endpoint error = %v", err)
	}
}

func createNode(t *testing.T, repo *store.Memory, target model.TargetProfile) {
	t.Helper()
	if err := repo.CreateTargetProfile(context.Background(), target); err != nil {
		t.Fatal(err)
	}
}

func assertResourceError(t *testing.T, err error) {
	t.Helper()
	if err == nil {
		t.Fatal("expected placement to fail")
	}
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) || appErr.Code != model.ErrResourceInsufficient {
		t.Fatalf("placement error = %v, want %s", err, model.ErrResourceInsufficient)
	}
}
