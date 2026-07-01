package model

import (
	"encoding/json"
	"time"
)

const (
	StatusRequested         = "REQUESTED"
	StatusValidating        = "VALIDATING"
	StatusValidated         = "VALIDATED"
	StatusScheduling        = "SCHEDULING"
	StatusDeploying         = "DEPLOYING"
	StatusRunning           = "RUNNING"
	StatusStopping          = "STOPPING"
	StatusStopped           = "STOPPED"
	StatusValidationFailed  = "VALIDATION_FAILED"
	StatusSchedulingFailed  = "SCHEDULING_FAILED"
	StatusDeploymentFailed  = "DEPLOYMENT_FAILED"
	StatusRuntimeFailed     = "RUNTIME_FAILED"
	StatusExternalAPIFailed = "EXTERNAL_API_FAILED"
	StatusUnknown           = "UNKNOWN"
)

const (
	ErrAppSpecInvalid        = "APP_SPEC_INVALID"
	ErrAppArtifactNotFound   = "APP_ARTIFACT_NOT_FOUND"
	ErrEntrypointInvalid     = "ENTRYPOINT_INVALID"
	ErrRuntimeProfileInvalid = "RUNTIME_PROFILE_INVALID"
	ErrTargetProfileInvalid  = "TARGET_PROFILE_INVALID"
	ErrResourceInsufficient  = "RESOURCE_INSUFFICIENT"
	ErrGPURuntimeNotFound    = "GPU_RUNTIME_NOT_FOUND"
	ErrNvidiaDriverNotFound  = "NVIDIA_DRIVER_NOT_FOUND"
	ErrCSPVMUnreachable      = "CSP_VM_UNREACHABLE"
	ErrStorageUnavailable    = "STORAGE_PATH_UNAVAILABLE"
	ErrAIInfraAPITimeout     = "AI_INFRA_API_TIMEOUT"
	ErrAIInfraAPIFailed      = "AI_INFRA_API_FAILED"
	ErrGatewayAuthFailed     = "GATEWAY_AUTH_FAILED"
	ErrBespinAPIFailed       = "BESPIN_API_FAILED"
	ErrDeploymentFailed      = "DEPLOYMENT_FAILED"
	ErrRuntimeFailed         = "RUNTIME_FAILED"
)

type HealthResponse struct {
	RequestID string `json:"request_id,omitempty"`
	Status    string `json:"status"`
}

type ReadinessResponse struct {
	RequestID string            `json:"request_id,omitempty"`
	Status    string            `json:"status"`
	Checks    map[string]string `json:"checks"`
}

type AppCreateRequest struct {
	AppSpec AppSpec `json:"app_spec"`
}

type AppResponse struct {
	RequestID    string    `json:"request_id,omitempty"`
	AppID        string    `json:"app_id"`
	AppVersionID string    `json:"app_version_id"`
	Name         string    `json:"name"`
	Version      string    `json:"version"`
	AppSpec      AppSpec   `json:"app_spec"`
	CreatedAt    time.Time `json:"created_at"`
}

type AppSpec struct {
	SchemaVersion string       `json:"schema_version"`
	Kind          string       `json:"kind"`
	Metadata      Metadata     `json:"metadata"`
	Artifact      Artifact     `json:"artifact"`
	Entrypoint    Entrypoint   `json:"entrypoint"`
	Runtime       AppRuntime   `json:"runtime"`
	Resources     Resources    `json:"resources"`
	ModelRefs     []ModelRef   `json:"model_refs,omitempty"`
	Network       *Network     `json:"network,omitempty"`
	Healthcheck   *Healthcheck `json:"healthcheck,omitempty"`
}

type Metadata struct {
	Name        string `json:"name"`
	Version     string `json:"version"`
	Description string `json:"description,omitempty"`
}

type Artifact struct {
	Type     string `json:"type"`
	URI      string `json:"uri"`
	Checksum string `json:"checksum,omitempty"`
}

