// Package resource defines the source-neutral contracts used by resource-ops.
// External systems are adapted into RawFacts, normalized into ResourceProfiles,
// and then evaluated without depending on the source payload format.
package resource

import "time"

const SchemaVersion = "1.0"

const (
	FactNodeUp                = "node_up"
	FactAcceleratorExporterUp = "accelerator_exporter_up"
	FactCPUCount              = "cpu_logical_count"
	FactCPUUtilizationPercent = "cpu_utilization_percent"
	FactMemoryTotalBytes      = "memory_total_bytes"
	FactMemoryAvailableBytes  = "memory_available_bytes"
	FactStorageTotalBytes     = "storage_total_bytes"
	FactStorageAvailableBytes = "storage_available_bytes"
	FactNodeInfo              = "node_info"
	FactOSInfo                = "os_info"
	FactGPUUtilizationPercent = "gpu_utilization_percent"
	FactGPUMemoryFreeMiB      = "gpu_memory_free_mib"
	FactGPUMemoryUsedMiB      = "gpu_memory_used_mib"
	FactGPUMemoryReservedMiB  = "gpu_memory_reserved_mib"
)

// ResourceQuery is the stable input consumed by resource-ops. It intentionally
// contains lookup conditions only, not an ApplicationProfile, deployment state,
// manifest, or scaling action.
type ResourceQuery struct {
	SchemaVersion string            `json:"schema_version,omitempty"`
	RequestID     string            `json:"request_id,omitempty"`
	ProfileID     string            `json:"profile_id,omitempty"`
	Constraints   QueryConstraints  `json:"constraints"`
	Selectors     ResourceSelectors `json:"selectors,omitempty"`
	Preferences   QueryPreferences  `json:"preferences,omitempty"`
}

type QueryConstraints struct {
	CPUCoresMin   int                   `json:"cpu_cores_min"`
	MemoryMiBMin  int64                 `json:"memory_mib_min"`
	StorageGiBMin float64               `json:"storage_gib_min"`
	Accelerator   AcceleratorConstraint `json:"accelerator"`
}

type AcceleratorConstraint struct {
	Required              bool    `json:"required"`
	Type                  string  `json:"type,omitempty"`
	Model                 string  `json:"model,omitempty"`
	CountMin              int     `json:"count_min"`
	MemoryMiBMinPerDevice float64 `json:"memory_mib_min_per_device"`
}

// ResourceSelectors are optional source-neutral metadata filters. A future
// adapter may populate provider/region/type without changing recommendation
// logic or the public query contract.
type ResourceSelectors struct {
	Providers      []string          `json:"providers,omitempty"`
	Regions        []string          `json:"regions,omitempty"`
	Architectures  []string          `json:"architectures,omitempty"`
	ResourceTypes  []string          `json:"resource_types,omitempty"`
	RequiredLabels map[string]string `json:"required_labels,omitempty"`
}

type QueryPreferences struct {
	MaxResults int `json:"max_results,omitempty"`
}

// RawFact is the narrow adapter boundary. A Prometheus metric, inventory row,
// or future resource API field can be represented as a fact without leaking its
// original wire format into normalization and recommendation packages.
type RawFact struct {
	ResourceID string
	DeviceID   string
	Kind       string
	Value      float64
	Unit       string
	Labels     map[string]string
	ObservedAt time.Time
}

type RawSnapshot struct {
	Source     string
	ObservedAt time.Time
	Facts      []RawFact
	Warnings   []string
}

type ResourceSnapshot struct {
	SchemaVersion string            `json:"schema_version"`
	Source        string            `json:"source"`
	ObservedAt    time.Time         `json:"observed_at"`
	Resources     []ResourceProfile `json:"resources"`
	Warnings      []string          `json:"warnings"`
}

