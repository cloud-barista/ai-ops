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

func TestValidateManifestAcceptsDeploymentRequirementsContract(t *testing.T) {
	requirements := DeploymentRequirements{
		Runtime:    "gpu",
		Resources:  ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "1", Storage: "20Gi"},
		CostPolicy: "min_cost",
	}
	manifest := validManifest()
	manifest.Spec.Resources = requirements.Resources
	manifest.Spec.Requirements = &requirements

	if err := ValidateManifest(manifest, ManifestConstraints{
		AppVersionID:    "appver-test",
		TargetProfileID: "target-gpu-001",
		RequestedBy:     "ai-ops-geon-planner",
		RuntimeType:     "gpu",
	}); err != nil {
		t.Fatal(err)
	}
}

func TestValidateManifestRejectsDeploymentRequirementViolations(t *testing.T) {
	tests := []struct {
		name     string
		mutate   func(*DeploymentManifest)
		contains string
	}{
		{
			name: "gpu runtime without gpu",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Requirements = &DeploymentRequirements{
					Runtime: "gpu", Resources: ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "0", Storage: "20Gi"}, CostPolicy: "min_cost",
				}
				manifest.Spec.Resources = manifest.Spec.Requirements.Resources
			},
			contains: "runtime gpu requires at least one GPU",
		},
		{
			name: "cpu runtime with gpu",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Requirements = &DeploymentRequirements{
					Runtime: "cpu", Resources: ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "1", Storage: "20Gi"}, CostPolicy: "min_cost",
				}
				manifest.Spec.Resources = manifest.Spec.Requirements.Resources
			},
			contains: "runtime cpu requires zero GPUs",
		},
		{
			name: "unknown cost policy",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Requirements = &DeploymentRequirements{
					Runtime: "gpu", Resources: manifest.Spec.Resources, CostPolicy: "unknown",
				}
			},
			contains: "cost_policy",
		},
		{
			name: "conflicting resources",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Requirements = &DeploymentRequirements{
					Runtime: "gpu", Resources: ResourceRequirements{CPU: "8", Memory: "8Gi", GPU: "1", Storage: "20Gi"}, CostPolicy: "min_cost",
				}
			},
			contains: "requirements.resources",
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

func TestValidateManifestRejectsRequirementsThatDoNotMatchTrustedConstraints(t *testing.T) {
	trusted := &DeploymentRequirements{
		Runtime:     "gpu",
		Resources:   ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "1", Storage: "20Gi"},
		Accelerator: "nvidia",
		CostPolicy:  "min_cost",
	}
	tests := []struct {
		name     string
		mutate   func(*DeploymentManifest)
		contains string
	}{
		{
			name: "absent requirements",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Resources = trusted.Resources
				manifest.Spec.Requirements = nil
			},
			contains: "spec.requirements is required",
		},
		{
			name: "resource substitution",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Requirements = &DeploymentRequirements{
					Runtime: "gpu", Resources: ResourceRequirements{CPU: "8", Memory: "8Gi", GPU: "1", Storage: "20Gi"}, Accelerator: "nvidia", CostPolicy: "min_cost",
				}
				manifest.Spec.Resources = manifest.Spec.Requirements.Resources
			},
			contains: "trusted resources",
		},
		{
			name: "cost policy substitution",
			mutate: func(manifest *DeploymentManifest) {
				manifest.Spec.Resources = trusted.Resources
				manifest.Spec.Requirements = &DeploymentRequirements{
					Runtime: "gpu", Resources: trusted.Resources, Accelerator: "nvidia", CostPolicy: "",
				}
			},
			contains: "trusted cost_policy",
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
				Requirements:    trusted,
			})
			if err == nil || !strings.Contains(err.Error(), test.contains) {
				t.Fatalf("expected error containing %q, got %v", test.contains, err)
			}
		})
	}
}

func TestValidateManifestRejectsSecretLikeRequirementKeys(t *testing.T) {
	tests := []struct {
		name         string
		requirements DeploymentRequirements
		contains     string
	}{
		{
			name: "nested slo api token",
			requirements: DeploymentRequirements{
				Runtime: "gpu", Resources: ResourceRequirements{CPU: "4", Memory: "16Gi", GPU: "1", Storage: "20Gi"},
				SLO: map[string]any{"routing": map[string]any{"api_token": "must-not-pass"}},
			},
			contains: "requirements.slo",
		},
		{
			name: "label private key",
			requirements: DeploymentRequirements{
				Runtime: "gpu", Resources: ResourceRequirements{CPU: "4", Memory: "16Gi", GPU: "1", Storage: "20Gi"},
				Labels: map[string]string{"private_key": "must-not-pass"},
			},
			contains: "requirements.labels",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			manifest := validManifest()
			manifest.Spec.Requirements = &test.requirements
			err := ValidateManifest(manifest, ManifestConstraints{
				AppVersionID: "appver-test", TargetProfileID: "target-gpu-001", RequestedBy: "ai-ops-geon-planner",
			})
			if err == nil || !strings.Contains(err.Error(), test.contains) {
				t.Fatalf("expected error containing %q, got %v", test.contains, err)
			}
		})
	}
}

func TestValidateManifestRejectsInvalidOrConflictingRequirementsAccelerator(t *testing.T) {
	tests := []struct {
		name        string
		accelerator string
		contains    string
	}{
		{name: "invalid accelerator", accelerator: "tpu", contains: "requirements.accelerator"},
		{name: "conflicting accelerator", accelerator: "none", contains: "requirements.accelerator must match"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			manifest := validManifest()
			manifest.Spec.Requirements = &DeploymentRequirements{
				Runtime: "gpu", Resources: manifest.Spec.Resources, Accelerator: test.accelerator,
			}
			err := ValidateManifest(manifest, ManifestConstraints{
				AppVersionID: "appver-test", TargetProfileID: "target-gpu-001", RequestedBy: "ai-ops-geon-planner",
			})
			if err == nil || !strings.Contains(err.Error(), test.contains) {
				t.Fatalf("expected error containing %q, got %v", test.contains, err)
			}
		})
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
