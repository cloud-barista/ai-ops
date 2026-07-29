package agentcontrol

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"
)

type ResourceRecommender interface {
	Recommend(context.Context, ApplicationProfile) (RecommendationResult, error)
}

type ResourceCatalog struct {
	Version    string            `json:"version"`
	Candidates []CatalogResource `json:"candidates"`
}

type CatalogResource struct {
	CandidateID          string  `json:"candidate_id"`
	CPUCores             int     `json:"cpu_cores"`
	MemoryMiB            int     `json:"memory_mib"`
	StorageGiB           int     `json:"storage_gib"`
	AcceleratorType      string  `json:"accelerator_type,omitempty"`
	AcceleratorCount     int     `json:"accelerator_count,omitempty"`
	AcceleratorMemoryMiB int     `json:"accelerator_memory_mib,omitempty"`
	CostPerHour          float64 `json:"cost_per_hour"`
	AvailabilityScore    float64 `json:"availability_score"`
}

type RecommendationEvidence struct {
	CatalogVersion string `json:"catalog_version"`
	CandidateCount int    `json:"candidate_count"`
	FeasibleCount  int    `json:"feasible_count"`
	Mode           string `json:"mode"`
}

type RecommendationResult struct {
	ResourceRecommendation ResourceRecommendation `json:"resource_recommendation"`
	Evidence               RecommendationEvidence `json:"evidence"`
}

type CatalogResourceRecommender struct {
	Catalog ResourceCatalog
	Now     func() time.Time
}

func LoadResourceCatalog(path string) (ResourceCatalog, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return ResourceCatalog{}, fmt.Errorf("read resource catalog: %w", err)
	}
	var catalog ResourceCatalog
	decoder := json.NewDecoder(strings.NewReader(string(content)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&catalog); err != nil {
		return ResourceCatalog{}, fmt.Errorf("parse resource catalog: %w", err)
	}
	if strings.TrimSpace(catalog.Version) == "" {
		return ResourceCatalog{}, fmt.Errorf("resource catalog version is required")
	}
	if len(catalog.Candidates) == 0 {
		return ResourceCatalog{}, fmt.Errorf("resource catalog candidates are required")
	}
	for _, candidate := range catalog.Candidates {
		if strings.TrimSpace(candidate.CandidateID) == "" {
			return ResourceCatalog{}, fmt.Errorf("resource catalog candidate_id is required")
		}
		if candidate.AvailabilityScore < 0 || candidate.AvailabilityScore > 1 {
			return ResourceCatalog{}, fmt.Errorf("availability_score must be between 0 and 1")
		}
	}
	return catalog, nil
}

func (recommender CatalogResourceRecommender) Recommend(
	ctx context.Context,
	profile ApplicationProfile,
) (RecommendationResult, error) {
	if err := ctx.Err(); err != nil {
		return RecommendationResult{}, err
	}
	if len(recommender.Catalog.Candidates) == 0 {
		return RecommendationResult{}, fmt.Errorf("resource catalog candidates are required")
	}

	requirements := profile.Requirements
	candidates := make([]ResourceCandidate, 0, len(recommender.Catalog.Candidates))
	feasibleCount := 0
	for _, catalogCandidate := range recommender.Catalog.Candidates {
		candidate := scoreCatalogCandidate(requirements, catalogCandidate)
		if candidate.Feasible {
			feasibleCount++
		}
		candidates = append(candidates, candidate)
	}
	sort.SliceStable(candidates, func(left, right int) bool {
		if candidates[left].Feasible != candidates[right].Feasible {
			return candidates[left].Feasible
		}
		return candidates[left].Scores.Total > candidates[right].Scores.Total
	})

	status := "FOUND"
	if feasibleCount == 0 {
		status = "RETRY_REQUIRED"
	}
	selected := candidates[0]
	now := time.Now
	if recommender.Now != nil {
		now = recommender.Now
	}
	recommendationID := fmt.Sprintf("resource-rec-%d", now().UTC().UnixNano())
	return RecommendationResult{
		ResourceRecommendation: ResourceRecommendation{
			RecommendationID:    recommendationID,
			ProfileID:           profile.ProfileID,
			SnapshotID:          "mock-catalog-" + recommender.Catalog.Version,
			Status:              status,
			SelectedCandidateID: selected.CandidateID,
			Candidates:          candidates,
		},
		Evidence: RecommendationEvidence{
			CatalogVersion: recommender.Catalog.Version,
			CandidateCount: len(candidates),
			FeasibleCount:  feasibleCount,
			Mode:           "mock_catalog",
		},
	}, nil
}

