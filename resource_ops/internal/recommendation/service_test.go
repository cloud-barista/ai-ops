package recommendation

import (
	"context"
	"errors"
	"testing"
	"time"

	"ai-mcmp/internal/resource"
)

type fakeSource struct {
	raw resource.RawSnapshot
	err error
}

func (source fakeSource) Name() string { return "fake" }
func (source fakeSource) Collect(context.Context) (resource.RawSnapshot, error) {
	return source.raw, source.err
}

func TestRecommendFiltersRanksAndExplains(t *testing.T) {
	service, err := New(fakeSource{raw: testSnapshot()})
	if err != nil {
		t.Fatal(err)
	}
	result, err := service.Recommend(context.Background(), resource.ResourceQuery{
		ProfileID: "profile-1",
		Constraints: resource.QueryConstraints{
			CPUCoresMin:   8,
			MemoryMiBMin:  16 * 1024,
			StorageGiBMin: 40,
			Accelerator: resource.AcceleratorConstraint{
				Required: true, Type: "GPU", CountMin: 1, MemoryMiBMinPerDevice: 32 * 1024,
			},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.RequestID != "profile-1" || result.Source != "fake" {
		t.Fatalf("unexpected envelope: %#v", result)
	}
	if result.Summary.Discovered != 2 || result.Summary.Feasible != 1 || result.Summary.Excluded != 1 {
		t.Fatalf("unexpected summary: %#v", result.Summary)
	}
	if len(result.Recommendations) != 1 || result.Recommendations[0].Resource.ResourceID != "gpu-node" || result.Recommendations[0].Rank != 1 {
		t.Fatalf("unexpected ranking: %#v", result.Recommendations)
	}
	if len(result.Recommendations[0].Reasons) < 4 {
		t.Fatalf("recommendation reasons missing: %#v", result.Recommendations[0].Reasons)
	}
	if result.ExcludedResources[0].Failures[0].Code != "ACCELERATOR_CAPACITY_INSUFFICIENT" {
		t.Fatalf("unexpected exclusion: %#v", result.ExcludedResources[0])
	}
}

func TestRecommendReturnsNoFeasibleResourceWithoutDeploymentDecision(t *testing.T) {
	service, _ := New(fakeSource{raw: testSnapshot()})
	result, err := service.Recommend(context.Background(), resource.ResourceQuery{
		RequestID: "request-1",
		Constraints: resource.QueryConstraints{
			CPUCoresMin: 256, MemoryMiBMin: 1024,
			Accelerator: resource.AcceleratorConstraint{},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Recommendations) != 0 || !contains(result.Warnings, "NO_FEASIBLE_RESOURCE") {
		t.Fatalf("unexpected no-candidate result: %#v", result)
	}
}

func TestRecommendValidatesQueryBeforeCallingSource(t *testing.T) {
	service, _ := New(fakeSource{err: errors.New("must not be reached")})
	_, err := service.Recommend(context.Background(), resource.ResourceQuery{RequestID: "r"})
	if !errors.Is(err, ErrInvalidQuery) {
		t.Fatalf("error = %v, want ErrInvalidQuery", err)
	}
}

func TestRecommendClassifiesSourceFailure(t *testing.T) {
	service, _ := New(fakeSource{err: errors.New("offline")})
	_, err := service.Recommend(context.Background(), resource.ResourceQuery{
		RequestID:   "r",
		Constraints: resource.QueryConstraints{CPUCoresMin: 1, MemoryMiBMin: 1},
	})
	var sourceError *SourceUnavailableError
	if !errors.As(err, &sourceError) {
		t.Fatalf("error = %v, want SourceUnavailableError", err)
	}
}

func testSnapshot() resource.RawSnapshot {
	observedAt := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	facts := []resource.RawFact{}
	facts = append(facts, nodeFacts("cpu-node", observedAt, 16, 10, 64*1024, 48*1024, 100, 80)...)
	facts = append(facts, nodeFacts("gpu-node", observedAt, 32, 20, 128*1024, 100*1024, 500, 400)...)
	facts = append(facts,
		rawFact("gpu-node", "", resource.FactAcceleratorExporterUp, 1, observedAt, nil),
		rawFact("gpu-node", "gpu-0", resource.FactGPUMemoryFreeMiB, 40*1024, observedAt, gpuLabels()),
		rawFact("gpu-node", "gpu-0", resource.FactGPUMemoryUsedMiB, 7*1024, observedAt, gpuLabels()),
		rawFact("gpu-node", "gpu-0", resource.FactGPUMemoryReservedMiB, 1024, observedAt, gpuLabels()),
		rawFact("gpu-node", "gpu-0", resource.FactGPUUtilizationPercent, 5, observedAt, gpuLabels()),
	)
	return resource.RawSnapshot{Source: "fake", ObservedAt: observedAt, Facts: facts, Warnings: []string{}}
}

func nodeFacts(id string, observedAt time.Time, cpus int, cpuUtil float64, memoryTotalMiB, memoryAvailableMiB float64, storageTotalGiB, storageAvailableGiB float64) []resource.RawFact {
	const bytesPerMiB = 1024 * 1024
	const bytesPerGiB = 1024 * 1024 * 1024
	labels := map[string]string{"hostname": id, "node_ip": "10.0.0.1"}
	return []resource.RawFact{
		rawFact(id, "", resource.FactNodeUp, 1, observedAt, labels),
		rawFact(id, "", resource.FactCPUCount, float64(cpus), observedAt, labels),
		rawFact(id, "", resource.FactCPUUtilizationPercent, cpuUtil, observedAt, labels),
		rawFact(id, "", resource.FactMemoryTotalBytes, memoryTotalMiB*bytesPerMiB, observedAt, labels),
		rawFact(id, "", resource.FactMemoryAvailableBytes, memoryAvailableMiB*bytesPerMiB, observedAt, labels),
		rawFact(id, "", resource.FactStorageTotalBytes, storageTotalGiB*bytesPerGiB, observedAt, labels),
		rawFact(id, "", resource.FactStorageAvailableBytes, storageAvailableGiB*bytesPerGiB, observedAt, labels),
	}
}

func rawFact(resourceID, deviceID, kind string, value float64, observedAt time.Time, labels map[string]string) resource.RawFact {
	return resource.RawFact{ResourceID: resourceID, DeviceID: deviceID, Kind: kind, Value: value, Labels: labels, ObservedAt: observedAt}
}

func gpuLabels() map[string]string { return map[string]string{"gpu": "0", "modelName": "Test GPU"} }

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
