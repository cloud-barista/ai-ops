package runtime

import (
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestProfileFromTargetMapsRuntimeAdapterDefaults(t *testing.T) {
	profile := ProfileFromTarget(model.TargetProfile{
		TargetProfileID: "target-gpu-001",
		Runtime: model.TargetRuntime{
			RuntimeType:   "gpu",
			Accelerator:   "nvidia",
			OperatingMode: "vm_process",
		},
	})
	if profile.RuntimeProfileID != "target-gpu-001-runtime-derived" || profile.RuntimeType != "gpu" || profile.AdapterType != "gpu_vm" || profile.Accelerator != "nvidia" || profile.OperatingMode != "vm_process" {
		t.Fatalf("unexpected derived runtime profile: %+v", profile)
	}
}

func TestProfileFromMockTargetAlwaysUsesMockAdapter(t *testing.T) {
	profile := ProfileFromTarget(model.TargetProfile{
		TargetProfileID: "target-mock-001",
		CSP:             "mock",
		Runtime: model.TargetRuntime{
			RuntimeType:   "cpu",
			Accelerator:   "none",
			OperatingMode: "local_mock",
		},
	})
	if profile.AdapterType != "mock" {
		t.Fatalf("adapter type = %q, want mock", profile.AdapterType)
	}
}
