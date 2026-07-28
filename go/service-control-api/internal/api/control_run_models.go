package api

import "kyunghee-aiops/service-control-api/internal/appdeploy"

type CreateControlRunRequest struct {
	NaturalLanguageRequest string                            `json:"natural_language_request" validate:"required,max=8000" example:"Deploy an inference application with one GPU and 16Gi memory."`
	AppVersionID           string                            `json:"app_version_id" validate:"required" example:"appver-llm-inference-v1"`
	CandidateID            string                            `json:"candidate_id" validate:"required" example:"qwen3.5-ops-planner"`
	TargetProfileID        string                            `json:"target_profile_id,omitempty" example:"target-profile-hint"`
	RequestedBy            string                            `json:"requested_by,omitempty" example:"ai-ops-geon-planner"`
	AgentName              string                            `json:"agent_name,omitempty" example:"AIApplicationAutomationAgent"`
	Parameters             map[string]any                    `json:"parameters,omitempty"`
	Requirements           *appdeploy.DeploymentRequirements `json:"requirements,omitempty"`
}

type SubmitControlRunRequest struct {
	PollIntervalMS  int `json:"poll_interval_ms,omitempty" validate:"omitempty,min=1,max=60000" example:"1000"`
	MaxPollAttempts int `json:"max_poll_attempts,omitempty" validate:"omitempty,min=1,max=600" example:"60"`
}
