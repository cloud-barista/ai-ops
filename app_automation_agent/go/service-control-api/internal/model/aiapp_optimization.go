package model

import (
	"encoding/json"
	"time"
)

type AIAppOptimizationReview struct {
	ProposalID string                     `json:"proposalId"`
	Proposal   json.RawMessage            `json:"proposal" swaggertype:"object"`
	Eligible   bool                       `json:"eligible"`
	Reason     string                     `json:"reason,omitempty"`
	Next       *AIAppApplyRequest         `json:"nextRevision,omitempty"`
	Approval   *AIAppOptimizationApproval `json:"approval,omitempty"`
}

type AIAppOptimizationApprovalRequest struct {
	Digest string `json:"digest" validate:"required"`
}

type AIAppOptimizationApproval struct {
	ProposalID    string             `json:"proposalId"`
	CorrelationID string             `json:"correlationId"`
	ApprovedBy    string             `json:"approvedBy"`
	ApprovedAt    time.Time          `json:"approvedAt"`
	Status        string             `json:"status" enums:"confirmation_pending,queued,succeeded,failed,rejected"`
	Request       AIAppApplyRequest  `json:"request"`
	Receipt       *AIAppApplyReceipt `json:"receipt,omitempty"`
}

type AIAppOptimizationWindow struct {
	Start time.Time `json:"start"`
	End   time.Time `json:"end"`
}

type AIAppOptimizationLoad struct {
	ObservationID   string                  `json:"observationId"`
	WorkloadID      string                  `json:"workloadId"`
	RuntimeImage    string                  `json:"runtimeImage"`
	Window          AIAppOptimizationWindow `json:"window"`
	PromptTokensP50 int                     `json:"promptTokensP50"`
	MaxOutputTokens int                     `json:"maxOutputTokens"`
	OfferedRPS      float64                 `json:"offeredRps"`
	SLOSeconds      float64                 `json:"sloSeconds"`
	Attempted       int                     `json:"attempted"`
	Failed          int                     `json:"failed"`
	WithinSLO       int                     `json:"withinSLO"`
}

type AIAppOptimizationCompareRequest struct {
	Load               AIAppOptimizationLoad                `json:"load"`
	DowntimeSeconds    *float64                             `json:"downtimeSeconds,omitempty"`
	TransitionEvidence *AIAppOptimizationTransitionEvidence `json:"transitionEvidence,omitempty"`
}

type AIAppOptimizationTransitionEvidence struct {
	CorrelationID                    string    `json:"correlationId"`
	PreviousRevision                 int       `json:"previousRevision"`
	Revision                         int       `json:"revision"`
	BaselineApplicationDeploymentID  string    `json:"baselineApplicationDeploymentId"`
	CandidateApplicationDeploymentID string    `json:"candidateApplicationDeploymentId"`
	RuntimeImage                     string    `json:"runtimeImage"`
	GPUUUIDs                         []string  `json:"gpuUUIDs"`
	LastBaselineResponseAt           time.Time `json:"lastBaselineResponseAt"`
	FirstCandidateResponseAt         time.Time `json:"firstCandidateResponseAt"`
}

type AIAppOptimizationAssessment struct {
	ProposalID    string             `json:"proposalId"`
	ObservationID string             `json:"observationId"`
	RecordedBy    string             `json:"recordedBy"`
	RecordedAt    time.Time          `json:"recordedAt"`
	Comparison    json.RawMessage    `json:"comparison" swaggertype:"object"`
	RollbackDraft *AIAppApplyRequest `json:"rollbackDraft,omitempty"`
}
