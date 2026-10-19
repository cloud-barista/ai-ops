// Package recommendation applies query validation, mandatory filtering,
// scoring, and deterministic ranking to normalized resource snapshots.
package recommendation

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"ai-mcmp/internal/resource"
)

const (
	defaultMaxResults = 3
	maxResultsLimit   = 100
)

var ErrInvalidQuery = errors.New("invalid resource query")

type SourceUnavailableError struct{ Err error }

func (errorValue *SourceUnavailableError) Error() string {
	return "resource source unavailable: " + errorValue.Err.Error()
}

func (errorValue *SourceUnavailableError) Unwrap() error { return errorValue.Err }

type Service struct {
	source resource.Source
}

func New(source resource.Source) (*Service, error) {
	if source == nil {
		return nil, errors.New("resource source is required")
	}
	return &Service{source: source}, nil
}

func (service *Service) SourceName() string { return service.source.Name() }

func (service *Service) Recommend(ctx context.Context, query resource.ResourceQuery) (resource.ResourceRecommendation, error) {
	query = applyDefaults(query)
	if err := validateQuery(query); err != nil {
		return resource.ResourceRecommendation{}, fmt.Errorf("%w: %v", ErrInvalidQuery, err)
	}
	raw, err := service.source.Collect(ctx)
	if err != nil {
		return resource.ResourceRecommendation{}, &SourceUnavailableError{Err: err}
	}
	if raw.Source == "" {
		raw.Source = service.source.Name()
	}
	snapshot := resource.Normalize(raw)

	ranked := make([]resource.RankedResource, 0, len(snapshot.Resources))
	excluded := make([]resource.ExcludedResource, 0)
	for _, candidate := range snapshot.Resources {
		failures := evaluate(candidate, query)
		if len(failures) > 0 {
			excluded = append(excluded, resource.ExcludedResource{
				ResourceID: candidate.ResourceID,
				Failures:   failures,
			})
			continue
		}
		ranked = append(ranked, score(candidate, query))
	}
	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].Score == ranked[j].Score {
			return ranked[i].Resource.ResourceID < ranked[j].Resource.ResourceID
		}
		return ranked[i].Score > ranked[j].Score
	})
	for index := range ranked {
		ranked[index].Rank = index + 1
	}
	sort.Slice(excluded, func(i, j int) bool {
		return excluded[i].ResourceID < excluded[j].ResourceID
	})

	feasibleCount := len(ranked)
	if len(ranked) > query.Preferences.MaxResults {
		ranked = ranked[:query.Preferences.MaxResults]
	}
	warnings := append([]string(nil), snapshot.Warnings...)
	if feasibleCount == 0 {
		warnings = appendUnique(warnings, "NO_FEASIBLE_RESOURCE")
	}
	if warnings == nil {
		warnings = []string{}
	}
	return resource.ResourceRecommendation{
		SchemaVersion:     resource.SchemaVersion,
		RequestID:         query.RequestID,
		ProfileID:         query.ProfileID,
		Source:            snapshot.Source,
		ObservedAt:        snapshot.ObservedAt,
		Query:             query,
		Recommendations:   ranked,
		ExcludedResources: excluded,
		Summary: resource.RecommendationSummary{
			Discovered: len(snapshot.Resources),
			Feasible:   feasibleCount,
			Excluded:   len(excluded),
			Returned:   len(ranked),
		},
		Warnings: warnings,
	}, nil
}

func applyDefaults(query resource.ResourceQuery) resource.ResourceQuery {
	query.RequestID = strings.TrimSpace(query.RequestID)
	query.ProfileID = strings.TrimSpace(query.ProfileID)
	if query.SchemaVersion == "" {
		query.SchemaVersion = resource.SchemaVersion
	}
	if query.RequestID == "" {
		query.RequestID = query.ProfileID
	}
	if query.Preferences.MaxResults == 0 {
		query.Preferences.MaxResults = defaultMaxResults
	}
	if query.Constraints.Accelerator.Required && strings.TrimSpace(query.Constraints.Accelerator.Type) == "" {
		query.Constraints.Accelerator.Type = "GPU"
	}
	query.Constraints.Accelerator.Type = strings.TrimSpace(query.Constraints.Accelerator.Type)
	query.Constraints.Accelerator.Model = strings.TrimSpace(query.Constraints.Accelerator.Model)
	return query
}

