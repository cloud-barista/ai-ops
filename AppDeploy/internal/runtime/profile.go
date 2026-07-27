package runtime

import "github.com/khu/ai-app-deployer/internal/model"

// ConfigFromTarget derives the transient execution configuration for a Target.
// It is never registered or persisted separately.
func ConfigFromTarget(target model.TargetProfile) model.RuntimeConfig {
	adapterType := target.Runtime.RuntimeType
	switch target.Runtime.RuntimeType {
	case "local":
		adapterType = "local_process"
	case "cpu":
		adapterType = "cpu_vm"
	case "gpu":
		adapterType = "gpu_vm"
	case "aiinfra":
		adapterType = "etri_aiinfra"
	case "mock":
		adapterType = "mock"
	}
	if target.CSP == "mock" {
		adapterType = "mock"
	}
	if target.CSP == "local" && target.Runtime.RuntimeType == "local" {
		adapterType = "local_process"
	}
	return model.RuntimeConfig{
		RuntimeType:   target.Runtime.RuntimeType,
		Accelerator:   target.Runtime.Accelerator,
		AdapterType:   adapterType,
		OperatingMode: target.Runtime.OperatingMode,
	}
}