// ResourceProfile is the normalized infrastructure profile produced by
// resource-ops before filtering and ranking.
type ResourceProfile struct {
	ResourceID   string               `json:"resource_id"`
	Hostname     string               `json:"hostname,omitempty"`
	Address      string               `json:"address,omitempty"`
	Provider     string               `json:"provider,omitempty"`
	Region       string               `json:"region,omitempty"`
	ResourceType string               `json:"resource_type,omitempty"`
	Architecture string               `json:"architecture,omitempty"`
	OperatingSys string               `json:"operating_system,omitempty"`
	Healthy      bool                 `json:"healthy"`
	ObservedAt   time.Time            `json:"observed_at"`
	CPU          CPUProfile           `json:"cpu"`
	Memory       MemoryProfile        `json:"memory"`
	Storage      StorageProfile       `json:"storage"`
	Accelerators []AcceleratorProfile `json:"accelerators"`
	Labels       map[string]string    `json:"labels,omitempty"`
	Warnings     []string             `json:"warnings"`
}

type CPUProfile struct {
	LogicalCores       int      `json:"logical_cores"`
	AvailableCores     *float64 `json:"available_cores,omitempty"`
	UtilizationPercent *float64 `json:"utilization_percent,omitempty"`
}

type MemoryProfile struct {
	TotalMiB     float64  `json:"total_mib"`
	AvailableMiB *float64 `json:"available_mib,omitempty"`
}

type StorageProfile struct {
	TotalGiB     float64  `json:"total_gib"`
	AvailableGiB *float64 `json:"available_gib,omitempty"`
}

type AcceleratorProfile struct {
	DeviceID           string   `json:"device_id"`
	Index              string   `json:"index,omitempty"`
	Type               string   `json:"type"`
	Model              string   `json:"model,omitempty"`
	MemoryTotalMiB     float64  `json:"memory_total_mib"`
	MemoryAvailableMiB *float64 `json:"memory_available_mib,omitempty"`
	UtilizationPercent *float64 `json:"utilization_percent,omitempty"`
	Healthy            bool     `json:"healthy"`
}

type ConditionFailure struct {
	Code     string `json:"code"`
	Field    string `json:"field"`
	Required any    `json:"required,omitempty"`
	Actual   any    `json:"actual,omitempty"`
	Reason   string `json:"reason"`
}

type ExcludedResource struct {
	ResourceID string             `json:"resource_id"`
	Failures   []ConditionFailure `json:"failures"`
}

type ScoreBreakdown struct {
	CPU         *float64 `json:"cpu,omitempty"`
	Memory      *float64 `json:"memory,omitempty"`
	Storage     *float64 `json:"storage,omitempty"`
	Accelerator *float64 `json:"accelerator,omitempty"`
	Health      float64  `json:"health"`
	Total       float64  `json:"total"`
}

type RecommendationReason struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type RankedResource struct {
	Rank           int                    `json:"rank"`
	Score          float64                `json:"score"`
	Resource       ResourceProfile        `json:"resource"`
	ScoreBreakdown ScoreBreakdown         `json:"score_breakdown"`
	Reasons        []RecommendationReason `json:"reasons"`
}

type RecommendationSummary struct {
	Discovered int `json:"discovered"`
	Feasible   int `json:"feasible"`
	Excluded   int `json:"excluded"`
	Returned   int `json:"returned"`
}

// ResourceRecommendation is advisory infrastructure information only. It does
// not contain deployment, scaling, manifest, or scheduler decisions.
type ResourceRecommendation struct {
	SchemaVersion     string                `json:"schema_version"`
	RequestID         string                `json:"request_id"`
	ProfileID         string                `json:"profile_id,omitempty"`
	Source            string                `json:"source"`
	ObservedAt        time.Time             `json:"observed_at"`
	Query             ResourceQuery         `json:"query"`
	Recommendations   []RankedResource      `json:"recommendations"`
	ExcludedResources []ExcludedResource    `json:"excluded_resources"`
	Summary           RecommendationSummary `json:"summary"`
	Warnings          []string              `json:"warnings"`
}