func validateQuery(query resource.ResourceQuery) error {
	if query.SchemaVersion != resource.SchemaVersion {
		return fmt.Errorf("schema_version must be %q", resource.SchemaVersion)
	}
	if strings.TrimSpace(query.RequestID) == "" {
		return errors.New("request_id or profile_id is required")
	}
	constraints := query.Constraints
	if constraints.CPUCoresMin < 1 {
		return errors.New("constraints.cpu_cores_min must be at least 1")
	}
	if constraints.MemoryMiBMin < 1 {
		return errors.New("constraints.memory_mib_min must be at least 1")
	}
	if !finiteAtLeast(constraints.StorageGiBMin, 0) {
		return errors.New("constraints.storage_gib_min must be finite and non-negative")
	}
	accelerator := constraints.Accelerator
	if accelerator.Required {
		if !strings.EqualFold(strings.TrimSpace(accelerator.Type), "GPU") {
			return errors.New("constraints.accelerator.type must be GPU when an accelerator is required")
		}
		if accelerator.CountMin < 1 {
			return errors.New("constraints.accelerator.count_min must be at least 1 when required")
		}
		if !finiteAtLeast(accelerator.MemoryMiBMinPerDevice, 0) {
			return errors.New("constraints.accelerator.memory_mib_min_per_device must be finite and non-negative")
		}
	} else if accelerator.CountMin != 0 || accelerator.MemoryMiBMinPerDevice != 0 || strings.TrimSpace(accelerator.Type) != "" || strings.TrimSpace(accelerator.Model) != "" {
		return errors.New("constraints.accelerator fields must be empty or zero when required is false")
	}
	if query.Preferences.MaxResults < 1 || query.Preferences.MaxResults > maxResultsLimit {
		return fmt.Errorf("preferences.max_results must be between 1 and %d", maxResultsLimit)
	}
	for _, key := range sortedLabelKeys(query.Selectors.RequiredLabels) {
		value := query.Selectors.RequiredLabels[key]
		if strings.TrimSpace(key) == "" || strings.TrimSpace(value) == "" {
			return errors.New("selectors.required_labels must contain non-empty keys and values")
		}
	}
	selectorValues := []struct {
		name   string
		values []string
	}{
		{"providers", query.Selectors.Providers},
		{"regions", query.Selectors.Regions},
		{"architectures", query.Selectors.Architectures},
		{"resource_types", query.Selectors.ResourceTypes},
	}
	for _, selector := range selectorValues {
		for _, value := range selector.values {
			if strings.TrimSpace(value) == "" {
				return fmt.Errorf("selectors.%s must not contain empty values", selector.name)
			}
		}
	}
	return nil
}

