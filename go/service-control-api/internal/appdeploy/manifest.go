package appdeploy

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

var (
	integerQuantity = regexp.MustCompile(`^[0-9]+$`)
	memoryQuantity  = regexp.MustCompile(`^[1-9][0-9]*(Mi|Gi|Ti)$`)
)

type ManifestConstraints struct {
	AppVersionID    string
	TargetProfileID string
	RequestedBy     string
}

func ValidateManifest(manifest DeploymentManifest, constraints ManifestConstraints) error {
	if manifest.SchemaVersion != ManifestSchemaVersion {
		return fmt.Errorf("schema_version must be %s", ManifestSchemaVersion)
	}
	if manifest.Kind != ManifestKind {
		return fmt.Errorf("kind must be %s", ManifestKind)
	}
	if strings.TrimSpace(manifest.Spec.AppVersionID) == "" {
		return fmt.Errorf("spec.app_version_id is required")
	}
	if constraints.AppVersionID != "" && manifest.Spec.AppVersionID != constraints.AppVersionID {
		return fmt.Errorf("spec.app_version_id must match the requested App Version")
	}
	if manifest.Spec.TargetProfileID != constraints.TargetProfileID {
		return fmt.Errorf("spec.target_profile_id must match the requested target hint")
	}
	if constraints.RequestedBy != "" && manifest.Spec.RequestedBy != constraints.RequestedBy {
		return fmt.Errorf("spec.requested_by must match the trusted requester")
	}
	if manifest.Spec.Accelerator != "none" && manifest.Spec.Accelerator != "nvidia" {
		return fmt.Errorf("spec.accelerator must be none or nvidia")
	}
	if !regexp.MustCompile(`^[1-9][0-9]*$`).MatchString(manifest.Spec.Resources.CPU) {
		return fmt.Errorf("spec.resources.cpu must be a positive integer string")
	}
	if !memoryQuantity.MatchString(manifest.Spec.Resources.Memory) {
		return fmt.Errorf("spec.resources.memory must use Mi, Gi, or Ti")
	}
	if !integerQuantity.MatchString(manifest.Spec.Resources.GPU) {
		return fmt.Errorf("spec.resources.gpu must be a non-negative integer string")
	}
	if !memoryQuantity.MatchString(manifest.Spec.Resources.Storage) {
		return fmt.Errorf("spec.resources.storage must use Mi, Gi, or Ti")
	}
	gpu, err := strconv.Atoi(manifest.Spec.Resources.GPU)
	if err != nil {
		return fmt.Errorf("spec.resources.gpu must be a non-negative integer string")
	}
	if manifest.Spec.Accelerator == "nvidia" && gpu < 1 {
		return fmt.Errorf("nvidia accelerator requires at least one GPU")
	}
	if manifest.Spec.Accelerator == "none" && gpu != 0 {
		return fmt.Errorf("none accelerator requires zero GPUs")
	}
	if secretKey, ok := findSecretLikeKey(manifest.Spec.Parameters); ok {
		return fmt.Errorf("spec.parameters contains secret-like key %q", secretKey)
	}
	return nil
}

func findSecretLikeKey(value any) (string, bool) {
	switch typed := value.(type) {
	case map[string]any:
		for key, item := range typed {
			normalized := strings.ToLower(strings.ReplaceAll(key, "-", "_"))
			for _, marker := range []string{"password", "secret", "token", "credential", "private_key", "api_key"} {
				if strings.Contains(normalized, marker) {
					return key, true
				}
			}
			if key, ok := findSecretLikeKey(item); ok {
				return key, true
			}
		}
	case []any:
		for _, item := range typed {
			if key, ok := findSecretLikeKey(item); ok {
				return key, true
			}
		}
	}
	return "", false
}
