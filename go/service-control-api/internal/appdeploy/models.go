package appdeploy

import (
	"encoding/json"
	"fmt"
	"io"
	"time"
)

const (
	ManifestSchemaVersion = "deployment.khu.ai/v1alpha1"
	ManifestKind          = "DeploymentManifest"
)

type DeploymentManifest struct {
	SchemaVersion string             `json:"schema_version"`
	Kind          string             `json:"kind"`
	Metadata      DeploymentMetadata `json:"metadata,omitempty"`
	Spec          DeploymentSpec     `json:"spec"`
}

type DeploymentMetadata struct {
	Name string `json:"name,omitempty"`
}

type DeploymentSpec struct {
	AppVersionID    string               `json:"app_version_id"`
	TargetProfileID string               `json:"target_profile_id,omitempty"`
	Accelerator     string               `json:"accelerator"`
	Resources       ResourceRequirements `json:"resources"`
	RequestedBy     string               `json:"requested_by,omitempty"`
	Parameters      map[string]any       `json:"parameters,omitempty"`
}

type ResourceRequirements struct {
	CPU     string `json:"cpu"`
	Memory  string `json:"memory"`
	GPU     string `json:"gpu"`
	Storage string `json:"storage"`
}

type DeploymentCreateRequest struct {
	Manifest DeploymentManifest `json:"manifest"`
}

type PackageUpload struct {
	Source          io.Reader
	Filename        string
	PackageType     string
	AppName         string
	AppVersion      string
	Entrypoint      string
	RuntimeType     string
	ServicePort     int
	HealthcheckPath string
}

type PackageBuildResponse struct {
	RequestID   string          `json:"request_id,omitempty"`
	PackageType string          `json:"package_type"`
	ArtifactURI string          `json:"artifact_uri"`
	ArchiveName string          `json:"archive_name"`
	SizeBytes   int64           `json:"size_bytes"`
	Checksum    string          `json:"checksum"`
	AppSpec     json.RawMessage `json:"app_spec"`
}

type AppRegistrationResponse struct {
	RequestID    string          `json:"request_id,omitempty"`
	AppID        string          `json:"app_id"`
	AppVersionID string          `json:"app_version_id"`
	Name         string          `json:"name"`
	Version      string          `json:"version"`
	AppSpec      json.RawMessage `json:"app_spec"`
}

type ResourceAllocation struct {
	CPUCores     float64 `json:"cpu_cores,omitempty"`
	MemoryBytes  int64   `json:"memory_bytes,omitempty"`
	GPUCount     int     `json:"gpu_count,omitempty"`
	StorageBytes int64   `json:"storage_bytes,omitempty"`
}

type PlacementDecision struct {
	TargetVMID         string             `json:"target_vm_id"`
	TargetProfileID    string             `json:"target_profile_id,omitempty"`
	Allocation         ResourceAllocation `json:"allocation"`
	Source             string             `json:"source"`
	Score              float64            `json:"score"`
	Reason             string             `json:"reason"`
	SelectedAt         time.Time          `json:"selected_at"`
	ExternalDecisionID string             `json:"external_decision_id,omitempty"`
}

type DeploymentResponse struct {
	RequestID       string             `json:"request_id,omitempty"`
	DeploymentID    string             `json:"deployment_id"`
	AppID           string             `json:"app_id,omitempty"`
	AppVersionID    string             `json:"app_version_id,omitempty"`
	TargetProfileID string             `json:"target_profile_id,omitempty"`
	Placement       *PlacementDecision `json:"placement,omitempty"`
	RuntimeID       string             `json:"runtime_id,omitempty"`
	Status          string             `json:"status"`
	CreatedAt       string             `json:"created_at,omitempty"`
	UpdatedAt       string             `json:"updated_at,omitempty"`
	Manifest        DeploymentManifest `json:"manifest,omitempty"`
}

type DeploymentListResponse struct {
	RequestID   string               `json:"request_id,omitempty"`
	Items       []DeploymentResponse `json:"items,omitempty"`
	Deployments []DeploymentResponse `json:"deployments,omitempty"`
}

