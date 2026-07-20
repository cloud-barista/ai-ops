package appdeploy

import "fmt"

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

type DeploymentResponse struct {
	RequestID       string             `json:"request_id,omitempty"`
	DeploymentID    string             `json:"deployment_id"`
	AppID           string             `json:"app_id,omitempty"`
	AppVersionID    string             `json:"app_version_id,omitempty"`
	TargetProfileID string             `json:"target_profile_id,omitempty"`
	Status          string             `json:"status"`
	CreatedAt       string             `json:"created_at,omitempty"`
	UpdatedAt       string             `json:"updated_at,omitempty"`
	Manifest        DeploymentManifest `json:"manifest,omitempty"`
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
