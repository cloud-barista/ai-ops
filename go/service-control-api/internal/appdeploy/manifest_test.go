package appdeploy

import (
	"strings"
	"testing"
)

func TestValidateManifestAcceptsAppDeployContract(t *testing.T) {
	manifest := validManifest()
	err := ValidateManifest(manifest, ManifestConstraints{
		AppVersionID:    "appver-test",
		TargetProfileID: "target-gpu-001",
		RequestedBy:     "ai-ops-geon-planner",
	})
	if err != nil {
		t.Fatalf("expected valid manifest, got %v", err)
	}
}

func TestValidateManifestRejectsContractViolations(t *testing.T) {
	tests := []struct {
		name     string
		mutate   func(*DeploymentManifest)
		contains string
	}{
		{
			name: "schema version",
			mutate: func(manifest *DeploymentManifest) {
				manifest.SchemaVersion = "v1"
			},
			contains: "schema_version",
		},
		{
			name: "kind",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Kind = "Pod"
			},
			contains: "kind",
		},
		{
			name: "app version substitution",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.AppVersionID = "appver-other"
			},
			contains: "app_version_id",
		},
		{
			name: "target hint substitution",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.TargetProfileID = "target-other"
			},
			contains: "target_profile_id",
		},
		{
			name: "missing cpu",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Resources.CPU = ""
			},
			contains: "resources.cpu",
		},
		{
			name: "invalid memory quantity",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Resources.Memory = "sixteen"
			},
			contains: "resources.memory",
		},
		{
			name: "nvidia without gpu",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Resources.GPU = "0"
			},
			contains: "nvidia accelerator",
		},
		{
			name: "none with gpu",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Accelerator = "none"
			},
			contains: "none accelerator",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			manifest := validManifest()
			test.mutate(&manifest)
			err := ValidateManifest(manifest, ManifestConstraints{
				AppVersionID:    "appver-test",
				TargetProfileID: "target-gpu-001",
				RequestedBy:     "ai-ops-geon-planner",
			})
			if err == nil || !strings.Contains(err.Error(), test.contains) {
				t.Fatalf("expected error containing %q, got %v", test.contains, err)
			}
		})
	}
}

func TestValidateManifestRejectsSecretLikeParameters(t *testing.T) {
	manifest := validManifest()
	manifest.Spec.Parameters = map[string]any{
		"runtime": map[string]any{
			"api_token": "must-not-pass",
		},
	}
	err := ValidateManifest(manifest, ManifestConstraints{
		AppVersionID:    "appver-test",
		TargetProfileID: "target-gpu-001",
		RequestedBy:     "ai-ops-geon-planner",
	})
	if err == nil || !strings.Contains(err.Error(), "secret-like") {
		t.Fatalf("expected secret-like parameter rejection, got %v", err)
	}
}

func validManifest() DeploymentManifest {
	return DeploymentManifest{
		SchemaVersion: ManifestSchemaVersion,
		Kind:          ManifestKind,
		Metadata: DeploymentMetadata{
			Name: "llm-inference-deployment",
		},
		Spec: DeploymentSpec{
			AppVersionID:    "appver-test",
			TargetProfileID: "target-gpu-001",
			Accelerator:     "nvidia",
			Resources: ResourceRequirements{
				CPU:     "4",
				Memory:  "16Gi",
				GPU:     "1",
				Storage: "20Gi",
			},
			RequestedBy: "ai-ops-geon-planner",
			Parameters:  map[string]any{"port": float64(18080)},
		},
	}
}