func evaluate(candidate resource.ResourceProfile, query resource.ResourceQuery) []resource.ConditionFailure {
	failures := make([]resource.ConditionFailure, 0)
	constraints := query.Constraints
	if !candidate.Healthy {
		failures = append(failures, failure("RESOURCE_UNHEALTHY", "healthy", true, candidate.Healthy, "resource health check is not up"))
	}
	if candidate.CPU.LogicalCores < constraints.CPUCoresMin {
		failures = append(failures, failure("CPU_CAPACITY_INSUFFICIENT", "cpu.logical_cores", constraints.CPUCoresMin, candidate.CPU.LogicalCores,
			fmt.Sprintf("logical CPU cores %d < required %d", candidate.CPU.LogicalCores, constraints.CPUCoresMin)))
	} else if candidate.CPU.AvailableCores == nil {
		failures = append(failures, failure("CPU_AVAILABILITY_UNKNOWN", "cpu.available_cores", constraints.CPUCoresMin, nil, "CPU availability is missing"))
	} else if *candidate.CPU.AvailableCores < float64(constraints.CPUCoresMin) {
		failures = append(failures, failure("CPU_AVAILABLE_INSUFFICIENT", "cpu.available_cores", constraints.CPUCoresMin, round2(*candidate.CPU.AvailableCores),
			fmt.Sprintf("available CPU cores %.2f < required %d", *candidate.CPU.AvailableCores, constraints.CPUCoresMin)))
	}

	if candidate.Memory.TotalMiB < float64(constraints.MemoryMiBMin) {
		failures = append(failures, failure("MEMORY_CAPACITY_INSUFFICIENT", "memory.total_mib", constraints.MemoryMiBMin, round2(candidate.Memory.TotalMiB),
			fmt.Sprintf("memory capacity %.2f MiB < required %d MiB", candidate.Memory.TotalMiB, constraints.MemoryMiBMin)))
	} else if candidate.Memory.AvailableMiB == nil {
		failures = append(failures, failure("MEMORY_AVAILABILITY_UNKNOWN", "memory.available_mib", constraints.MemoryMiBMin, nil, "memory availability is missing"))
	} else if *candidate.Memory.AvailableMiB < float64(constraints.MemoryMiBMin) {
		failures = append(failures, failure("MEMORY_AVAILABLE_INSUFFICIENT", "memory.available_mib", constraints.MemoryMiBMin, round2(*candidate.Memory.AvailableMiB),
			fmt.Sprintf("available memory %.2f MiB < required %d MiB", *candidate.Memory.AvailableMiB, constraints.MemoryMiBMin)))
	}

	if constraints.StorageGiBMin > 0 {
		if candidate.Storage.TotalGiB < constraints.StorageGiBMin {
			failures = append(failures, failure("STORAGE_CAPACITY_INSUFFICIENT", "storage.total_gib", constraints.StorageGiBMin, round2(candidate.Storage.TotalGiB),
				fmt.Sprintf("storage capacity %.2f GiB < required %.2f GiB", candidate.Storage.TotalGiB, constraints.StorageGiBMin)))
		} else if candidate.Storage.AvailableGiB == nil {
			failures = append(failures, failure("STORAGE_AVAILABILITY_UNKNOWN", "storage.available_gib", constraints.StorageGiBMin, nil, "storage availability is missing"))
		} else if *candidate.Storage.AvailableGiB < constraints.StorageGiBMin {
			failures = append(failures, failure("STORAGE_AVAILABLE_INSUFFICIENT", "storage.available_gib", constraints.StorageGiBMin, round2(*candidate.Storage.AvailableGiB),
				fmt.Sprintf("available storage %.2f GiB < required %.2f GiB", *candidate.Storage.AvailableGiB, constraints.StorageGiBMin)))
		}
	}

	failures = append(failures, selectorFailures(candidate, query.Selectors)...)
	if constraints.Accelerator.Required {
		failures = append(failures, acceleratorFailures(candidate, constraints.Accelerator)...)
	}
	return failures
}

func selectorFailures(candidate resource.ResourceProfile, selectors resource.ResourceSelectors) []resource.ConditionFailure {
	failures := make([]resource.ConditionFailure, 0)
	checks := []struct {
		field   string
		actual  string
		allowed []string
	}{
		{"provider", candidate.Provider, selectors.Providers},
		{"region", candidate.Region, selectors.Regions},
		{"architecture", candidate.Architecture, selectors.Architectures},
		{"resource_type", candidate.ResourceType, selectors.ResourceTypes},
	}
	for _, check := range checks {
		if len(check.allowed) > 0 && !containsFold(check.allowed, check.actual) {
			failures = append(failures, failure("SELECTOR_MISMATCH", check.field, check.allowed, check.actual,
				fmt.Sprintf("%s %q is not in the allowed set", check.field, check.actual)))
		}
	}
	for _, key := range sortedLabelKeys(selectors.RequiredLabels) {
		expected := selectors.RequiredLabels[key]
		actual := candidate.Labels[key]
		if actual != expected {
			failures = append(failures, failure("LABEL_MISMATCH", "labels."+key, expected, actual,
				fmt.Sprintf("label %s=%q; want %q", key, actual, expected)))
		}
	}
	return failures
}

