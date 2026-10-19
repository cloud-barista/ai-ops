package resource

import (
	"math"
	"testing"
	"time"
)

func TestNormalizeJoinsNodeAndGPUFactsByResourceID(t *testing.T) {
	observedAt := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	raw := RawSnapshot{
		Source:     "test",
		ObservedAt: observedAt,
		Facts: []RawFact{
			fact("node-a", "", FactNodeUp, 1, observedAt, map[string]string{"hostname": "worker-a", "node_ip": "10.0.0.1"}),
			fact("node-a", "", FactCPUCount, 16, observedAt, nil),
			fact("node-a", "", FactCPUUtilizationPercent, 25, observedAt, nil),
			fact("node-a", "", FactMemoryTotalBytes, 64*bytesPerGiB, observedAt, nil),
			fact("node-a", "", FactMemoryAvailableBytes, 48*bytesPerGiB, observedAt, nil),
			fact("node-a", "", FactStorageTotalBytes, 200*bytesPerGiB, observedAt, nil),
			fact("node-a", "", FactStorageAvailableBytes, 150*bytesPerGiB, observedAt, nil),
			fact("node-a", "", FactAcceleratorExporterUp, 1, observedAt, nil),
			fact("node-a", "gpu-0", FactGPUMemoryFreeMiB, 40_000, observedAt, gpuLabels("0")),
			fact("node-a", "gpu-0", FactGPUMemoryUsedMiB, 7_000, observedAt, gpuLabels("0")),
			fact("node-a", "gpu-0", FactGPUMemoryReservedMiB, 1_000, observedAt, gpuLabels("0")),
			fact("node-a", "gpu-0", FactGPUUtilizationPercent, 10, observedAt, gpuLabels("0")),
		},
	}

	snapshot := Normalize(raw)
	if len(snapshot.Resources) != 1 {
		t.Fatalf("resources = %d, want 1", len(snapshot.Resources))
	}
	profile := snapshot.Resources[0]
	if profile.ResourceID != "node-a" || profile.Hostname != "worker-a" || profile.Address != "10.0.0.1" || !profile.Healthy {
		t.Fatalf("unexpected identity/health: %#v", profile)
	}
	if profile.CPU.AvailableCores == nil || math.Abs(*profile.CPU.AvailableCores-12) > 1e-9 {
		t.Fatalf("available CPU = %v, want 12", profile.CPU.AvailableCores)
	}
	if profile.Memory.AvailableMiB == nil || *profile.Memory.AvailableMiB != 48*1024 {
		t.Fatalf("available memory = %v", profile.Memory.AvailableMiB)
	}
	if len(profile.Accelerators) != 1 {
		t.Fatalf("accelerators = %d, want 1", len(profile.Accelerators))
	}
	gpu := profile.Accelerators[0]
	if gpu.Model != "Test GPU" || gpu.MemoryTotalMiB != 48_000 || gpu.MemoryAvailableMiB == nil || *gpu.MemoryAvailableMiB != 40_000 || !gpu.Healthy {
		t.Fatalf("unexpected GPU: %#v", gpu)
	}
}

func TestNormalizePreservesMissingDataAsWarnings(t *testing.T) {
	observedAt := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	snapshot := Normalize(RawSnapshot{
		Source:     "test",
		ObservedAt: observedAt,
		Facts:      []RawFact{fact("node-a", "", FactNodeUp, 1, observedAt, nil)},
	})
	profile := snapshot.Resources[0]
	for _, wanted := range []string{"CPU_CAPACITY_MISSING", "MEMORY_CAPACITY_MISSING", "STORAGE_CAPACITY_MISSING"} {
		if !contains(profile.Warnings, wanted) {
			t.Fatalf("warnings %v do not contain %s", profile.Warnings, wanted)
		}
	}
}

func TestNormalizeUsesOldestAcceptedMeasurementTime(t *testing.T) {
	queriedAt := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	snapshot := Normalize(RawSnapshot{
		Source:     "test",
		ObservedAt: queriedAt,
		Facts: []RawFact{
			fact("node-a", "", FactNodeUp, 1, queriedAt.Add(-10*time.Second), nil),
			fact("node-a", "", FactMemoryAvailableBytes, 16*bytesPerGiB, queriedAt.Add(-25*time.Second), nil),
			fact("node-b", "", FactNodeUp, 1, queriedAt.Add(-15*time.Second), nil),
			fact("node-b", "", FactCPUCount, 8, time.Time{}, nil),
		},
	})
	if !snapshot.ObservedAt.Equal(queriedAt.Add(-25 * time.Second)) {
		t.Fatalf("snapshot observed_at = %s, want oldest accepted fact", snapshot.ObservedAt)
	}
	if !snapshot.Resources[0].ObservedAt.Equal(queriedAt.Add(-25*time.Second)) ||
		!snapshot.Resources[1].ObservedAt.Equal(queriedAt.Add(-15*time.Second)) {
		t.Fatalf("resource observation times = %s, %s", snapshot.Resources[0].ObservedAt, snapshot.Resources[1].ObservedAt)
	}
	if snapshot.Resources[1].CPU.LogicalCores != 0 || !contains(snapshot.Warnings, "RAW_FACT_WITHOUT_OBSERVATION_TIME_IGNORED") {
		t.Fatalf("timestamp-free CPU fact was accepted: %#v", snapshot)
	}
}

func fact(resourceID, deviceID, kind string, value float64, observedAt time.Time, labels map[string]string) RawFact {
	if labels == nil {
		labels = map[string]string{}
	}
	return RawFact{ResourceID: resourceID, DeviceID: deviceID, Kind: kind, Value: value, Labels: labels, ObservedAt: observedAt}
}

func gpuLabels(index string) map[string]string {
	return map[string]string{"gpu": index, "modelName": "Test GPU"}
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
