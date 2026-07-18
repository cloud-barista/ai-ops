package runtime

import "github.com/khu/ai-app-deployer/internal/model"

// ProfileFromTarget derives the default execution profile for a target when a
// caller does not need a separately registered Runtime Profile. The derived
// profile is transient; it is not persisted as a Runtime Profile record.
func ProfileFromTarget(target model.TargetProfile) model.RuntimeProfile {
	adapterType := target.Runtime.RuntimeType
	switch target.Runtime.RuntimeType {
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
	return model.RuntimeProfile{
		RuntimeProfileID: target.TargetProfileID + "-runtime-derived",
		RuntimeType:      target.Runtime.RuntimeType,
		Accelerator:      target.Runtime.Accelerator,
		AdapterType:      adapterType,
		OperatingMode:    target.Runtime.OperatingMode,
	}
}
