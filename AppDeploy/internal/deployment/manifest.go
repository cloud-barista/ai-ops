package deployment

import (
	"net/http"
	"reflect"
	"strconv"
	"strings"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

// normalizeManifest turns the request into the stable DeploymentManifest
// consumed by the orchestrator. A caller may provide a target hint for
// compatibility, but the field is optional because App Deployer target
// selection happens after App requirements are loaded and checked.
func normalizeManifest(req model.DeploymentCreateRequest, deploymentID string) (model.DeploymentManifest, error) {
	providedManifest := req.Manifest != nil
	manifest := model.DeploymentManifest{
		SchemaVersion: model.DeploymentManifestSchemaVersion,
		Kind:          model.DeploymentManifestKind,
		Metadata:      &model.DeploymentManifestMetadata{Name: deploymentID},
	}
	if req.Manifest != nil {
		manifest = *req.Manifest
		if manifest.SchemaVersion != model.DeploymentManifestSchemaVersion {
			return model.DeploymentManifest{}, manifestInvalid("manifest.schema_version must be deployment.khu.ai/v1alpha1")
		}
		if manifest.Kind != model.DeploymentManifestKind {
			return model.DeploymentManifest{}, manifestInvalid("manifest.kind must be DeploymentManifest")
		}
		if manifest.Metadata == nil {
			manifest.Metadata = &model.DeploymentManifestMetadata{Name: deploymentID}
		}
		if manifest.Metadata.Name == "" {
			manifest.Metadata.Name = deploymentID
		}
		if manifest.Spec.Accelerator != "" && manifest.Spec.Accelerator != "none" && manifest.Spec.Accelerator != "nvidia" {
			return model.DeploymentManifest{}, manifestInvalid("manifest.spec.accelerator must be none or nvidia")
		}
		if rawGPU := strings.TrimSpace(manifest.Spec.Resources.GPU); rawGPU != "" {
			gpu, err := strconv.Atoi(rawGPU)
			if err != nil || gpu < 0 {
				return model.DeploymentManifest{}, manifestInvalid("manifest.spec.resources.gpu must be a non-negative integer")
			}
			if manifest.Spec.Accelerator == "nvidia" && gpu < 1 {
				return model.DeploymentManifest{}, manifestInvalid("manifest.spec.accelerator=nvidia requires resources.gpu >= 1")
			}
		}
	}

	if err := mergeManifestReference(&manifest.Spec.AppVersionID, req.AppVersionID, "app_version_id"); err != nil {
		return model.DeploymentManifest{}, err
	}
	if err := mergeManifestReference(&manifest.Spec.TargetProfileID, req.TargetProfileID, "target_profile_id"); err != nil {
		return model.DeploymentManifest{}, err
	}
	if req.RequestedBy != "" {
		if manifest.Spec.RequestedBy != "" && manifest.Spec.RequestedBy != req.RequestedBy {
			return model.DeploymentManifest{}, manifestInvalid("requested_by does not match manifest.spec.requested_by")
		}
		manifest.Spec.RequestedBy = req.RequestedBy
	}
	if req.Parameters != nil {
		if manifest.Spec.Parameters != nil && !reflect.DeepEqual(manifest.Spec.Parameters, req.Parameters) {
			return model.DeploymentManifest{}, manifestInvalid("parameters do not match manifest.spec.parameters")
		}
		manifest.Spec.Parameters = req.Parameters
	}

	if manifest.Spec.AppVersionID == "" {
		if !providedManifest {
			return model.DeploymentManifest{}, manifestInvalid("app_version_id is required")
		}
		return model.DeploymentManifest{}, manifestInvalid("manifest.spec requires app_version_id")
	}
	return manifest, nil
}

// CompleteManifestRequirements fills resource fields omitted by a legacy web
// request or a planner with the registered AppSpec requirements. Explicit
// Manifest values remain authoritative so a planner can request a larger
// resource envelope than the App's default.
func completeManifestRequirements(manifest *model.DeploymentManifest, app model.AppSpec) {
	if manifest.Spec.Accelerator == "" {
		manifest.Spec.Accelerator = app.Runtime.Accelerator
	}
	manifest.Spec.Resources = mergeResources(manifest.Spec.Resources, app.Resources)
}

func mergeResources(requested, fallback model.Resources) model.Resources {
	if requested.CPU == "" {
		requested.CPU = fallback.CPU
	}
	if requested.Memory == "" {
		requested.Memory = fallback.Memory
	}
	if requested.GPU == "" {
		requested.GPU = fallback.GPU
	}
	if requested.Storage == "" {
		requested.Storage = fallback.Storage
	}
	return requested
}

func mergeManifestReference(manifestValue *string, requestValue, field string) error {
	if requestValue != "" {
		if *manifestValue != "" && *manifestValue != requestValue {
			return manifestInvalid(field + " does not match manifest.spec." + field)
		}
		*manifestValue = requestValue
	}
	return nil
}

func manifestInvalid(message string) error {
	return apperrors.New(model.ErrDeploymentFailed, message, http.StatusBadRequest, false)
}
