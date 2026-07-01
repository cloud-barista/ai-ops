package resource

import (
	"context"
	"time"

	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/store"
)

type Service struct {
	profiles    store.ProfileRepository
	deployments store.DeploymentRepository
	adapter     runtime.Adapter
}

func NewService(profiles store.ProfileRepository, deployments store.DeploymentRepository, adapter runtime.Adapter) *Service {
	return &Service{profiles: profiles, deployments: deployments, adapter: adapter}
}

func (s *Service) Check(ctx context.Context, targetProfileID string) (model.ResourceCheckResponse, error) {
	target, err := s.profiles.GetTargetProfile(ctx, targetProfileID)
	if err != nil {
		return model.ResourceCheckResponse{}, err
	}
	checks := map[string]string{
		"target":  "ok",
		"runtime": "ok",
	}
	status := "available"
	if err := s.adapter.ValidateTarget(ctx, target); err != nil {
		status = "unavailable"
		checks["target"] = err.Error()
	}
	if status == "available" {
		if err := s.adapter.HealthCheck(ctx, runtimeProfileFromTarget(target), target); err != nil {
			status = "unavailable"
			checks["runtime"] = err.Error()
		}
	}
	inventory := model.ResourceInventory{
		TargetProfileID:  target.TargetProfileID,
		CPUAvailable:     status == "available",
		MemoryAvailable:  status == "available",
		GPUAvailable:     status == "available" && target.GPU != nil && target.GPU.Count > 0,
		StorageAvailable: status == "available",
		RuntimeHealth:    checks["runtime"],
		LastCheckedAt:    time.Now().UTC(),
	}
	if target.Runtime.RuntimeType == "mock" {
		inventory.GPUAvailable = false
	}
	if err := s.deployments.SaveInventory(ctx, inventory); err != nil {
		return model.ResourceCheckResponse{}, err
	}
	return model.ResourceCheckResponse{
		TargetProfileID: targetProfileID,
		Status:          status,
		Checks:          checks,
		Details: map[string]any{
			"runtime_type": target.Runtime.RuntimeType,
			"accelerator":  target.Runtime.Accelerator,
			"gpu_count":    gpuCount(target),
		},
		CheckedAt: inventory.LastCheckedAt,
	}, nil
}

func (s *Service) ListInventory(ctx context.Context) ([]model.ResourceInventory, error) {
	return s.deployments.ListInventory(ctx)
}

func gpuCount(target model.TargetProfile) int {
	if target.GPU == nil {
		return 0
	}
	return target.GPU.Count
}

func runtimeProfileFromTarget(target model.TargetProfile) model.RuntimeProfile {
	adapterType := target.Runtime.RuntimeType
	switch target.Runtime.RuntimeType {
	case "cpu":
		adapterType = "cpu_vm"
	case "gpu":
		adapterType = "gpu_vm"
	case "aiinfra":
		adapterType = "etri_aiinfra"
	case "mock":
		adapterType = "mock"
	}
	return model.RuntimeProfile{
		RuntimeProfileID: target.TargetProfileID + "-runtime-check",
		RuntimeType:      target.Runtime.RuntimeType,
		Accelerator:      target.Runtime.Accelerator,
		AdapterType:      adapterType,
		OperatingMode:    target.Runtime.OperatingMode,
	}
}
