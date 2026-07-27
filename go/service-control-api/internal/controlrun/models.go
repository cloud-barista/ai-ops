package controlrun

import (
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type Status string

const (
	StatusReceived         Status = "RECEIVED"
	StatusRequestRejected  Status = "REQUEST_REJECTED"
	StatusAgentRejected    Status = "AGENT_REJECTED"
	StatusPlanning         Status = "PLANNING"
	StatusManifestRejected Status = "MANIFEST_REJECTED"
	StatusManifestApproved Status = "MANIFEST_APPROVED"
	StatusSubmitting       Status = "SUBMITTING"
	StatusAppDeployFailed  Status = "APPDEPLOY_FAILED"
	StatusDeployed         Status = "DEPLOYED"
	StatusAgentDispatching Status = "AGENT_DISPATCHING"
	StatusAgentCompleted   Status = "AGENT_COMPLETED"
	StatusAgentFailed      Status = "AGENT_FAILED"
	StatusResultRejected   Status = "RESULT_REJECTED"
)

type SafeRequest struct {
	NaturalLanguageRequest string `json:"natural_language_request,omitempty"`
	AppVersionID           string `json:"app_version_id,omitempty"`
	TargetProfileID        string `json:"target_profile_id,omitempty"`
	CandidateID            string `json:"candidate_id,omitempty"`
	RequestedBy            string `json:"requested_by,omitempty"`
	AgentName              string `json:"agent_name,omitempty"`
	Capability             string `json:"capability,omitempty"`
	Action                 string `json:"action,omitempty"`
}

type AgentSelection struct {
	Name       string `json:"name,omitempty"`
	Capability string `json:"capability,omitempty"`
	Action     string `json:"action,omitempty"`
	Source     string `json:"source,omitempty"`
	Reason     string `json:"reason,omitempty"`
}

type Stage struct {
	Name      string         `json:"name"`
	Status    string         `json:"status"`
	Reason    string         `json:"reason,omitempty"`
	StartedAt time.Time      `json:"started_at"`
	EndedAt   *time.Time     `json:"ended_at,omitempty"`
	Details   map[string]any `json:"details,omitempty"`
}

type AgentExecution struct {
	Status           string         `json:"status"`
	LatencyMS        int64          `json:"latency_ms"`
	Proposal         map[string]any `json:"proposal,omitempty"`
	Result           map[string]any `json:"result,omitempty"`
	Evidence         map[string]any `json:"evidence,omitempty"`
	GuardStatus      string         `json:"guard_status,omitempty"`
	Message          string         `json:"message,omitempty"`
	DomainValidation string         `json:"domain_validation,omitempty"`
}

type Run struct {
	RunID            string                           `json:"run_id"`
	Status           Status                           `json:"status"`
	CreatedAt        time.Time                        `json:"created_at"`
	UpdatedAt        time.Time                        `json:"updated_at"`
	Request          SafeRequest                      `json:"request"`
	RequestGuard     plannerguard.Decision            `json:"request_guard"`
	SelectedAgent    AgentSelection                   `json:"selected_agent"`
	Execution        *AgentExecution                  `json:"execution,omitempty"`
	Generation       deploymentplanner.GenerateResult `json:"generation"`
	Manifest         appdeploy.DeploymentManifest     `json:"manifest"`
	Deployment       *appdeploy.DeploymentResponse    `json:"deployment,omitempty"`
	Polling          *deploymentplanner.PollingResult `json:"polling,omitempty"`
	Logs             []appdeploy.DeploymentLog        `json:"logs,omitempty"`
	RetryRecommended bool                             `json:"retry_recommended,omitempty"`
	RetryReason      string                           `json:"retry_reason,omitempty"`
	CorrelationIDs   []string                         `json:"correlation_ids,omitempty"`
	Stages           []Stage                          `json:"stages"`
}

type CreateInput struct {
	RunID   string
	Request SafeRequest
}
