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

// FlowDelivery is a local Revision 1 submission receipt. Its KHU request IDs
// are not echoed by AppDeployer as execution action IDs.
type FlowDelivery struct {
	CorrelationID string `json:"correlation_id"`
	DecisionID    string `json:"decision_id"`
	Revision      int    `json:"revision"`
	// RequestID identifies the approved KHU request, not an AppDeployer action.
	RequestID string `json:"request_id,omitempty"`
	// RequestMessageID is the approved KHU Common JSON message ID.
	RequestMessageID  string `json:"request_message_id,omitempty"`
	RequestOccurredAt string `json:"request_occurred_at,omitempty"`
	Status            string `json:"status"`
	// ReservedAt is before HTTP POST and does not prove AppDeployer received it.
	ReservedAt string `json:"reserved_at,omitempty"`
	// ReceiptReceivedAt is set only after a matching AppDeployer response.
	ReceiptReceivedAt string                            `json:"receipt_received_at,omitempty"`
	ManifestSHA256    string                            `json:"manifest_sha256"`
	Request           appdeploy.DeploymentCreateRequest `json:"request"`
	Deployment        *appdeploy.DeploymentResponse     `json:"deployment,omitempty"`
	// StatusObservations are source-declared states, not ready-replica evidence.
	StatusObservations []FlowDeliveryStatusObservation      `json:"status_observations,omitempty"`
	Metrics            *appdeploy.DeploymentMetricsResponse `json:"metrics,omitempty"`
	Flow               *agentcontrol.Flow                   `json:"flow,omitempty"`
	UpdatedAt          string                               `json:"updated_at"`
	DestinationSHA256  string                               `json:"destination_sha256"`
	Limitations        []string                             `json:"limitations"`
}

// FlowDeliveryStatusObservation is an AppDeployer state observation, not a
// service-readiness or applied-replica acknowledgement.
type FlowDeliveryStatusObservation struct {
	ObservationID string `json:"observation_id"`
	DeploymentID  string `json:"deployment_id"`
	// Status is the AppDeployer-declared state, not a readiness measurement.
	Status string `json:"status"`
	// SourceUpdatedAt preserves the raw AppDeployer-declared status time.
	SourceUpdatedAt string `json:"source_updated_at,omitempty"`
	// FetchedAt is the local KHU status fetch time.
	FetchedAt string `json:"fetched_at"`
	// EffectiveUpdatedAt is the time used by the in-memory Flow status.
	EffectiveUpdatedAt string `json:"effective_updated_at"`
	// TimeBasis identifies whether source time or local fetch time was used.
	TimeBasis string `json:"time_basis"`
	// FallbackReason explains why the raw source time could not be used.
	FallbackReason string `json:"fallback_reason,omitempty"`
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
