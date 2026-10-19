package resource

import (
	"errors"
	"fmt"
	"strings"
)

// ProfileRecommendationRequest is the canonical resource-ops input. It keeps
// the ApplicationProfile produced by llmop intact at the service boundary and
// projects only infrastructure lookup conditions into ResourceQuery.
type ProfileRecommendationRequest struct {
	SchemaVersion      string             `json:"schema_version,omitempty"`
	RequestID          string             `json:"request_id,omitempty"`
	ApplicationProfile ApplicationProfile `json:"application_profile"`
	Selectors          ResourceSelectors  `json:"selectors,omitempty"`
	Preferences        QueryPreferences   `json:"preferences,omitempty"`
}

type ApplicationProfile struct {
	ProfileID    string                  `json:"profile_id"`
	AppID        string                  `json:"app_id"`
	AppVersion   string                  `json:"app_version"`
	Artifact     *Artifact               `json:"artifact,omitempty"`
	Workload     WorkloadProfile         `json:"workload"`
	Requirements ApplicationRequirements `json:"requirements"`
	Analysis     AnalysisSummary         `json:"analysis"`
}

type Artifact struct {
	Type       string   `json:"type"`
	URI        string   `json:"uri"`
	Entrypoint []string `json:"entrypoint,omitempty"`
}

type WorkloadProfile struct {
	TaskType       string  `json:"task_type"`
	RequestPattern string  `json:"request_pattern"`
	ExpectedRPS    float64 `json:"expected_rps,omitempty"`
}

type ApplicationRequirements struct {
	Compute     ComputeRequirements     `json:"compute"`
	Accelerator AcceleratorRequirements `json:"accelerator"`
	Deployment  DeploymentRequirements  `json:"deployment"`
	SLO         SLORequirements         `json:"slo"`
	Cost        CostRequirements        `json:"cost"`
}

type ComputeRequirements struct {
	CPUCoresMin   int `json:"cpu_cores_min"`
	MemoryMiBMin  int `json:"memory_mib_min"`
	StorageGiBMin int `json:"storage_gib_min"`
}

type AcceleratorRequirements struct {
	Required              bool   `json:"required"`
	Type                  string `json:"type,omitempty"`
	CountMin              int    `json:"count_min"`
	MemoryMiBMinPerDevice int    `json:"memory_mib_min_per_device"`
}

type DeploymentRequirements struct {
	ReplicasMin int    `json:"replicas_min"`
	ReplicasMax int    `json:"replicas_max"`
	Isolation   string `json:"isolation"`
}

type SLORequirements struct {
	LatencyP95MSMax  float64 `json:"latency_p95_ms_max,omitempty"`
	ThroughputRPSMin float64 `json:"throughput_rps_min,omitempty"`
}

type CostRequirements struct {
	Currency       string  `json:"currency,omitempty"`
	CostPerHourMax float64 `json:"cost_per_hour_max,omitempty"`
}

type AnalysisSummary struct {
	Confidence    float64  `json:"confidence"`
	Assumptions   []string `json:"assumptions,omitempty"`
	MissingFields []string `json:"missing_fields,omitempty"`
	Warnings      []string `json:"warnings,omitempty"`
}

// QueryFromApplicationProfile performs the deterministic llmop -> resource-ops
// projection. No ranking, deployment, or manifest decision is made here.
func QueryFromApplicationProfile(request ProfileRecommendationRequest) (ResourceQuery, error) {
	profile := request.ApplicationProfile
	if request.SchemaVersion == "" {
		request.SchemaVersion = SchemaVersion
	}
	if request.SchemaVersion != SchemaVersion {
		return ResourceQuery{}, fmt.Errorf("schema_version must be %q", SchemaVersion)
	}
	if strings.TrimSpace(profile.ProfileID) == "" {
		return ResourceQuery{}, errors.New("application_profile.profile_id is required")
	}
	if strings.TrimSpace(profile.AppID) == "" || strings.TrimSpace(profile.AppVersion) == "" {
		return ResourceQuery{}, errors.New("application_profile app_id and app_version are required")
	}
	if profile.Requirements.Deployment.ReplicasMin < 1 {
		return ResourceQuery{}, errors.New("application_profile.requirements.deployment.replicas_min must be at least 1")
	}
	if profile.Requirements.Deployment.ReplicasMax < profile.Requirements.Deployment.ReplicasMin {
		return ResourceQuery{}, errors.New("application_profile deployment replica range is invalid")
	}

	requestID := strings.TrimSpace(request.RequestID)
	if requestID == "" {
		requestID = profile.ProfileID
	}
	maxResults := request.Preferences.MaxResults
	if maxResults == 0 {
		maxResults = 3
	}
	if maxResults < profile.Requirements.Deployment.ReplicasMin {
		maxResults = profile.Requirements.Deployment.ReplicasMin
	}
	accelerator := profile.Requirements.Accelerator
	return ResourceQuery{
		SchemaVersion: request.SchemaVersion,
		RequestID:     requestID,
		ProfileID:     profile.ProfileID,
		Constraints: QueryConstraints{
			CPUCoresMin:   profile.Requirements.Compute.CPUCoresMin,
			MemoryMiBMin:  int64(profile.Requirements.Compute.MemoryMiBMin),
			StorageGiBMin: float64(profile.Requirements.Compute.StorageGiBMin),
			Accelerator: AcceleratorConstraint{
				Required:              accelerator.Required,
				Type:                  accelerator.Type,
				CountMin:              accelerator.CountMin,
				MemoryMiBMinPerDevice: float64(accelerator.MemoryMiBMinPerDevice),
			},
		},
		Selectors: request.Selectors,
		Preferences: QueryPreferences{
			MaxResults: maxResults,
		},
	}, nil
}