func scoreCatalogCandidate(
	requirements ApplicationRequirements,
	resource CatalogResource,
) ResourceCandidate {
	reasons := make([]string, 0, 7)
	addMinimumReason := func(name string, actual, required int) {
		if actual < required {
			reasons = append(
				reasons,
				fmt.Sprintf("%s is below minimum: actual=%d required=%d", name, actual, required),
			)
		}
	}
	addMinimumReason("cpu_cores", resource.CPUCores, requirements.Compute.CPUCoresMin)
	addMinimumReason("memory_mib", resource.MemoryMiB, requirements.Compute.MemoryMiBMin)
	addMinimumReason("storage_gib", resource.StorageGiB, requirements.Compute.StorageGiBMin)
	if requirements.Accelerator.Required {
		if !strings.EqualFold(resource.AcceleratorType, requirements.Accelerator.Type) {
			reasons = append(
				reasons,
				fmt.Sprintf(
					"accelerator_type mismatch: actual=%s required=%s",
					resource.AcceleratorType,
					requirements.Accelerator.Type,
				),
			)
		}
		addMinimumReason(
			"accelerator_count",
			resource.AcceleratorCount,
			requirements.Accelerator.CountMin,
		)
		addMinimumReason(
			"accelerator_memory_mib",
			resource.AcceleratorMemoryMiB,
			requirements.Accelerator.MemoryMiBMinPerDevice,
		)
	}

	resourceFit := averageScore([]float64{
		fitRatio(resource.CPUCores, requirements.Compute.CPUCoresMin),
		fitRatio(resource.MemoryMiB, requirements.Compute.MemoryMiBMin),
		fitRatio(resource.StorageGiB, requirements.Compute.StorageGiBMin),
		acceleratorFitRatio(resource, requirements.Accelerator),
	})
	costEfficiency := clampScore(1 / (1 + resource.CostPerHour))
	availability := clampScore(resource.AvailabilityScore)
	sloHeadroom := clampScore(0.5 + 0.5*resourceFit)
	total := clampScore(
		0.45*resourceFit +
			0.20*sloHeadroom +
			0.25*costEfficiency +
			0.10*availability,
	)
	return ResourceCandidate{
		CandidateID: resource.CandidateID,
		Feasible:    len(reasons) == 0,
		DesiredInfrastructure: DesiredInfrastructure{
			NodeCount:         maxInt(1, requirements.Deployment.ReplicasMin),
			CPUCoresPerNode:   resource.CPUCores,
			MemoryMiBPerNode:  resource.MemoryMiB,
			StorageGiBPerNode: resource.StorageGiB,
			Accelerator: AcceleratorAllocation{
				Type:                  resource.AcceleratorType,
				Count:                 resource.AcceleratorCount,
				MemoryMiBMinPerDevice: resource.AcceleratorMemoryMiB,
			},
			Isolation: requirements.Deployment.Isolation,
		},
		ResourceHints: []string{"catalog:" + resource.CandidateID},
		Scores: ResourceScores{
			ResourceFit:    roundScore(resourceFit),
			SLOHeadroom:    roundScore(sloHeadroom),
			CostEfficiency: roundScore(costEfficiency),
			Availability:   roundScore(availability),
			Total:          roundScore(total),
		},
		RejectionReasons: reasons,
	}
}

func fitRatio(actual, required int) float64 {
	if required <= 0 {
		return 1
	}
	if actual <= 0 {
		return 0
	}
	ratio := float64(actual) / float64(required)
	if ratio >= 1 {
		return 1 / ratio
	}
	return ratio
}

func acceleratorFitRatio(resource CatalogResource, requirements AcceleratorRequirements) float64 {
	if !requirements.Required {
		if resource.AcceleratorCount == 0 {
			return 1
		}
		return 0.7
	}
	if !strings.EqualFold(resource.AcceleratorType, requirements.Type) {
		return 0
	}
	return averageScore([]float64{
		fitRatio(resource.AcceleratorCount, requirements.CountMin),
		fitRatio(resource.AcceleratorMemoryMiB, requirements.MemoryMiBMinPerDevice),
	})
}

func averageScore(scores []float64) float64 {
	if len(scores) == 0 {
		return 0
	}
	total := 0.0
	for _, score := range scores {
		total += clampScore(score)
	}
	return total / float64(len(scores))
}

func clampScore(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}

func roundScore(value float64) float64 {
	return math.Round(clampScore(value)*10000) / 10000
}
