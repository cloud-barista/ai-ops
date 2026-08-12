package llmop

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"reflect"

	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

const (
	SafeguardStageName             = "request_safeguard"
	SafeguardContinuationStageName = "downstream_planning"
	SafeguardBindingAlgorithm      = "sha256"
)

// SafeguardStageResult is the public boundary between untrusted natural
// language and downstream planning. Approved is false unless both the
// deterministic request guard and the bounded safeguard review allow the
// exact normalized request. It intentionally contains no Manifest, proposal,
// target, runtime, or AppDeploy submission payload.
type SafeguardStageResult struct {
	APIVersion    string                         `json:"api_version"`
	Stage         string                         `json:"stage"`
	RequestID     string                         `json:"request_id,omitempty"`
	CorrelationID string                         `json:"correlation_id,omitempty"`
	TraceID       string                         `json:"trace_id,omitempty"`
	Status        string                         `json:"status"`
	Approved      bool                           `json:"approved"`
	Decision      Decision                       `json:"decision"`
	RequestGuard  plannerguard.Decision          `json:"request_guard"`
	Review        *SafeguardReviewEvidence       `json:"review,omitempty"`
	Input         InputSummary                   `json:"input"`
	Continuation  *ApprovedContinuationEvidence `json:"continuation,omitempty"`
}

// ApprovedContinuationEvidence lets an orchestrator correlate the exact
// request, normalized observations, guard policy, candidate identity, and
// safeguard decision that were reviewed. The binding is audit evidence, not a
// bearer token: it authorizes neither deployment nor AppDeploy submission.
type ApprovedContinuationEvidence struct {
	Stage              string   `json:"stage"`
	RequestID          string   `json:"request_id"`
	CorrelationID      string   `json:"correlation_id"`
	TraceID            string   `json:"trace_id,omitempty"`
	PolicyVersion      string   `json:"policy_version"`
	CandidateID        string   `json:"candidate_id"`
	ReviewDecision     string   `json:"review_decision"`
	ReviewReasonCode   string   `json:"review_reason_code"`
	Confidence         *float64 `json:"confidence"`
	ObservationStatus  string   `json:"observation_status"`
	BindingAlgorithm   string   `json:"binding_algorithm"`
	RequestBinding     string   `json:"request_binding"`
	SubmissionMode     string   `json:"submission_mode"`
}

type safeguardBindingEnvelope struct {
	APIVersion    string                  `json:"api_version"`
	Stage         string                  `json:"stage"`
	Request       Request                 `json:"request"`
	Normalized    NormalizedContext       `json:"normalized"`
	Policy        plannerguard.Policy     `json:"policy"`
	Candidate     llmclient.Candidate     `json:"candidate"`
	Review        SafeguardReview         `json:"review"`
	ReviewEvidence SafeguardReviewEvidence `json:"review_evidence"`
}

func newSafeguardStageResult(result Result) SafeguardStageResult {
	return SafeguardStageResult{
		APIVersion:    APIVersion,
		Stage:         SafeguardStageName,
		RequestID:     result.RequestID,
		CorrelationID: result.CorrelationID,
		TraceID:       result.TraceID,
		Status:        result.Status,
		Approved:      false,
		Decision:      result.Decision,
		RequestGuard:  result.Safeguard.Request,
		Review:        copySafeguardReviewEvidence(result.Evidence.SafeguardReview),
		Input:         result.Evidence.Input,
	}
}

func approveSafeguardStage(
	stage *SafeguardStageResult,
	request Request,
	normalized NormalizedContext,
	candidate llmclient.Candidate,
	guardPolicy plannerguard.Policy,
	review SafeguardReview,
) error {
	if stage == nil ||
		stage.Status != StatusSafeguardApproved ||
		!stage.RequestGuard.Valid ||
		review.Decision != SafeguardDecisionAllow ||
		stage.Review == nil ||
		stage.Review.Decision != SafeguardDecisionAllow {
		return fmt.Errorf("safeguard continuation requires matching deterministic and model approvals")
	}
	binding, err := safeguardRequestBinding(
		request,
		normalized,
		candidate,
		guardPolicy,
		review,
		*stage.Review,
	)
	if err != nil {
		return err
	}
	stage.Approved = true
	stage.Continuation = &ApprovedContinuationEvidence{
		Stage:             SafeguardContinuationStageName,
		RequestID:         stage.RequestID,
		CorrelationID:     stage.CorrelationID,
		TraceID:           stage.TraceID,
		PolicyVersion:     stage.RequestGuard.PolicyVersion,
		CandidateID:       candidate.CandidateID,
		ReviewDecision:    review.Decision,
		ReviewReasonCode:  review.ReasonCode,
		Confidence:        copyConfidence(review.Confidence),
		ObservationStatus: normalized.ObservationStatus,
		BindingAlgorithm:  SafeguardBindingAlgorithm,
		RequestBinding:    binding,
		SubmissionMode:    "not_submitted",
	}
	return nil
}