func acceleratorFailures(candidate resource.ResourceProfile, constraint resource.AcceleratorConstraint) []resource.ConditionFailure {
	capable := make([]resource.AcceleratorProfile, 0)
	available := make([]resource.AcceleratorProfile, 0)
	for _, accelerator := range candidate.Accelerators {
		if !strings.EqualFold(accelerator.Type, constraint.Type) || !modelMatches(accelerator.Model, constraint.Model) {
			continue
		}
		if accelerator.MemoryTotalMiB >= constraint.MemoryMiBMinPerDevice {
			capable = append(capable, accelerator)
			if accelerator.Healthy && accelerator.MemoryAvailableMiB != nil && *accelerator.MemoryAvailableMiB >= constraint.MemoryMiBMinPerDevice {
				available = append(available, accelerator)
			}
		}
	}
	if len(capable) < constraint.CountMin {
		return []resource.ConditionFailure{failure("ACCELERATOR_CAPACITY_INSUFFICIENT", "accelerators", map[string]any{
			"type": constraint.Type, "model": constraint.Model, "count": constraint.CountMin, "memory_mib_per_device": constraint.MemoryMiBMinPerDevice,
		}, len(capable), fmt.Sprintf("%d capable accelerator(s) < required %d", len(capable), constraint.CountMin))}
	}
	if len(available) < constraint.CountMin {
		return []resource.ConditionFailure{failure("ACCELERATOR_AVAILABLE_INSUFFICIENT", "accelerators.available", map[string]any{
			"count": constraint.CountMin, "memory_mib_per_device": constraint.MemoryMiBMinPerDevice,
		}, len(available), fmt.Sprintf("%d available accelerator(s) < required %d", len(available), constraint.CountMin))}
	}
	return nil
}

func score(candidate resource.ResourceProfile, query resource.ResourceQuery) resource.RankedResource {
	constraints := query.Constraints
	breakdown := resource.ScoreBreakdown{Health: 100}
	weightedTotal := 1_000.0
	weightTotal := 10.0

	cpu := capacityScore(float64(constraints.CPUCoresMin), float64(candidate.CPU.LogicalCores), *candidate.CPU.AvailableCores)
	breakdown.CPU = &cpu
	weightedTotal += cpu * 30
	weightTotal += 30

	memory := capacityScore(float64(constraints.MemoryMiBMin), candidate.Memory.TotalMiB, *candidate.Memory.AvailableMiB)
	breakdown.Memory = &memory
	weightedTotal += memory * 30
	weightTotal += 30

	if constraints.StorageGiBMin > 0 {
		storage := capacityScore(constraints.StorageGiBMin, candidate.Storage.TotalGiB, *candidate.Storage.AvailableGiB)
		breakdown.Storage = &storage
		weightedTotal += storage * 10
		weightTotal += 10
	}
	if constraints.Accelerator.Required {
		accelerator := acceleratorScore(candidate.Accelerators, constraints.Accelerator)
		breakdown.Accelerator = &accelerator
		weightedTotal += accelerator * 30
		weightTotal += 30
	}
	breakdown.Total = round2(weightedTotal / weightTotal)
	return resource.RankedResource{
		Score:          breakdown.Total,
		Resource:       candidate,
		ScoreBreakdown: breakdown,
		Reasons:        reasons(candidate, query),
	}
}

