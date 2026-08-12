package llmop

import (
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

const (
	APIVersion = "ai-ops.llm-operation/v1alpha1"

	ModePrepareOnly = "prepare_only"

	StatusHandoffReady         = "HANDOFF_READY"
	StatusSafeguardApproved    = "SAFEGUARD_APPROVED"
	StatusClarificationNeeded = "CLARIFICATION_REQUIRED"
	StatusRequestRejected      = "REQUEST_REJECTED"
	StatusManifestRejected     = "MANIFEST_REJECTED"
	StatusModelUnavailable     = "MODEL_UNAVAILABLE"
	StatusConfigurationError   = "CONFIGURATION_ERROR"
)

type Request struct {
	APIVersion       string           `json:"api_version" validate:"required"`
	RequestID        string           `json:"request_id" validate:"required"`
	CorrelationID    string           `json:"correlation_id" validate:"required"`
	TraceID          string           `json:"trace_id,omitempty"`
	CandidateID      string           `json:"candidate_id" validate:"required"`
	RequestedBy      string           `json:"requested_by" validate:"required"`
	Application      Application      `json:"application" validate:"required"`
	OperationContext OperationContext `json:"operation_context,omitempty"`
	Policy           RequestPolicy    `json:"policy" validate:"required"`
}

type Application struct {
	AppVersionID        string               `json:"app_version_id" validate:"required"`
	DeploymentID        string               `json:"deployment_id,omitempty"`
	UserRequest         string               `json:"user_request" validate:"required,max=8000"`
	TargetProfileID     string               `json:"target_profile_id,omitempty"`
	PlanningConstraints *PlanningConstraints `json:"planning_constraints,omitempty"`
	Parameters          map[string]any       `json:"parameters,omitempty"`
}

// PlanningConstraints is trusted integration metadata projected from an
// existing ApplicationProfile and feasible ResourceRecommendation. Profile
// minima and an optional exact upstream resource recommendation are kept
// separate; source identifiers are correlation evidence, never target IDs.
type PlanningConstraints struct {
	SourceProfileID        string                `json:"source_profile_id"`
	SourceRecommendationID string                `json:"source_recommendation_id"`
	RecommendationFeasible bool                  `json:"recommendation_feasible"`
	CPUCoresMin            uint64                `json:"cpu_cores_min"`
	MemoryMiBMin           uint64                `json:"memory_mib_min"`
	GPUCountMin            uint64                `json:"gpu_count_min"`
	StorageGiBMin          uint64                `json:"storage_gib_min"`
	Accelerator            string                `json:"accelerator,omitempty"`
	RecommendedResources   *RecommendedResources `json:"recommended_resources,omitempty"`
}

// RecommendedResources is the exact single-node resource shape selected by an
// upstream ResourceRecommendation. It is not an LLM/model candidate and does
// not select an AppDeploy target.
type RecommendedResources struct {
	CPUCores    uint64 `json:"cpu_cores"`
	MemoryMiB   uint64 `json:"memory_mib"`
	GPUCount    uint64 `json:"gpu_count"`
	StorageGiB  uint64 `json:"storage_gib"`
	Accelerator string `json:"accelerator"`
}

type OperationContext struct {
	ResourceSnapshot  *ResourceSnapshot     `json:"resource_snapshot,omitempty"`
	MonitoringSummary *MonitoringObservation `json:"monitoring_summary,omitempty"`
	DeploymentLogs    *LogObservation       `json:"deployment_logs,omitempty"`
	MetricsSummary    *MetricsObservation   `json:"metrics_summary,omitempty"`
}

type ResourceSnapshot struct {
	Source     string                            `json:"source"`
	ObservedAt time.Time                         `json:"observed_at"`
	Targets    []appdeploy.RuntimeHealthSnapshot `json:"targets,omitempty"`
}

type MonitoringObservation struct {
	Source     string                              `json:"source"`
	ObservedAt time.Time                           `json:"observed_at"`
	Summary    appdeploy.MonitoringSummaryResponse `json:"summary"`
}

type LogObservation struct {
	Source     string                    `json:"source"`
	ObservedAt time.Time                 `json:"observed_at"`
	Items      []appdeploy.DeploymentLog `json:"items,omitempty"`
}

type MetricsObservation struct {
	Source        string    `json:"source"`
	ObservedAt    time.Time `json:"observed_at"`
	DeploymentID  string    `json:"deployment_id,omitempty"`
	LatencyP95MS  float64   `json:"latency_p95_ms,omitempty"`
	ThroughputRPS float64   `json:"throughput_rps,omitempty"`
	ErrorRate     float64   `json:"error_rate,omitempty"`
	SampleCount   int       `json:"sample_count,omitempty"`
}

type RequestPolicy struct {
	Mode              string `json:"mode" validate:"required"`
	ApprovalReference string `json:"approval_reference,omitempty"`
}

type Proposal struct {
	Action      string                          `json:"action"`
	ReasonCode  string                          `json:"reason_code"`
	Reason      string                          `json:"reason"`
	Confidence  *float64                        `json:"confidence"`
	Accelerator string                          `json:"accelerator,omitempty"`
	Resources   *appdeploy.ResourceRequirements `json:"resources,omitempty"`
	Assumptions []string                        `json:"assumptions,omitempty"`
}

type NormalizedContext struct {
	ResourceSnapshot  *ResourceSnapshot      `json:"resource_snapshot,omitempty"`
	MonitoringSummary *MonitoringObservation `json:"monitoring_summary,omitempty"`
	DeploymentLogs    *LogObservation        `json:"deployment_logs,omitempty"`
	MetricsSummary    *MetricsObservation    `json:"metrics_summary,omitempty"`
	ObservationStatus string                 `json:"observation_status"`
	RedactedValues    int                    `json:"redacted_values"`
	DroppedLogs       int                    `json:"dropped_logs"`
	StaleSources      []string               `json:"stale_sources,omitempty"`
}

type Decision struct {
	Action            string    `json:"action"`
	ReasonCode        string    `json:"reason_code,omitempty"`
	Reason            string    `json:"reason"`
	Confidence        *float64  `json:"confidence,omitempty"`
	Assumptions       []string  `json:"assumptions,omitempty"`
	ObservationStatus string    `json:"observation_status"`
}

type ManifestGuard struct {
	Valid  bool   `json:"valid"`
	Status string `json:"status"`
	Reason string `json:"reason"`
}

type Safeguard struct {
	Request  plannerguard.Decision `json:"request"`
	Manifest ManifestGuard         `json:"manifest"`
}

type ModelEvidence struct {
	Provider    string `json:"provider"`
	CandidateID string `json:"candidate_id"`
	ActualModel string `json:"actual_model"`
	LatencyMS   int64  `json:"latency_ms"`
}

type SafeguardReviewEvidence struct {
	Provider    string   `json:"provider"`
	CandidateID string   `json:"candidate_id"`
	ActualModel string   `json:"actual_model"`
	LatencyMS   int64    `json:"latency_ms"`
	Decision    string   `json:"decision,omitempty"`
	ReasonCode  string   `json:"reason_code,omitempty"`
	Confidence  *float64 `json:"confidence,omitempty"`
}

type InputSummary struct {
	ObservationStatus       string   `json:"observation_status"`
	ResourceSnapshotIncluded bool     `json:"resource_snapshot_included"`
	MonitoringIncluded      bool     `json:"monitoring_included"`
	LogsIncluded            bool     `json:"logs_included"`
	MetricsIncluded         bool     `json:"metrics_included"`
	RedactedValues          int      `json:"redacted_values"`
	DroppedLogs             int      `json:"dropped_logs"`
	StaleSources            []string `json:"stale_sources,omitempty"`
}

type Evidence struct {
	SafeguardReview *SafeguardReviewEvidence `json:"safeguard_review,omitempty"`
	Model           ModelEvidence            `json:"model"`
	Input           InputSummary             `json:"input"`
}

type Handoff struct {
	SubmissionMode  string                              `json:"submission_mode"`
	NextEndpoint    string                              `json:"next_endpoint,omitempty"`
	PreparedRequest *appdeploy.DeploymentCreateRequest `json:"prepared_request,omitempty"`
}

type Result struct {
	APIVersion    string                        `json:"api_version"`
	RequestID     string                        `json:"request_id"`
	CorrelationID string                        `json:"correlation_id"`
	TraceID       string                        `json:"trace_id,omitempty"`
	Status        string                        `json:"status"`
	Decision      Decision                      `json:"decision"`
	Safeguard     Safeguard                     `json:"safeguard"`
	Manifest      *appdeploy.DeploymentManifest `json:"manifest,omitempty"`
	Evidence      Evidence                      `json:"evidence"`
	Handoff       Handoff                       `json:"handoff"`
}

type StageError struct {
	Status string
	Cause  error
}

func (err *StageError) Error() string {
	if err == nil {
		return ""
	}
	return err.Status
}

func (err *StageError) Unwrap() error {
	if err == nil {
		return nil
	}
	return err.Cause
}

func NewResult(request Request) Result {
	return Result{
		APIVersion:    APIVersion,
		RequestID:     safeResultIdentifier(request.RequestID),
		CorrelationID: safeResultIdentifier(request.CorrelationID),
		TraceID:       safeResultIdentifier(request.TraceID),
		Status:        StatusModelUnavailable,
		Decision: Decision{
			Action:            "none",
			Reason:            "LLM operation has not completed",
			ObservationStatus: "not_evaluated",
		},
		Safeguard: Safeguard{
			Manifest: ManifestGuard{
				Status: "not_evaluated",
				Reason: "manifest has not been generated",
			},
		},
		Evidence: Evidence{
			Input: InputSummary{ObservationStatus: "not_evaluated"},
		},
		Handoff: Handoff{
			SubmissionMode: "not_submitted",
		},
	}
}

func safeResultIdentifier(value string) string {
	// Request/correlation/trace identifiers are strong identifiers in the
	// request contract. Do not reflect even syntactically safe short values in
	// an error Result before the full request guard has run.
	if !boundedRequestIdentifier(value) || len(value) < 8 {
		return ""
	}
	return value
}