func safeguardRequestBinding(
	request Request,
	normalized NormalizedContext,
	candidate llmclient.Candidate,
	guardPolicy plannerguard.Policy,
	review SafeguardReview,
	reviewEvidence SafeguardReviewEvidence,
) (string, error) {
	// PlanningConstraints are trusted geon enrichment added only after this
	// stage. Every other request field, including app_version_id and the full
	// operation context, remains bound to the first safeguard decision.
	boundRequest := request
	boundRequest.Application.PlanningConstraints = nil
	payload, err := json.Marshal(safeguardBindingEnvelope{
		APIVersion:     APIVersion,
		Stage:          SafeguardStageName,
		Request:        boundRequest,
		Normalized:     normalized,
		Policy:         guardPolicy,
		Candidate:      candidate,
		Review:         review,
		ReviewEvidence: reviewEvidence,
	})
	if err != nil {
		return "", fmt.Errorf("encode safeguard continuation binding: %w", err)
	}
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:]), nil
}

func verifyApprovedContinuation(
	stage SafeguardStageResult,
	request Request,
	normalized NormalizedContext,
	requestGuard plannerguard.Decision,
	candidate llmclient.Candidate,
	guardPolicy plannerguard.Policy,
) (SafeguardReview, error) {
	if stage.APIVersion != APIVersion ||
		stage.Stage != SafeguardStageName ||
		stage.Status != StatusSafeguardApproved ||
		!stage.Approved ||
		stage.Continuation == nil ||
		stage.Review == nil {
		return SafeguardReview{}, fmt.Errorf("approved safeguard stage envelope is incomplete")
	}
	if stage.RequestID != request.RequestID ||
		stage.CorrelationID != request.CorrelationID ||
		stage.TraceID != request.TraceID ||
		!reflect.DeepEqual(stage.Input, inputSummary(request, normalized)) ||
		stage.Decision.ObservationStatus != normalized.ObservationStatus {
		return SafeguardReview{}, fmt.Errorf("approved safeguard identifiers or observation status changed")
	}
	if !requestGuard.Valid ||
		!stage.RequestGuard.Valid ||
		stage.RequestGuard.Status != "approved" ||
		stage.RequestGuard.PolicyVersion != guardPolicy.Version ||
		!reflect.DeepEqual(stage.RequestGuard, requestGuard) {
		return SafeguardReview{}, fmt.Errorf("deterministic request guard approval changed")
	}
	if err := validateCandidate(candidate, request); err != nil {
		return SafeguardReview{}, err
	}
	if stage.Review.Provider != candidate.Provider ||
		stage.Review.CandidateID != candidate.CandidateID ||
		stage.Review.ActualModel != candidate.ActualModel ||
		stage.Review.LatencyMS < 0 ||
		stage.Review.Decision != SafeguardDecisionAllow ||
		stage.Decision.Action != SafeguardDecisionAllow ||
		stage.Decision.ReasonCode != stage.Review.ReasonCode ||
		!sameConfidence(stage.Decision.Confidence, stage.Review.Confidence) {
		return SafeguardReview{}, fmt.Errorf("safeguard review evidence is inconsistent")
	}
	review := SafeguardReview{
		Decision:   stage.Review.Decision,
		ReasonCode: stage.Review.ReasonCode,
		Reason:     stage.Decision.Reason,
		Confidence: copyConfidence(stage.Review.Confidence),
	}
	if err := validateSafeguardReview(review, request, candidate, guardPolicy); err != nil {
		return SafeguardReview{}, fmt.Errorf("revalidate safeguard review: %w", err)
	}
	continuation := stage.Continuation
	if continuation.Stage != SafeguardContinuationStageName ||
		continuation.RequestID != request.RequestID ||
		continuation.CorrelationID != request.CorrelationID ||
		continuation.TraceID != request.TraceID ||
		continuation.PolicyVersion != guardPolicy.Version ||
		continuation.CandidateID != candidate.CandidateID ||
		continuation.ReviewDecision != review.Decision ||
		continuation.ReviewReasonCode != review.ReasonCode ||
		!sameConfidence(continuation.Confidence, review.Confidence) ||
		continuation.ObservationStatus != normalized.ObservationStatus ||
		continuation.BindingAlgorithm != SafeguardBindingAlgorithm ||
		continuation.SubmissionMode != "not_submitted" {
		return SafeguardReview{}, fmt.Errorf("safeguard continuation fields are inconsistent")
	}
	expectedBinding, err := safeguardRequestBinding(
		request,
		normalized,
		candidate,
		guardPolicy,
		review,
		*stage.Review,
	)
	if err != nil {
		return SafeguardReview{}, err
	}
	if subtle.ConstantTimeCompare(
		[]byte(continuation.RequestBinding),
		[]byte(expectedBinding),
	) != 1 {
		return SafeguardReview{}, fmt.Errorf("safeguard continuation binding mismatch")
	}
	return review, nil
}

func sameConfidence(left *float64, right *float64) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return *left == *right
}

func copySafeguardReviewEvidence(
	evidence *SafeguardReviewEvidence,
) *SafeguardReviewEvidence {
	if evidence == nil {
		return nil
	}
	copy := *evidence
	copy.Confidence = copyConfidence(evidence.Confidence)
	return &copy
}
