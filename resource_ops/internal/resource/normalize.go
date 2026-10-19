package resource

import (
	"math"
	"sort"
)

const (
	bytesPerMiB = 1024 * 1024
	bytesPerGiB = 1024 * 1024 * 1024
)

type resourceBuilder struct {
	profile       ResourceProfile
	nodeUpSeen    bool
	acceleratorUp bool
	acceleratorOK bool
	devices       map[string]*acceleratorBuilder
}

type acceleratorBuilder struct {
	profile  AcceleratorProfile
	freeMiB  *float64
	usedMiB  *float64
	reserved *float64
}

// Normalize converts source-neutral facts into a deterministic resource
// snapshot. Source-specific metric names and response envelopes are handled by
// adapters before this function is called.
func Normalize(raw RawSnapshot) ResourceSnapshot {
	builders := make(map[string]*resourceBuilder)
	warnings := append([]string(nil), raw.Warnings...)
	observedAt := raw.ObservedAt
	hasObservedFact := false
	for _, fact := range raw.Facts {
		if fact.ResourceID == "" {
			warnings = appendUnique(warnings, "RAW_FACT_WITHOUT_RESOURCE_ID_IGNORED")
			continue
		}
		if fact.ObservedAt.IsZero() {
			warnings = appendUnique(warnings, "RAW_FACT_WITHOUT_OBSERVATION_TIME_IGNORED")
			continue
		}
		if math.IsNaN(fact.Value) || math.IsInf(fact.Value, 0) {
			warnings = appendUnique(warnings, "NON_FINITE_RAW_FACT_IGNORED")
			continue
		}
		// A snapshot built from several metrics cannot be newer than its
		// oldest accepted fact. raw.ObservedAt may be a query time.
		if !hasObservedFact || fact.ObservedAt.Before(observedAt) {
			observedAt = fact.ObservedAt
		}
		hasObservedFact = true
		builder := builders[fact.ResourceID]
		if builder == nil {
			builder = &resourceBuilder{
				profile: ResourceProfile{
					ResourceID:   fact.ResourceID,
					Accelerators: []AcceleratorProfile{},
					Labels:       map[string]string{},
					Warnings:     []string{},
				},
				devices: make(map[string]*acceleratorBuilder),
			}
			builders[fact.ResourceID] = builder
		}
		mergeIdentity(&builder.profile, fact.Labels)
		if builder.profile.ObservedAt.IsZero() || fact.ObservedAt.Before(builder.profile.ObservedAt) {
			builder.profile.ObservedAt = fact.ObservedAt
		}
		applyFact(builder, fact)
	}

	resources := make([]ResourceProfile, 0, len(builders))
	for _, builder := range builders {
		finalizeBuilder(builder)
		resources = append(resources, builder.profile)
	}
	sort.Slice(resources, func(i, j int) bool {
		return resources[i].ResourceID < resources[j].ResourceID
	})
	sort.Strings(warnings)
	if warnings == nil {
		warnings = []string{}
	}
	return ResourceSnapshot{
		SchemaVersion: SchemaVersion,
		Source:        raw.Source,
		ObservedAt:    observedAt,
		Resources:     resources,
		Warnings:      warnings,
	}
}

func applyFact(builder *resourceBuilder, fact RawFact) {
	switch fact.Kind {
	case FactNodeUp:
		builder.nodeUpSeen = true
		builder.profile.Healthy = fact.Value > 0
	case FactAcceleratorExporterUp:
		builder.acceleratorOK = true
		builder.acceleratorUp = fact.Value > 0
	case FactCPUCount:
		builder.profile.CPU.LogicalCores = int(math.Round(fact.Value))
	case FactCPUUtilizationPercent:
		value := clamp(fact.Value, 0, 100)
		builder.profile.CPU.UtilizationPercent = &value
	case FactMemoryTotalBytes:
		builder.profile.Memory.TotalMiB = fact.Value / bytesPerMiB
	case FactMemoryAvailableBytes:
		value := fact.Value / bytesPerMiB
		builder.profile.Memory.AvailableMiB = &value
	case FactStorageTotalBytes:
		builder.profile.Storage.TotalGiB = fact.Value / bytesPerGiB
	case FactStorageAvailableBytes:
		value := fact.Value / bytesPerGiB
		builder.profile.Storage.AvailableGiB = &value
	case FactNodeInfo, FactOSInfo:
		// Identity labels are applied before this switch.
	case FactGPUUtilizationPercent, FactGPUMemoryFreeMiB, FactGPUMemoryUsedMiB, FactGPUMemoryReservedMiB:
		applyAcceleratorFact(builder, fact)
	}
}

