package gpuvm

import (
	"context"
	"errors"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
)

func TestValidateTargetRequiresGPUCount(t *testing.T) {
	adapter := New(NewDryRunRunner())
	err := adapter.ValidateTarget(context.Background(), model.TargetProfile{
		TargetProfileID: "target-gpu-001",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "gpu-vm.example.internal",
			CredentialRef: "cred://local/gpu-vm-001",
		},
		Runtime: model.TargetRuntime{
			RuntimeType: "gpu",
			Accelerator: "nvidia",
		},
		Storage: &model.Storage{
			ArtifactDir: "/opt/aiapp/artifacts",
			LogDir:      "/var/log/aiapp",
		},
	})
	if err == nil {
		t.Fatal("expected missing gpu count error")
	}
}

func TestHealthCheckMapsNvidiaSMIFailure(t *testing.T) {
	adapter := New(failingRunner{})
	err := adapter.HealthCheck(context.Background(), model.RuntimeProfile{
		RuntimeProfileID: "rt-gpu-001",
		RuntimeType:      "gpu",
		Accelerator:      "nvidia",
		AdapterType:      "gpu_vm",
		OperatingMode:    "vm_process",
	}, validTarget())
	if err == nil {
		t.Fatal("expected nvidia-smi failure")
	}
	if err.Error() == "" {
		t.Fatal("expected error message")
	}
}

func TestHealthCheckDryRun(t *testing.T) {
	adapter := New(NewDryRunRunner())
	err := adapter.HealthCheck(context.Background(), model.RuntimeProfile{
		RuntimeProfileID: "rt-gpu-001",
		RuntimeType:      "gpu",
		Accelerator:      "nvidia",
		AdapterType:      "gpu_vm",
		OperatingMode:    "vm_process",
	}, validTarget())
	if err != nil {
		t.Fatal(err)
	}
}

type failingRunner struct{}

func (r failingRunner) Run(ctx context.Context, target model.TargetProfile, command cpuvm.Command) (cpuvm.Result, error) {
	return cpuvm.Result{}, errors.New("nvidia-smi not found")
}

func validTarget() model.TargetProfile {
	return model.TargetProfile{
		TargetProfileID: "target-gpu-001",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "gpu-vm.example.internal",
			CredentialRef: "cred://local/gpu-vm-001",
		},
		Runtime: model.TargetRuntime{
			RuntimeType: "gpu",
			Accelerator: "nvidia",
		},
		GPU: &model.GPUProfile{
			Vendor:         "nvidia",
			Count:          1,
			DriverRequired: true,
		},
		Storage: &model.Storage{
			ArtifactDir: "/opt/aiapp/artifacts",
			LogDir:      "/var/log/aiapp",
		},
	}
}
