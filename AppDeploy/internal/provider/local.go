package provider

import (
	"context"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

type LocalPlacementProvider struct {
	profiles     store.ProfileRepository
	reservations store.ResourceReservationRepository
}

func NewLocalPlacementProvider(profiles store.ProfileRepository) (*LocalPlacementProvider, error) {
	reservations, ok := profiles.(store.ResourceReservationRepository)
	if !ok {
		return nil, fmt.Errorf("local placement provider requires a resource reservation repository")
	}
	return &LocalPlacementProvider{profiles: profiles, reservations: reservations}, nil
}

func (p *LocalPlacementProvider) ListResources(ctx context.Context, filter model.ResourceFilter) ([]model.NodeProfile, error) {
	allocation, err := allocationFor(filter.Resources)
	if err != nil {
		return nil, err
	}
	targets, err := p.profiles.ListTargetProfiles(ctx)
	if err != nil {
		return nil, err
	}
	items := make([]model.NodeProfile, 0, len(targets))
	for _, target := range targets {
		node := nodeFromTarget(target)
		if ready(node) && matchesFilter(node, filter) && capacityFits(node, allocation) && acceleratorMatches(node, filter.Accelerator) {
			items = append(items, node)
		}
	}
	sort.Slice(items, func(i, j int) bool { return items[i].VMID < items[j].VMID })
	return items, nil
}

func (p *LocalPlacementProvider) GetResource(ctx context.Context, id string) (model.NodeProfile, error) {
	target, err := p.profiles.GetTargetProfile(ctx, id)
	if err != nil {
		return model.NodeProfile{}, err
	}
	return nodeFromTarget(target), nil
}

func (p *LocalPlacementProvider) Place(ctx context.Context, req model.PlacementRequest) (model.PlacementDecision, error) {
	if strings.TrimSpace(req.CostPolicy) == "" {
		req.CostPolicy = model.CostPolicyMinCost
	}
	if req.CostPolicy != model.CostPolicyMinCost {
		return model.PlacementDecision{}, apperrors.New(model.ErrDeploymentFailed, "cost_policy must be min_cost", http.StatusBadRequest, false)
	}
	allocation, err := allocationFor(req.Resources)
	if err != nil {
		return model.PlacementDecision{}, err
	}
	nodes, err := p.ListResources(ctx, model.ResourceFilter{
		Runtime: req.Runtime, Accelerator: req.Accelerator, Resources: req.Resources,
		Labels: req.Labels, TargetVMID: req.TargetVMID,
	})
	if err != nil {
		return model.PlacementDecision{}, err
	}
	if req.TargetVMID != "" && len(nodes) == 0 {
		if _, lookupErr := p.profiles.GetTargetProfile(ctx, req.TargetVMID); lookupErr != nil {
			return model.PlacementDecision{}, apperrors.WithDetails(
				model.ErrTargetProfileInvalid,
				"target profile hint was not found",
				http.StatusNotFound,
				false,
				map[string]any{"target_profile_id": req.TargetVMID},
			)
		}
	}
	candidates := make([]candidate, 0, len(nodes))
	for _, node := range nodes {
		candidates = append(candidates, candidate{node: node, remaining: remainingScore(node, allocation)})
	}
	if len(candidates) == 0 {
		return model.PlacementDecision{}, apperrors.WithDetails(
			model.ErrResourceInsufficient,
			"no READY VM satisfies the deployment requirements",
			http.StatusBadRequest,
			false,
			map[string]any{"runtime": req.Runtime, "cost_policy": req.CostPolicy},
		)
	}
	sort.Slice(candidates, func(i, j int) bool {
		left, right := candidates[i], candidates[j]
		if math.Abs(left.node.CostWeight-right.node.CostWeight) > 1e-9 {
			return left.node.CostWeight < right.node.CostWeight
		}
		if math.Abs(left.remaining-right.remaining) > 1e-9 {
			return left.remaining > right.remaining
		}
		return left.node.VMID < right.node.VMID
	})

	for _, item := range candidates {
		if err := p.reservations.ReserveResources(ctx, item.node.VMID, req.DeploymentID, allocation); err != nil {
			if appErr, ok := err.(*apperrors.AppError); ok && appErr.Code == model.ErrResourceInsufficient {
				continue
			}
			return model.PlacementDecision{}, err
		}
		return model.PlacementDecision{
			TargetVMID:      item.node.VMID,
			TargetProfileID: item.node.VMID,
			Allocation:      allocation,
			Source:          "local",
			Score:           -item.node.CostWeight,
			Reason:          fmt.Sprintf("min_cost selected cost_weight=%.4g; remaining_capacity_score=%.4g; tie_break=vm_id", item.node.CostWeight, item.remaining),
			SelectedAt:      time.Now().UTC(),
		}, nil
	}
	return model.PlacementDecision{}, apperrors.New(model.ErrResourceInsufficient, "eligible VM capacity was reserved by another deployment", http.StatusConflict, true)
}

// ReleaseDeployment is used by the orchestrator because PlacementDecision is
// deliberately a public placement result and does not carry deployment IDs.
func (p *LocalPlacementProvider) ReleaseDeployment(ctx context.Context, decision model.PlacementDecision, deploymentID string) error {
	id := decision.TargetVMID
	if id == "" {
		id = decision.TargetProfileID
	}
	if id == "" || deploymentID == "" {
		return nil
	}
	return p.reservations.ReleaseResources(ctx, id, deploymentID)
}

type candidate struct {
	node      model.NodeProfile
	remaining float64
}

func nodeFromTarget(target model.TargetProfile) model.NodeProfile {
	vmType := target.VMType
	if vmType == "" {
		switch target.CSP {
		case "mock":
			vmType = "Mock"
		case "local":
			vmType = "Local"
		default:
			vmType = "Cloud"
		}
	}
	status := target.Status
	if status == "" {
		status = model.NodeStatusReady
	}
	capacity := target.Capacity
	if capacity.GPUCount == 0 && target.GPU != nil {
		capacity.GPUCount = target.GPU.Count
		if capacity.GPUType == "" {
			capacity.GPUType = target.GPU.Vendor
		}
	}
	return model.NodeProfile{
		VMID: target.TargetProfileID, VMType: vmType, Status: status, Target: target,
		Capacity: capacity, Available: availableCapacity(capacity, target.Allocated),
		Allocated: target.Allocated, SupportedRuntimes: target.SupportedRuntimes,
		CostWeight: target.CostWeight, Labels: target.Labels,
	}
}

func ready(node model.NodeProfile) bool {
	return node.Status == model.NodeStatusReady || node.Target.Status == ""
}

func runtimeMatches(node model.NodeProfile, runtime string) bool {
	if runtime == "" {
		return true
	}
	if node.Target.CSP == "mock" || node.Target.Runtime.RuntimeType == "mock" {
		return true
	}
	for _, supported := range node.SupportedRuntimes {
		if supported == runtime {
			return true
		}
	}
	return node.Target.Runtime.RuntimeType == runtime || (node.Target.Runtime.RuntimeType == "local" && runtime == "cpu")
}

func acceleratorMatches(node model.NodeProfile, accelerator string) bool {
	if accelerator == "" || accelerator == "none" {
		return true
	}
	if node.Target.CSP == "mock" || node.Target.Runtime.RuntimeType == "mock" {
		return true
	}
	if accelerator != "nvidia" {
		return false
	}
	if node.Capacity.GPUType != "" {
		return strings.EqualFold(node.Capacity.GPUType, "nvidia")
	}
	return strings.EqualFold(node.Target.Runtime.Accelerator, "nvidia")
}

func labelsMatch(have, want map[string]string) bool {
	for key, value := range want {
		if have[key] != value {
			return false
		}
	}
	return true
}

func capacityFits(node model.NodeProfile, request model.ResourceAllocation) bool {
	available := node.Available
	capacity := node.Capacity
	gpuFits := available.GPUCount >= request.GPUCount || node.Target.CSP == "mock" || node.Target.Runtime.RuntimeType == "mock"
	return (capacity.CPUCores == 0 || available.CPUCores >= request.CPUCores) &&
		(capacity.MemoryBytes == 0 || available.MemoryBytes >= request.MemoryBytes) &&
		gpuFits &&
		(capacity.StorageBytes == 0 || available.StorageBytes >= request.StorageBytes)
}

func availableCapacity(capacity model.ResourceCapacity, allocated model.ResourceAllocation) model.ResourceCapacity {
	return model.ResourceCapacity{
		CPUCores:     capacity.CPUCores - allocated.CPUCores,
		MemoryBytes:  capacity.MemoryBytes - allocated.MemoryBytes,
		GPUCount:     capacity.GPUCount - allocated.GPUCount,
		GPUType:      capacity.GPUType,
		StorageBytes: capacity.StorageBytes - allocated.StorageBytes,
	}
}

func remainingScore(node model.NodeProfile, allocation model.ResourceAllocation) float64 {
	available := node.Available
	return positive(available.CPUCores-allocation.CPUCores) +
		positive(float64(available.MemoryBytes-allocation.MemoryBytes)/float64(1<<30)) +
		positive(float64(available.GPUCount-allocation.GPUCount)) +
		positive(float64(available.StorageBytes-allocation.StorageBytes)/float64(1<<30))
}

func positive(value float64) float64 {
	if value < 0 {
		return 0
	}
	return value
}

func allocationFor(resources model.Resources) (model.ResourceAllocation, error) {
	cpu, err := parseCPU(resources.CPU)
	if err != nil {
		return model.ResourceAllocation{}, invalidQuantity("resources.cpu")
	}
	memory, err := parseBytes(resources.Memory)
	if err != nil {
		return model.ResourceAllocation{}, invalidQuantity("resources.memory")
	}
	gpu := 0
	if strings.TrimSpace(resources.GPU) != "" {
		gpu, err = strconv.Atoi(strings.TrimSpace(resources.GPU))
		if err != nil || gpu < 0 {
			return model.ResourceAllocation{}, invalidQuantity("resources.gpu")
		}
	}
	storage, err := parseBytes(resources.Storage)
	if err != nil {
		return model.ResourceAllocation{}, invalidQuantity("resources.storage")
	}
	return model.ResourceAllocation{CPUCores: cpu, MemoryBytes: memory, GPUCount: gpu, StorageBytes: storage}, nil
}

func parseCPU(value string) (float64, error) {
	value = strings.TrimSpace(strings.ToLower(value))
	if value == "" {
		return 0, nil
	}
	if strings.HasSuffix(value, "m") {
		milli, err := strconv.ParseFloat(strings.TrimSuffix(value, "m"), 64)
		if err == nil && (milli < 0 || math.IsNaN(milli) || math.IsInf(milli, 0)) {
			return 0, fmt.Errorf("cpu quantity must be non-negative and finite")
		}
		return milli / 1000, err
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err == nil && (parsed < 0 || math.IsNaN(parsed) || math.IsInf(parsed, 0)) {
		return 0, fmt.Errorf("cpu quantity must be non-negative and finite")
	}
	return parsed, err
}

func parseBytes(value string) (int64, error) {
	value = strings.TrimSpace(strings.ToLower(value))
	if value == "" {
		return 0, nil
	}
	units := []struct {
		suffix     string
		multiplier int64
	}{
		{"gib", 1 << 30}, {"gb", 1_000_000_000}, {"gi", 1 << 30}, {"g", 1 << 30},
		{"mib", 1 << 20}, {"mb", 1_000_000}, {"mi", 1 << 20}, {"m", 1 << 20},
		{"kib", 1 << 10}, {"kb", 1_000}, {"ki", 1 << 10}, {"k", 1 << 10},
	}
	for _, unit := range units {
		if strings.HasSuffix(value, unit.suffix) {
			number, err := strconv.ParseFloat(strings.TrimSpace(strings.TrimSuffix(value, unit.suffix)), 64)
			if err == nil && (number < 0 || math.IsNaN(number) || math.IsInf(number, 0)) {
				return 0, fmt.Errorf("byte quantity must be non-negative and finite")
			}
			return int64(number * float64(unit.multiplier)), err
		}
	}
	number, err := strconv.ParseFloat(value, 64)
	if err == nil && (number < 0 || math.IsNaN(number) || math.IsInf(number, 0)) {
		return 0, fmt.Errorf("byte quantity must be non-negative and finite")
	}
	return int64(number), err
}

func invalidQuantity(field string) error {
	return apperrors.New(model.ErrDeploymentFailed, field+" must be a valid resource quantity", http.StatusBadRequest, false)
}

func matchesFilter(node model.NodeProfile, filter model.ResourceFilter) bool {
	if filter.TargetVMID != "" && node.VMID != filter.TargetVMID {
		return false
	}
	return runtimeMatches(node, filter.Runtime) && acceleratorMatches(node, filter.Accelerator) && labelsMatch(node.Labels, filter.Labels)
}
