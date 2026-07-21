package api

type AppDeployPlannerRequest struct {
	NaturalLanguageRequest string         `json:"natural_language_request" validate:"required,max=8000" example:"GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요."`
	AppVersionID           string         `json:"app_version_id" validate:"required" example:"appver-llm-inference-v1"`
	CandidateID            string         `json:"candidate_id" validate:"required" example:"qwen3.5-ops-planner"`
	TargetProfileID        string         `json:"target_profile_id,omitempty" example:"target-profile-hint"`
	RequestedBy            string         `json:"requested_by,omitempty" example:"ai-ops-geon-planner"`
	Parameters             map[string]any `json:"parameters,omitempty"`
	PollIntervalMS         int            `json:"poll_interval_ms,omitempty" validate:"omitempty,min=1,max=60000" example:"1000"`
	MaxPollAttempts        int            `json:"max_poll_attempts,omitempty" validate:"omitempty,min=1,max=600" example:"60"`
}