func applyAcceleratorFact(builder *resourceBuilder, fact RawFact) {
	deviceID := fact.DeviceID
	if deviceID == "" {
		deviceID = firstNonEmpty(fact.Labels["UUID"], fact.Labels["gpu"], fact.Labels["device"])
	}
	if deviceID == "" {
		builder.profile.Warnings = appendUnique(builder.profile.Warnings, "GPU_FACT_WITHOUT_DEVICE_ID_IGNORED")
		return
	}
	device := builder.devices[deviceID]
	if device == nil {
		device = &acceleratorBuilder{profile: AcceleratorProfile{
			DeviceID: deviceID,
			Index:    fact.Labels["gpu"],
			Type:     "GPU",
			Model:    firstNonEmpty(fact.Labels["modelName"], fact.Labels["model"]),
		}}
		builder.devices[deviceID] = device
	}
	if device.profile.Model == "" {
		device.profile.Model = firstNonEmpty(fact.Labels["modelName"], fact.Labels["model"])
	}
	switch fact.Kind {
	case FactGPUUtilizationPercent:
		value := clamp(fact.Value, 0, 100)
		device.profile.UtilizationPercent = &value
	case FactGPUMemoryFreeMiB:
		value := math.Max(0, fact.Value)
		device.freeMiB = &value
	case FactGPUMemoryUsedMiB:
		value := math.Max(0, fact.Value)
		device.usedMiB = &value
	case FactGPUMemoryReservedMiB:
		value := math.Max(0, fact.Value)
		device.reserved = &value
	}
}

func finalizeBuilder(builder *resourceBuilder) {
	profile := &builder.profile
	if !builder.nodeUpSeen {
		profile.Warnings = appendUnique(profile.Warnings, "NODE_HEALTH_UNKNOWN")
	}
	if profile.CPU.LogicalCores <= 0 {
		profile.Warnings = appendUnique(profile.Warnings, "CPU_CAPACITY_MISSING")
	}
	if profile.CPU.UtilizationPercent != nil && profile.CPU.LogicalCores > 0 {
		available := float64(profile.CPU.LogicalCores) * (1 - *profile.CPU.UtilizationPercent/100)
		profile.CPU.AvailableCores = &available
	} else if profile.CPU.LogicalCores > 0 {
		profile.Warnings = appendUnique(profile.Warnings, "CPU_AVAILABILITY_MISSING")
	}
	if profile.Memory.TotalMiB <= 0 {
		profile.Warnings = appendUnique(profile.Warnings, "MEMORY_CAPACITY_MISSING")
	}
	if profile.Memory.AvailableMiB == nil {
		profile.Warnings = appendUnique(profile.Warnings, "MEMORY_AVAILABILITY_MISSING")
	}
	if profile.Storage.TotalGiB <= 0 {
		profile.Warnings = appendUnique(profile.Warnings, "STORAGE_CAPACITY_MISSING")
	}
	if profile.Storage.AvailableGiB == nil {
		profile.Warnings = appendUnique(profile.Warnings, "STORAGE_AVAILABILITY_MISSING")
	}

	devices := make([]AcceleratorProfile, 0, len(builder.devices))
	for _, device := range builder.devices {
		if device.freeMiB != nil {
			available := *device.freeMiB
			device.profile.MemoryAvailableMiB = &available
		}
		for _, part := range []*float64{device.freeMiB, device.usedMiB, device.reserved} {
			if part != nil {
				device.profile.MemoryTotalMiB += *part
			}
		}
		device.profile.Healthy = builder.acceleratorOK && builder.acceleratorUp
		devices = append(devices, device.profile)
	}
	sort.Slice(devices, func(i, j int) bool {
		if devices[i].Index == devices[j].Index {
			return devices[i].DeviceID < devices[j].DeviceID
		}
		return devices[i].Index < devices[j].Index
	})
	profile.Accelerators = devices
	if len(devices) > 0 && !builder.acceleratorOK {
		profile.Warnings = appendUnique(profile.Warnings, "ACCELERATOR_HEALTH_UNKNOWN")
	}
	if profile.ObservedAt.IsZero() {
		profile.Warnings = appendUnique(profile.Warnings, "OBSERVATION_TIME_MISSING")
	}
	sort.Strings(profile.Warnings)
	if profile.Warnings == nil {
		profile.Warnings = []string{}
	}
}

func mergeIdentity(profile *ResourceProfile, labels map[string]string) {
	profile.Hostname = firstNonEmpty(profile.Hostname, labels["hostname"], labels["exported_hostname"])
	profile.Address = firstNonEmpty(profile.Address, labels["node_ip"])
	profile.Provider = firstNonEmpty(profile.Provider, labels["provider"])
	profile.Region = firstNonEmpty(profile.Region, labels["region"])
	profile.ResourceType = firstNonEmpty(profile.ResourceType, labels["resource_type"], labels["infra_type"])
	profile.Architecture = firstNonEmpty(profile.Architecture, labels["machine"], labels["architecture"])
	profile.OperatingSys = firstNonEmpty(profile.OperatingSys, labels["pretty_name"], labels["os"])
	for _, key := range []string{"node_id", "hostname", "node_ip", "provider", "region", "resource_type", "infra_type"} {
		if value := labels[key]; value != "" {
			profile.Labels[key] = value
		}
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func appendUnique(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}

func clamp(value, minimum, maximum float64) float64 {
	return math.Max(minimum, math.Min(maximum, value))
}
