// Package applicationprofile defines the only domain output produced by LLM_Op.
package applicationprofile

const (
	InputTypeNaturalLanguage = "natural_language"
	InputTypeStructured      = "structured"

	AnalysisModeLLM        = "llm"
	AnalysisModeLocalRule  = "local_rule"
	AnalysisModeStructured = "structured"
)

// GenerateRequest contains untrusted natural language and optional structured
// application metadata supplied by the caller. The metadata is kept outside
// model authority, but this contract does not authenticate its provenance. The
// request intentionally has no target, provider, VM, deployment, or execution
// fields.
type GenerateRequest struct {
	InputType       string              `json:"input_type,omitempty" enums:"natural_language,structured"`
	Request         string              `json:"request,omitempty" validate:"max=8000"`
	AppSpec         *StructuredAppSpec  `json:"app_spec,omitempty"`
	CandidateID     string              `json:"candidate_id,omitempty"`
	SelectionPolicy string              `json:"selection_policy,omitempty" example:"quality_first"`
	Application     ApplicationMetadata `json:"application,omitempty"`
}

// StructuredAppSpec is deterministically converted into an ApplicationProfile
// without sending its fields to an LLM.
type StructuredAppSpec struct {
	AppID                         string    `json:"app_id" validate:"required"`
	AppVersion                    string    `json:"app_version" validate:"required"`
	Artifact                      *Artifact `json:"artifact,omitempty"`
	WorkloadType                  string    `json:"workload_type" validate:"required"`
	RequestPattern                string    `json:"request_pattern,omitempty" enums:"ONLINE,BATCH,STREAMING"`
	ExpectedRPS                   float64   `json:"expected_rps,omitempty"`
	CPUCores                      int       `json:"cpu_cores" validate:"required"`
	MemoryMiB                     int       `json:"memory_mib" validate:"required"`
	StorageGiB                    int       `json:"storage_gib" validate:"required"`
	AcceleratorType               string    `json:"accelerator_type,omitempty"`
	AcceleratorCount              int       `json:"accelerator_count,omitempty"`
	AcceleratorMemoryMiBPerDevice int       `json:"accelerator_memory_mib_per_device,omitempty"`
	ReplicasMin                   int       `json:"replicas_min,omitempty"`
	ReplicasMax                   int       `json:"replicas_max,omitempty"`
	Isolation                     string    `json:"isolation,omitempty"`
	LatencyP95MSMax               float64   `json:"latency_p95_ms_max,omitempty"`
	ThroughputRPSMin              float64   `json:"throughput_rps_min,omitempty"`
	CostCurrency                  string    `json:"cost_currency,omitempty"`
	CostPerHourMax                float64   `json:"cost_per_hour_max,omitempty"`
}

type ApplicationMetadata struct {
	AppID      string    `json:"app_id,omitempty"`
	AppVersion string    `json:"app_version,omitempty"`
	Artifact   *Artifact `json:"artifact,omitempty"`
}

type GenerateResult struct {
	ApplicationProfile ApplicationProfile `json:"application_profile" validate:"required"`
	Analysis           AnalysisEvidence   `json:"analysis" validate:"required"`
}

type AnalysisEvidence struct {
	Mode            string `json:"mode" validate:"required"`
	CandidateID     string `json:"candidate_id,omitempty"`
	Provider        string `json:"provider,omitempty"`
	ActualModel     string `json:"actual_model,omitempty"`
	LatencyMS       int64  `json:"latency_ms,omitempty"`
	Attempts        int    `json:"attempts,omitempty"`
	SelectionPolicy string `json:"selection_policy,omitempty"`
	FallbackReason  string `json:"fallback_reason,omitempty"`
}

type Artifact struct {
	Type       string   `json:"type" validate:"required"`
	URI        string   `json:"uri" validate:"required"`
	Entrypoint []string `json:"entrypoint,omitempty"`
}

type ApplicationProfile struct {
	ProfileID    string                  `json:"profile_id" validate:"required"`
	AppID        string                  `json:"app_id" validate:"required"`
	AppVersion   string                  `json:"app_version" validate:"required"`
	Artifact     *Artifact               `json:"artifact,omitempty"`
	Workload     WorkloadProfile         `json:"workload" validate:"required"`
	Requirements ApplicationRequirements `json:"requirements" validate:"required"`
	Analysis     AnalysisSummary         `json:"analysis" validate:"required"`
}

type WorkloadProfile struct {
	TaskType       string  `json:"task_type" validate:"required"`
	RequestPattern string  `json:"request_pattern" validate:"required"`
	ExpectedRPS    float64 `json:"expected_rps,omitempty"`
}

type ApplicationRequirements struct {
	Compute     ComputeRequirements     `json:"compute" validate:"required"`
	Accelerator AcceleratorRequirements `json:"accelerator" validate:"required"`
	Deployment  DeploymentRequirements  `json:"deployment" validate:"required"`
	SLO         SLORequirements         `json:"slo" validate:"required"`
	Cost        CostRequirements        `json:"cost" validate:"required"`
}

type ComputeRequirements struct {
	CPUCoresMin   int `json:"cpu_cores_min" validate:"required"`
	MemoryMiBMin  int `json:"memory_mib_min" validate:"required"`
	StorageGiBMin int `json:"storage_gib_min" validate:"required"`
}

type AcceleratorRequirements struct {
	Required              bool   `json:"required" validate:"required"`
	Type                  string `json:"type,omitempty"`
	CountMin              int    `json:"count_min" validate:"required"`
	MemoryMiBMinPerDevice int    `json:"memory_mib_min_per_device" validate:"required"`
}

type DeploymentRequirements struct {
	ReplicasMin int    `json:"replicas_min" validate:"required"`
	ReplicasMax int    `json:"replicas_max" validate:"required"`
	Isolation   string `json:"isolation" validate:"required"`
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
	Confidence    float64  `json:"confidence" validate:"required"`
	Assumptions   []string `json:"assumptions,omitempty"`
	MissingFields []string `json:"missing_fields,omitempty"`
	Warnings      []string `json:"warnings,omitempty"`
}
