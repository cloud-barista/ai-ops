package agentcontrol

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestCatalogResourceRecommenderSelectsGPUCandidate(t *testing.T) {
	recommender := CatalogResourceRecommender{Catalog: testResourceCatalog()}
	profile := analyzerProfile(t, StructuredAppSpec{
		AppID:                "gpu-service",
		CPUCores:             4,
		MemoryMiB:            16384,
		StorageGiB:           20,
		AcceleratorType:      "GPU",
		AcceleratorCount:     1,
		AcceleratorMemoryMiB: 16384,
		ReplicasMin:          1,
		ReplicasMax:          2,
	})

	result, err := recommender.Recommend(context.Background(), profile)
	if err != nil {
		t.Fatalf("recommend GPU resource: %v", err)
	}
	recommendation := result.ResourceRecommendation
	if recommendation.SelectedCandidateID != "mock-gpu-l4" {
		t.Fatalf("selected candidate = %q, want mock-gpu-l4", recommendation.SelectedCandidateID)
	}
	if len(recommendation.Candidates) == 0 || !recommendation.Candidates[0].Feasible {
		t.Fatalf("GPU candidate is not feasible: %#v", recommendation.Candidates)
	}
}

func TestCatalogResourceRecommenderPrefersLowerCostCPUCandidate(t *testing.T) {
	recommender := CatalogResourceRecommender{Catalog: testResourceCatalog()}
	profile := analyzerProfile(t, StructuredAppSpec{
		AppID:       "cpu-service",
		CPUCores:    2,
		MemoryMiB:   4096,
		StorageGiB:  20,
		ReplicasMin: 1,
		ReplicasMax: 2,
	})

	result, err := recommender.Recommend(context.Background(), profile)
	if err != nil {
		t.Fatalf("recommend CPU resource: %v", err)
	}
	if result.ResourceRecommendation.SelectedCandidateID != "mock-cpu-balanced" {
		t.Fatalf(
			"selected candidate = %q, want mock-cpu-balanced",
			result.ResourceRecommendation.SelectedCandidateID,
		)
	}
}

func TestCatalogResourceRecommenderReturnsClosestInfeasibleCandidate(t *testing.T) {
	recommender := CatalogResourceRecommender{Catalog: testResourceCatalog()}
	profile := analyzerProfile(t, StructuredAppSpec{
		AppID:                "large-gpu-service",
		CPUCores:             16,
		MemoryMiB:            65536,
		StorageGiB:           100,
		AcceleratorType:      "GPU",
		AcceleratorCount:     2,
		AcceleratorMemoryMiB: 98304,
		ReplicasMin:          1,
		ReplicasMax:          2,
	})

	result, err := recommender.Recommend(context.Background(), profile)
	if err != nil {
		t.Fatalf("recommend closest resource: %v", err)
	}
	recommendation := result.ResourceRecommendation
	if recommendation.SelectedCandidateID == "" {
		t.Fatal("closest candidate must be selected for RETRY evidence")
	}
	selected := recommendation.Candidates[0]
	if selected.Feasible {
		t.Fatalf("oversized profile unexpectedly has a feasible candidate: %#v", selected)
	}
	if len(selected.RejectionReasons) == 0 {
		t.Fatalf("infeasible candidate has no rejection reasons: %#v", selected)
	}
}

func TestLoadResourceCatalogRejectsInvalidJSON(t *testing.T) {
	path := filepath.Join(t.TempDir(), "catalog.json")
	if err := os.WriteFile(path, []byte(`{"version":`), 0o600); err != nil {
		t.Fatalf("write invalid catalog: %v", err)
	}
	if _, err := LoadResourceCatalog(path); err == nil {
		t.Fatal("invalid catalog JSON must be rejected")
	}
}

func TestCatalogResourceRecommenderSortsByTotalScore(t *testing.T) {
	recommender := CatalogResourceRecommender{Catalog: testResourceCatalog()}
	profile := analyzerProfile(t, StructuredAppSpec{
		AppID:       "cpu-service",
		CPUCores:    2,
		MemoryMiB:   4096,
		StorageGiB:  20,
		ReplicasMin: 1,
		ReplicasMax: 2,
	})

	result, err := recommender.Recommend(context.Background(), profile)
	if err != nil {
		t.Fatalf("recommend resources: %v", err)
	}
	candidates := result.ResourceRecommendation.Candidates
	for index := 1; index < len(candidates); index++ {
		if candidates[index-1].Scores.Total < candidates[index].Scores.Total {
			t.Fatalf("candidates are not sorted by total score: %#v", candidates)
		}
	}
}

func analyzerProfile(t *testing.T, spec StructuredAppSpec) ApplicationProfile {
	t.Helper()
	result, err := (LocalRequirementAnalyzer{}).Analyze(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec:   &spec,
	})
	if err != nil {
		t.Fatalf("analyze structured profile: %v", err)
	}
	return result.ApplicationProfile
}

func testResourceCatalog() ResourceCatalog {
	return ResourceCatalog{
		Version: "1.0",
		Candidates: []CatalogResource{
			{
				CandidateID:       "mock-cpu-balanced",
				CPUCores:          4,
				MemoryMiB:         8192,
				StorageGiB:        100,
				CostPerHour:       0.12,
				AvailabilityScore: 0.99,
			},
			{
				CandidateID:          "mock-gpu-l4",
				CPUCores:             8,
				MemoryMiB:            32768,
				StorageGiB:           200,
				AcceleratorType:      "GPU",
				AcceleratorCount:     1,
				AcceleratorMemoryMiB: 24576,
				CostPerHour:          0.8,
				AvailabilityScore:    0.96,
			},
			{
				CandidateID:          "mock-gpu-high-memory",
				CPUCores:             16,
				MemoryMiB:            65536,
				StorageGiB:           500,
				AcceleratorType:      "GPU",
				AcceleratorCount:     1,
				AcceleratorMemoryMiB: 49152,
				CostPerHour:          1.7,
				AvailabilityScore:    0.9,
			},
		},
	}
}