type InferenceMetricRecord struct {
	RequestID     string         `json:"request_id,omitempty"`
	MetricID      string         `json:"metric_id"`
	DeploymentID  string         `json:"deployment_id"`
	Timestamp     time.Time      `json:"timestamp"`
	LatencyMS     float64        `json:"latency_ms,omitempty"`
	ThroughputRPS float64        `json:"throughput_rps,omitempty"`
	QualityScore  float64        `json:"quality_score,omitempty"`
	RequestCount  int            `json:"request_count,omitempty"`
	ErrorCount    int            `json:"error_count,omitempty"`
	Metadata      map[string]any `json:"metadata,omitempty"`
}

type DeploymentMetricsResponse struct {
	RequestID string                  `json:"request_id,omitempty"`
	Items     []InferenceMetricRecord `json:"items,omitempty"`
	Metrics   []InferenceMetricRecord `json:"metrics,omitempty"`
}

type MonitoringSummaryResponse struct {
	RequestID     string                   `json:"request_id,omitempty"`
	GeneratedAt   time.Time                `json:"generated_at"`
	Status        string                   `json:"status"`
	Deployments   DeploymentMonitorSummary `json:"deployments"`
	RuntimeHealth []RuntimeHealthSnapshot  `json:"runtime_health"`
	Alarms        []DeploymentAlarmSummary `json:"alarms"`
}

type DeploymentMonitorSummary struct {
	Total    int            `json:"total"`
	Active   int            `json:"active"`
	Failed   int            `json:"failed"`
	Stopped  int            `json:"stopped"`
	ByStatus map[string]int `json:"by_status"`
}

type RuntimeHealthSnapshot struct {
	TargetProfileID  string    `json:"target_profile_id"`
	Status           string    `json:"status"`
	RuntimeHealth    string    `json:"runtime_health"`
	CPUAvailable     bool      `json:"cpu_available"`
	MemoryAvailable  bool      `json:"memory_available"`
	GPUAvailable     bool      `json:"gpu_available"`
	StorageAvailable bool      `json:"storage_available"`
	LastCheckedAt    time.Time `json:"last_checked_at"`
}

type DeploymentAlarmSummary struct {
	Severity           string    `json:"severity"`
	ErrorCode          string    `json:"error_code"`
	Count              int       `json:"count"`
	LatestDeploymentID string    `json:"latest_deployment_id"`
	LatestStage        string    `json:"latest_stage"`
	LatestMessage      string    `json:"latest_message"`
	LatestAt           time.Time `json:"latest_at"`
	Retryable          bool      `json:"retryable"`
}

type DeploymentLog struct {
	Timestamp    string `json:"timestamp,omitempty"`
	Level        string `json:"level,omitempty"`
	RequestID    string `json:"request_id,omitempty"`
	DeploymentID string `json:"deployment_id,omitempty"`
	Component    string `json:"component,omitempty"`
	Stage        string `json:"stage,omitempty"`
	Message      string `json:"message,omitempty"`
	ErrorCode    string `json:"error_code,omitempty"`
}

type DeploymentLogsResponse struct {
	RequestID    string          `json:"request_id,omitempty"`
	DeploymentID string          `json:"deployment_id,omitempty"`
	Items        []DeploymentLog `json:"items,omitempty"`
	Logs         []DeploymentLog `json:"logs,omitempty"`
}

type ErrorObject struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	Details   map[string]any `json:"details,omitempty"`
	Retryable bool           `json:"retryable"`
}

type ErrorResponse struct {
	RequestID string      `json:"request_id,omitempty"`
	ErrorBody ErrorObject `json:"error"`
}

type APIError struct {
	StatusCode int
	RequestID  string
	Code       string
	Message    string
	Retryable  bool
}

func (err *APIError) Error() string {
	if err == nil {
		return ""
	}
	if err.Code != "" {
		return fmt.Sprintf("AppDeploy request failed with status %d (%s)", err.StatusCode, err.Code)
	}
	return fmt.Sprintf("AppDeploy request failed with status %d", err.StatusCode)
}
