package deployment

import (
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestNormalizeManifestFromLegacyRequest(t *testing.T) {
	manifest, err := normalizeManifest(model.DeploymentCreateRequest{
		AppVersionID:    "appver-001",
		TargetProfileID: "target-cpu-001",
		RequestedBy:     "appdeployer-web",
		Parameters:      map[string]any{"port": 18080},
	}, "dep-001")
	if err != nil {
		t.Fatal(err)
	}
	if manifest.SchemaVersion != model.DeploymentManifestSchemaVersion || manifest.Kind != model.DeploymentManifestKind {
		t.Fatalf("unexpected manifest identity: %+v", manifest)
	}
	if manifest.Metadata == nil || manifest.Metadata.Name != "dep-001" {
		t.Fatalf("unexpected manifest metadata: %+v", manifest.Metadata)
	}
	if manifest.Spec.AppVersionID != "appver-001" || manifest.Spec.TargetProfileID != "target-cpu-001" {
		t.Fatalf("unexpected manifest references: %+v", manifest.Spec)
	}
}

func TestNormalizeManifestAcceptsManifestOnly(t *testing.T) {
	manifest, err := normalizeManifest(model.DeploymentCreateRequest{
		Manifest: &model.DeploymentManifest{
			SchemaVersion: model.DeploymentManifestSchemaVersion,
			Kind:          model.DeploymentManifestKind,
			Spec: model.DeploymentManifestSpec{
				AppVersionID: "appver-001",
				RequestedBy:  "ai-ops-geon-planner",
			},
		},
	}, "dep-002")
	if err != nil {
		t.Fatal(err)
	}
	if manifest.Metadata == nil || manifest.Metadata.Name != "dep-002" {
		t.Fatalf("manifest metadata = %+v", manifest.Metadata)
	}
	if manifest.Spec.RequestedBy != "ai-ops-geon-planner" {
		t.Fatalf("requested_by = %q", manifest.Spec.RequestedBy)
	}
}

func TestNormalizeManifestAllowsPlannerRequestWithoutTarget(t *testing.T) {
	manifest, err := normalizeManifest(model.DeploymentCreateRequest{
		AppVersionID: "appver-001",
	}, "dep-target-only")
	if err != nil {
		t.Fatal(err)
	}
	if manifest.Spec.AppVersionID != "appver-001" || manifest.Spec.TargetProfileID != "" {
		t.Fatalf("unexpected planner references: %+v", manifest.Spec)
	}
}

func TestNormalizeManifestRejectsConflictingLegacyFields(t *testing.T) {
	_, err := normalizeManifest(model.DeploymentCreateRequest{
		AppVersionID: "appver-other",
		Manifest: &model.DeploymentManifest{
			SchemaVersion: model.DeploymentManifestSchemaVersion,
			Kind:          model.DeploymentManifestKind,
			Spec: model.DeploymentManifestSpec{
				AppVersionID:    "appver-001",
				TargetProfileID: "target-cpu-001",
			},
		},
	}, "dep-003")
	if err == nil {
		t.Fatal("expected conflicting app_version_id to fail")
	}
}

func TestCompleteManifestRequirementsFillsAppDefaultsAndKeepsPlannerOverrides(t *testing.T) {
	manifest := model.DeploymentManifest{}
	completeManifestRequirements(&manifest, model.AppSpec{
		Runtime: model.AppRuntime{Accelerator: "none"},
		Resources: model.Resources{
			CPU:     "2",
			Memory:  "4Gi",
			GPU:     "0",
			Storage: "20Gi",
		},
	})
	if manifest.Spec.Accelerator != "none" || manifest.Spec.Resources.Memory != "4Gi" || manifest.Spec.Resources.Storage != "20Gi" {
		t.Fatalf("app defaults were not copied: %+v", manifest.Spec)
	}

	manifest = model.DeploymentManifest{Spec: model.DeploymentManifestSpec{
		Accelerator: "nvidia",
		Resources:   model.Resources{Memory: "16Gi", GPU: "2"},
	}}
	completeManifestRequirements(&manifest, model.AppSpec{
		Runtime:   model.AppRuntime{Accelerator: "none"},
		Resources: model.Resources{CPU: "2", Memory: "4Gi", GPU: "1", Storage: "20Gi"},
	})
	if manifest.Spec.Accelerator != "nvidia" || manifest.Spec.Resources.CPU != "2" || manifest.Spec.Resources.Memory != "16Gi" || manifest.Spec.Resources.GPU != "2" || manifest.Spec.Resources.Storage != "20Gi" {
		t.Fatalf("planner requirements were not merged correctly: %+v", manifest.Spec)
	}
}

func TestCompleteManifestRequirementsUsesDeploymentRequirements(t *testing.T) {
	manifest := model.DeploymentManifest{Spec: model.DeploymentManifestSpec{
		Requirements: &model.DeploymentRequirements{
			Resources:   model.Resources{CPU: "4", Memory: "8Gi", GPU: "1"},
			Accelerator: "nvidia",
		},
	}}
	completeManifestRequirements(&manifest, model.AppSpec{
		Runtime:   model.AppRuntime{Type: "gpu", Accelerator: "none"},
		Resources: model.Resources{CPU: "1", Memory: "1Gi", GPU: "0", Storage: "1Gi"},
	})
	if manifest.Spec.Accelerator != "nvidia" || manifest.Spec.Resources.CPU != "4" || manifest.Spec.Resources.Memory != "8Gi" || manifest.Spec.Resources.GPU != "1" || manifest.Spec.Resources.Storage != "1Gi" {
		t.Fatalf("deployment requirements were not applied: %+v", manifest.Spec)
	}
	if manifest.Spec.Requirements.Resources != manifest.Spec.Resources {
		t.Fatalf("effective requirements do not match manifest resources: %+v", manifest.Spec.Requirements.Resources)
	}
}

func TestNormalizeManifestRejectsNvidiaWithoutGPU(t *testing.T) {
	_, err := normalizeManifest(model.DeploymentCreateRequest{
		Manifest: &model.DeploymentManifest{
			SchemaVersion: model.DeploymentManifestSchemaVersion,
			Kind:          model.DeploymentManifestKind,
			Spec: model.DeploymentManifestSpec{
				AppVersionID:    "appver-1",
				TargetProfileID: "target-1",
				Accelerator:     "nvidia",
				Resources:       model.Resources{GPU: "0"},
			},
		},
	}, "dep-1")
	if err == nil {
		t.Fatal("expected nvidia manifest with zero GPUs to be rejected")
	}
}