type Entrypoint struct {
	Command    string   `json:"command"`
	Args       []string `json:"args,omitempty"`
	WorkingDir string   `json:"working_dir,omitempty"`
}

type AppRuntime struct {
	Type        string `json:"type"`
	Accelerator string `json:"accelerator,omitempty"`
}

type Resources struct {
	CPU     string `json:"cpu,omitempty"`
	Memory  string `json:"memory,omitempty"`
	GPU     string `json:"gpu,omitempty"`
	Storage string `json:"storage,omitempty"`
}

type ModelRef struct {
	Name      string `json:"name"`
	Version   string `json:"version,omitempty"`
	URI       string `json:"uri"`
	MountPath string `json:"mount_path,omitempty"`
}

type Network struct {
	Ports []Port `json:"ports,omitempty"`
}

type Port struct {
	Name     string `json:"name,omitempty"`
	AppPort  int    `json:"app_port"`
	Protocol string `json:"protocol,omitempty"`
}

type Healthcheck struct {
	Type    string `json:"type,omitempty"`
	Path    string `json:"path,omitempty"`
	Command string `json:"command,omitempty"`
}

type RuntimeProfile struct {
	RuntimeProfileID string            `json:"runtime_profile_id"`
	Name             string            `json:"name,omitempty"`
	RuntimeType      string            `json:"runtime_type"`
	Accelerator      string            `json:"accelerator,omitempty"`
	AdapterType      string            `json:"adapter_type"`
	OperatingMode    string            `json:"operating_mode"`
	Readiness        map[string]string `json:"readiness,omitempty"`
}

type TargetProfile struct {
	TargetProfileID string         `json:"target_profile_id"`
	Name            string         `json:"name,omitempty"`
	CSP             string         `json:"csp"`
	Region          string         `json:"region,omitempty"`
	OS              *OSProfile     `json:"os,omitempty"`
	VM              VMProfile      `json:"vm"`
	Runtime         TargetRuntime  `json:"runtime"`
	GPU             *GPUProfile    `json:"gpu,omitempty"`
	Storage         *Storage       `json:"storage,omitempty"`
	Network         *TargetNetwork `json:"network,omitempty"`
}

type OSProfile struct {
	Type    string `json:"type,omitempty"`
	Version string `json:"version,omitempty"`
}

type VMProfile struct {
	Host          string `json:"host,omitempty"`
	SSHPort       int    `json:"ssh_port,omitempty"`
	CredentialRef string `json:"credential_ref,omitempty"`
}

type TargetRuntime struct {
	RuntimeType   string `json:"runtime_type,omitempty"`
	Accelerator   string `json:"accelerator,omitempty"`
	OperatingMode string `json:"operating_mode,omitempty"`
}

type GPUProfile struct {
	Vendor         string `json:"vendor,omitempty"`
	Count          int    `json:"count,omitempty"`
	DriverRequired bool   `json:"driver_required,omitempty"`
}

type Storage struct {
	ArtifactDir string `json:"artifact_dir,omitempty"`
	ModelDir    string `json:"model_dir,omitempty"`
	LogDir      string `json:"log_dir,omitempty"`
}

type TargetNetwork struct {
	ServicePortRange string `json:"service_port_range,omitempty"`
}

type DeploymentCreateRequest struct {
	AppID            string         `json:"app_id,omitempty"`
	AppVersionID     string         `json:"app_version_id"`
	RuntimeProfileID string         `json:"runtime_profile_id"`
	TargetProfileID  string         `json:"target_profile_id"`
	RequestedBy      string         `json:"requested_by,omitempty"`
	Parameters       map[string]any `json:"parameters,omitempty"`
}

