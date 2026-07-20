package profile

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/khu/ai-app-deployer/internal/credentialref"
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

func (s *Service) GetTarget(ctx context.Context, id string) (model.TargetProfile, error) {
	return s.repo.GetTargetProfile(ctx, id)
}

func (s *Service) DeleteTarget(ctx context.Context, id string) (model.ProfileDeleteResponse, error) {
	profile, inventoryDeleted, err := s.repo.DeleteTargetProfile(ctx, id)
	if err != nil {
		return model.ProfileDeleteResponse{}, err
	}
	return model.ProfileDeleteResponse{
		ProfileType:      model.ProfileTypeTarget,
		ProfileID:        profile.TargetProfileID,
		Name:             profile.Name,
		Deleted:          true,
		InventoryDeleted: inventoryDeleted,
		DeletedAt:        time.Now().UTC(),
	}, nil
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
	if err := validateCredentialRef(profile.VM.CredentialRef); err != nil {
		return err
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

func validateCredentialRef(credentialRef string) error {
	if !credentialref.Valid(credentialRef) {
		return targetInvalid("vm.credential_ref must be a credential reference such as cred://runtime/credential-id")
	}
	return nil
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
