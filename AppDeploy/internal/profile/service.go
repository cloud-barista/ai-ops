package profile

import (
	"context"
	"net/http"
	"strings"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

type Service struct {
	repo store.ProfileRepository
}

func NewService(repo store.ProfileRepository) *Service {
	return &Service{repo: repo}
}

func (s *Service) CreateRuntime(ctx context.Context, profile model.RuntimeProfile) (model.RuntimeProfile, error) {
	if err := ValidateRuntimeProfile(profile); err != nil {
		return model.RuntimeProfile{}, err
	}
	if err := s.repo.CreateRuntimeProfile(ctx, profile); err != nil {
		return model.RuntimeProfile{}, err
	}
	return profile, nil
}

func (s *Service) ListRuntimes(ctx context.Context) ([]model.RuntimeProfile, error) {
	return s.repo.ListRuntimeProfiles(ctx)
}

func (s *Service) CreateTarget(ctx context.Context, profile model.TargetProfile) (model.TargetProfile, error) {
	if err := ValidateTargetProfile(profile); err != nil {
		return model.TargetProfile{}, err
	}
	if err := s.repo.CreateTargetProfile(ctx, profile); err != nil {
		return model.TargetProfile{}, err
	}
	return profile, nil
}

func (s *Service) ListTargets(ctx context.Context) ([]model.TargetProfile, error) {
	return s.repo.ListTargetProfiles(ctx)
}

func (s *Service) GetRuntime(ctx context.Context, id string) (model.RuntimeProfile, error) {
	return s.repo.GetRuntimeProfile(ctx, id)
}

func (s *Service) GetTarget(ctx context.Context, id string) (model.TargetProfile, error) {
	return s.repo.GetTargetProfile(ctx, id)
}

func ValidateRuntimeProfile(profile model.RuntimeProfile) error {
	if strings.TrimSpace(profile.RuntimeProfileID) == "" {
		return runtimeInvalid("runtime_profile_id is required")
	}
	if !allowed(profile.RuntimeType, "mock", "cpu", "gpu", "aiinfra") {
		return runtimeInvalid("runtime_type must be one of mock, cpu, gpu, aiinfra")
	}
	if profile.Accelerator != "" && !allowed(profile.Accelerator, "none", "nvidia") {
		return runtimeInvalid("accelerator must be none or nvidia")
	}
	if !allowed(profile.AdapterType, "mock", "cpu_vm", "gpu_vm", "etri_aiinfra") {
		return runtimeInvalid("adapter_type must be one of mock, cpu_vm, gpu_vm, etri_aiinfra")
	}
	if !allowed(profile.OperatingMode, "local_mock", "dry_run", "vm_process", "remote_api") {
		return runtimeInvalid("operating_mode must be one of local_mock, dry_run, vm_process, remote_api")
	}
	return nil
}

func ValidateTargetProfile(profile model.TargetProfile) error {
	if strings.TrimSpace(profile.TargetProfileID) == "" {
		return targetInvalid("target_profile_id is required")
	}
	if !allowed(profile.CSP, "aws", "azure", "gcp", "etri", "local", "mock") {
		return targetInvalid("csp must be one of aws, azure, gcp, etri, local, mock")
	}
	if profile.CSP != "mock" && strings.TrimSpace(profile.VM.Host) == "" {
		return targetInvalid("vm.host is required for non-mock targets")
	}
	if !allowed(profile.Runtime.RuntimeType, "mock", "cpu", "gpu", "aiinfra") {
		return targetInvalid("runtime.runtime_type must be one of mock, cpu, gpu, aiinfra")
	}
	if profile.Runtime.Accelerator != "" && !allowed(profile.Runtime.Accelerator, "none", "nvidia") {
		return targetInvalid("runtime.accelerator must be none or nvidia")
	}
	if profile.Runtime.OperatingMode != "" && !allowed(profile.Runtime.OperatingMode, "local_mock", "dry_run", "vm_process", "remote_api") {
		return targetInvalid("runtime.operating_mode must be local_mock, dry_run, vm_process, or remote_api")
	}
	if profile.GPU != nil && profile.GPU.Count < 0 {
		return targetInvalid("gpu.count cannot be negative")
	}
	return nil
}

func runtimeInvalid(message string) error {
	return apperrors.New(model.ErrRuntimeProfileInvalid, message, http.StatusBadRequest, false)
}

func targetInvalid(message string) error {
	return apperrors.New(model.ErrTargetProfileInvalid, message, http.StatusBadRequest, false)
}

func allowed(value string, allowedValues ...string) bool {
	for _, allowedValue := range allowedValues {
		if value == allowedValue {
			return true
		}
	}
	return false
}