type DeploymentResponse struct {
	RequestID        string    `json:"request_id,omitempty"`
	DeploymentID     string    `json:"deployment_id"`
	AppID            string    `json:"app_id,omitempty"`
	AppVersionID     string    `json:"app_version_id"`
	RuntimeProfileID string    `json:"runtime_profile_id"`
	TargetProfileID  string    `json:"target_profile_id"`
	Status           string    `json:"status"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

type DeploymentEvent struct {
	EventID      string    `json:"event_id,omitempty"`
	Timestamp    time.Time `json:"timestamp"`
	Level        string    `json:"level"`
	RequestID    string    `json:"request_id"`
	DeploymentID string    `json:"deployment_id"`
	Component    string    `json:"component"`
	Stage        string    `json:"stage"`
	Message      string    `json:"message"`
	ErrorCode    string    `json:"error_code,omitempty"`
	Retryable    bool      `json:"retryable,omitempty"`
}

type DeploymentLog struct {
	Timestamp    time.Time `json:"timestamp"`
	Level        string    `json:"level"`
	RequestID    string    `json:"request_id"`
	DeploymentID string    `json:"deployment_id"`
	Component    string    `json:"component"`
	Stage        string    `json:"stage"`
	Message      string    `json:"message"`
	ErrorCode    string    `json:"error_code,omitempty"`
}

type InferenceMetricCreateRequest struct {
	Timestamp     time.Time      `json:"timestamp,omitempty"`
	LatencyMS     float64        `json:"latency_ms,omitempty"`
	ThroughputRPS float64        `json:"throughput_rps,omitempty"`
	QualityScore  float64        `json:"quality_score,omitempty"`
	RequestCount  int            `json:"request_count,omitempty"`
	ErrorCount    int            `json:"error_count,omitempty"`
	Metadata      map[string]any `json:"metadata,omitempty"`
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

type InferenceInvokeRequest struct {
	Method         string            `json:"method,omitempty"`
	Path           string            `json:"path,omitempty"`
	Port           int               `json:"port,omitempty"`
	TimeoutSeconds int               `json:"timeout_seconds,omitempty"`
	Headers        map[string]string `json:"headers,omitempty"`
	Body           json.RawMessage   `json:"body,omitempty"`
}

type InferenceInvokeResponse struct {
	RequestID        string    `json:"request_id,omitempty"`
	DeploymentID     string    `json:"deployment_id"`
	AppID            string    `json:"app_id,omitempty"`
	AppVersionID     string    `json:"app_version_id"`
	RuntimeProfileID string    `json:"runtime_profile_id"`
	TargetProfileID  string    `json:"target_profile_id"`
	Method           string    `json:"method"`
	Path             string    `json:"path"`
	Port             int       `json:"port"`
	StatusCode       int       `json:"status_code"`
	Body             any       `json:"body,omitempty"`
	RawBody          string    `json:"raw_body,omitempty"`
	DurationMS       int64     `json:"duration_ms"`
	InvokedAt        time.Time `json:"invoked_at"`
}

type ResourceCheckRequest struct {
	RuntimeProfileID string   `json:"runtime_profile_id,omitempty"`
	TargetProfileID  string   `json:"target_profile_id"`
	Checks           []string `json:"checks,omitempty"`
}

type ResourceCheckResponse struct {
	RequestID        string            `json:"request_id,omitempty"`
	RuntimeProfileID string            `json:"runtime_profile_id,omitempty"`
	TargetProfileID  string            `json:"target_profile_id"`
	Status           string            `json:"status"`
	Checks           map[string]string `json:"checks"`
	Details          map[string]any    `json:"details,omitempty"`
	CheckedAt        time.Time         `json:"checked_at,omitempty"`
}

type ResourceInventory struct {
	TargetProfileID  string    `json:"target_profile_id"`
	CPUAvailable     bool      `json:"cpu_available"`
	MemoryAvailable  bool      `json:"memory_available"`
	GPUAvailable     bool      `json:"gpu_available"`
	StorageAvailable bool      `json:"storage_available"`
	RuntimeHealth    string    `json:"runtime_health"`
	LastCheckedAt    time.Time `json:"last_checked_at"`
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

type ErrorResponse struct {
	RequestID string      `json:"request_id"`
	Error     ErrorObject `json:"error"`
}

type ErrorObject struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	Details   map[string]any `json:"details,omitempty"`
	Retryable bool           `json:"retryable"`
}
