package agentcontrol

const (
	// Canonical decision actions for the optimizer contract. Legacy actions
	// remain in models.go for compatibility with the existing prototype.
	ActionAcceptRecommendation       = "ACCEPT_RECOMMENDATION"
	ActionRequestAlternativeResource = "REQUEST_ALTERNATIVE_RESOURCE"
	ActionAdjustResourceRequirement  = "ADJUST_RESOURCE_REQUIREMENT"
	ActionRetryDeployment            = "RETRY_DEPLOYMENT"
	ActionRequestNewVM               = "REQUEST_NEW_VM"
	ActionRejectDeployment           = "REJECT_DEPLOYMENT"
	ActionRequestUserClarification   = "REQUEST_USER_CLARIFICATION"

	ReasonFailureRequiresAlternative = "FAILURE_REQUIRES_ALTERNATIVE_RESOURCE"
	ReasonFailureRequiresAdjustment  = "FAILURE_REQUIRES_RESOURCE_ADJUSTMENT"
	ReasonFailureTransient           = "FAILURE_TRANSIENT_RETRYABLE"
	ReasonFailureNeedsClarification  = "FAILURE_NEEDS_USER_CLARIFICATION"

	ErrorCodeGPUOOM               = "GPU_OOM"
	ErrorCodeCUDAMismatch         = "CUDA_MISMATCH"
	ErrorCodeResourceUnavailable  = "RESOURCE_UNAVAILABLE"
	ErrorCodeResourceInsufficient = "RESOURCE_INSUFFICIENT"
	ErrorCodeTransientDeployment  = "TRANSIENT_DEPLOYMENT_FAILURE"
)

type RetryPolicy struct {
	MaxAttempts int `json:"max_attempts"`
	Attempt     int `json:"attempt,omitempty"`
}

type ExperienceQuery struct {
	ProfileID   string
	CandidateID string
	ErrorCode   string
	Limit       int
}

type DeploymentExperience struct {
	CorrelationID   string   `json:"correlation_id"`
	ProfileID       string   `json:"profile_id"`
	CandidateID     string   `json:"candidate_id,omitempty"`
	DecisionID      string   `json:"decision_id,omitempty"`
	DeploymentID    string   `json:"deployment_id,omitempty"`
	DeploymentState string   `json:"deployment_state,omitempty"`
	Outcome         string   `json:"outcome,omitempty"`
	ErrorCode       string   `json:"error_code,omitempty"`
	Success         bool     `json:"success"`
	SLOViolations   []string `json:"slo_violations,omitempty"`
	UpdatedAt       string   `json:"updated_at"`
}