// capacityScore combines remaining headroom with the current availability
// ratio. Both values are source snapshot facts, not deployment guarantees.
func capacityScore(required, total, available float64) float64 {
	if total <= 0 || available <= 0 {
		return 0
	}
	availabilityRatio := clamp(available/total, 0, 1)
	postAllocationHeadroom := clamp((available-required)/available, 0, 1)
	return round2(100 * (0.65*availabilityRatio + 0.35*postAllocationHeadroom))
}

func acceleratorScore(accelerators []resource.AcceleratorProfile, constraint resource.AcceleratorConstraint) float64 {
	scores := make([]float64, 0)
	for _, accelerator := range accelerators {
		if !accelerator.Healthy || accelerator.MemoryAvailableMiB == nil ||
			!strings.EqualFold(accelerator.Type, constraint.Type) || !modelMatches(accelerator.Model, constraint.Model) ||
			accelerator.MemoryTotalMiB < constraint.MemoryMiBMinPerDevice || *accelerator.MemoryAvailableMiB < constraint.MemoryMiBMinPerDevice {
			continue
		}
		value := capacityScore(constraint.MemoryMiBMinPerDevice, accelerator.MemoryTotalMiB, *accelerator.MemoryAvailableMiB)
		if accelerator.UtilizationPercent != nil {
			idle := 100 - clamp(*accelerator.UtilizationPercent, 0, 100)
			value = 0.8*value + 0.2*idle
		}
		scores = append(scores, value)
	}
	sort.Sort(sort.Reverse(sort.Float64Slice(scores)))
	if len(scores) < constraint.CountMin {
		return 0
	}
	total := 0.0
	for _, value := range scores[:constraint.CountMin] {
		total += value
	}
	return round2(total / float64(constraint.CountMin))
}

func reasons(candidate resource.ResourceProfile, query resource.ResourceQuery) []resource.RecommendationReason {
	result := []resource.RecommendationReason{
		{Code: "RESOURCE_HEALTHY", Message: "Resource health check is up."},
		{Code: "CPU_AVAILABLE", Message: fmt.Sprintf("%.2f CPU cores are available for %d required cores.", *candidate.CPU.AvailableCores, query.Constraints.CPUCoresMin)},
		{Code: "MEMORY_AVAILABLE", Message: fmt.Sprintf("%.2f MiB memory is available for %d MiB required.", *candidate.Memory.AvailableMiB, query.Constraints.MemoryMiBMin)},
	}
	if query.Constraints.StorageGiBMin > 0 {
		result = append(result, resource.RecommendationReason{
			Code: "STORAGE_AVAILABLE", Message: fmt.Sprintf("%.2f GiB storage is available for %.2f GiB required.", *candidate.Storage.AvailableGiB, query.Constraints.StorageGiBMin),
		})
	}
	if query.Constraints.Accelerator.Required {
		result = append(result, resource.RecommendationReason{
			Code: "ACCELERATOR_AVAILABLE",
			Message: fmt.Sprintf("At least %d matching GPU(s) have %.2f MiB or more free VRAM per device.",
				query.Constraints.Accelerator.CountMin, query.Constraints.Accelerator.MemoryMiBMinPerDevice),
		})
	}
	return result
}

func modelMatches(actual, required string) bool {
	required = strings.TrimSpace(required)
	return required == "" || strings.EqualFold(strings.TrimSpace(actual), required)
}

func containsFold(values []string, actual string) bool {
	for _, value := range values {
		if strings.EqualFold(strings.TrimSpace(value), strings.TrimSpace(actual)) {
			return true
		}
	}
	return false
}

func sortedLabelKeys(labels map[string]string) []string {
	keys := make([]string, 0, len(labels))
	for key := range labels {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func failure(code, field string, required, actual any, reason string) resource.ConditionFailure {
	return resource.ConditionFailure{Code: code, Field: field, Required: required, Actual: actual, Reason: reason}
}

func finiteAtLeast(value, minimum float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= minimum
}

func clamp(value, minimum, maximum float64) float64 {
	return math.Max(minimum, math.Min(maximum, value))
}

func round2(value float64) float64 { return math.Round(value*100) / 100 }

func appendUnique(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}
