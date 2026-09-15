package model

import (
	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/trustedorchestration"
)

type AgentControlReasoningComparisonRequest struct {
	CandidateID string `json:"candidate_id" validate:"required"`
}

type ExternalFlowRequest struct {
	ApplicationContext     agentcontrol.ApplicationContextEnvelope     `json:"application_context" validate:"required"`
	ResourceRecommendation agentcontrol.ResourceRecommendationEnvelope `json:"resource_recommendation" validate:"required"`
	DecisionAgent          string                                      `json:"decision_agent,omitempty"`
	InputOrigin            string                                      `json:"input_origin,omitempty"`
}

type FlowDeliveryRequest struct {
	AppVersionID           string `json:"app_version_id" validate:"required"`
	AcceptProjectionLimits bool   `json:"accept_projection_limits"`
}

type FlowDelivery struct {
	CorrelationID     string                               `json:"correlation_id"`
	DecisionID        string                               `json:"decision_id"`
	Revision          int                                  `json:"revision"`
	Status            string                               `json:"status"`
	ManifestSHA256    string                               `json:"manifest_sha256"`
	Request           appdeploy.DeploymentCreateRequest    `json:"request"`
	Deployment        *appdeploy.DeploymentResponse        `json:"deployment,omitempty"`
	Metrics           *appdeploy.DeploymentMetricsResponse `json:"metrics,omitempty"`
	Flow              *agentcontrol.Flow                   `json:"flow,omitempty"`
	UpdatedAt         string                               `json:"updated_at"`
	DestinationSHA256 string                               `json:"destination_sha256"`
	Limitations       []string                             `json:"limitations"`
}

type TrustedAutomationRunRequest struct {
	AppVersionID string                          `json:"app_version_id" validate:"required"`
	CandidateID  string                          `json:"candidate_id" validate:"required"`
	Input        agentcontrol.AutomationRunInput `json:"input" validate:"required"`
}

type TrustedAutomationRunErrorResponse struct {
	Message string                      `json:"message"`
	Error   string                      `json:"error"`
	Result  trustedorchestration.Result `json:"result"`
}

type ReadinessResponse struct {
	Status  string `json:"status" example:"ready"`
	Service string `json:"service" example:"geon"`
}
