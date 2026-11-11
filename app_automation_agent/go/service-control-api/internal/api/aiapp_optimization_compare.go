package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"time"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/model"
)

type optimizationAssessmentRecord struct {
	model.AIAppOptimizationAssessment
	RequestDigest string `json:"requestDigest"`
}

// RestCompareAIAppOptimization godoc
// @ID CompareAIAppOptimization
// @Summary Record the applied optimization's measured SLO and throughput comparison
// @Tags AI-App Integration
// @Security FrameworkPrincipalBearer
// @Accept json
// @Produce json
// @Param correlation_id path string true "Approved flow ID"
// @Param proposal_id path string true "Approved proposal SHA-256"
// @Param request body model.AIAppOptimizationCompareRequest true "Post-apply load and application response evidence. Legacy proposals accept a measured duration instead."
// @Success 200 {object} model.AIAppOptimizationAssessment
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/optimizations/{proposal_id}/compare [post]
func (h restHandler) RestCompareAIAppOptimization(c echo.Context) error {
	access, failure := h.authorizeOptimization(c, "optimize_inference.compare")
	if failure != nil {
		return failure.respond(c)
	}
	var req model.AIAppOptimizationCompareRequest
	if err := decodeFocusedRequest(c, &req); err != nil || !aiAppEvidenceDigest.MatchString(req.Load.ObservationID) {
		return focusedError(c, http.StatusBadRequest, "INVALID_OPTIMIZATION_COMPARISON",
			"Supply the post-apply observation and its load report.", nil)
	}
	req.Load.Window.Start, req.Load.Window.End = req.Load.Window.Start.UTC(), req.Load.Window.End.UTC()
	downtime, ok := comparisonDowntime(access.flow, &req)
	if !ok {
		return focusedError(c, http.StatusBadRequest, "INVALID_OPTIMIZATION_COMPARISON",
			"Supply one transition measurement for the applied flow and observation window.", nil)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	result, failure := h.compareOptimization(ctx, access, req, downtime)
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, result)
}

func comparisonDowntime(flow string, req *model.AIAppOptimizationCompareRequest) (float64, bool) {
	if req.TransitionEvidence == nil {
		if req.DowntimeSeconds == nil || *req.DowntimeSeconds < 0 {
			return 0, false
		}
		return *req.DowntimeSeconds, true
	}
	evidence := req.TransitionEvidence
	if req.DowntimeSeconds != nil || evidence.CorrelationID != flow || evidence.PreviousRevision < 1 || evidence.Revision-1 != evidence.PreviousRevision ||
		!aiAppDeploymentID.MatchString(evidence.BaselineApplicationDeploymentID) || !aiAppDeploymentID.MatchString(evidence.CandidateApplicationDeploymentID) ||
		evidence.RuntimeImage == "" || evidence.RuntimeImage != req.Load.RuntimeImage || len(evidence.GPUUUIDs) == 0 ||
		evidence.LastBaselineResponseAt.IsZero() || !evidence.FirstCandidateResponseAt.After(evidence.LastBaselineResponseAt) ||
		evidence.FirstCandidateResponseAt.After(req.Load.Window.Start) {
		return 0, false
	}
	evidence.LastBaselineResponseAt, evidence.FirstCandidateResponseAt = evidence.LastBaselineResponseAt.UTC(), evidence.FirstCandidateResponseAt.UTC()
	return evidence.FirstCandidateResponseAt.Sub(evidence.LastBaselineResponseAt).Seconds(), true
}

func (h restHandler) compareOptimization(ctx context.Context, access optimizationAccess, req model.AIAppOptimizationCompareRequest, downtime float64) (*model.AIAppOptimizationAssessment, *aiAppIntegrationFailure) {
	approval, failure := h.readOptimizationApproval(access)
	if failure != nil {
		return nil, failure
	}
	if approval == nil {
		return nil, optimizationConflict("The optimization must be approved and applied before comparison.")
	}
	path := h.optimizationPath(access, "comparisons", req.Load.ObservationID+".json")
	digest := frameworkDigest(req)
	if saved, failure := readOptimizationAssessment(path, access.id, req.Load.ObservationID, digest); saved != nil || failure != nil {
		return saved, failure
	}
	h.refreshOptimizationApproval(ctx, access, approval)
	if approval.EngineDigest != aiAppEngineDigest(access.engine) || approval.Receipt == nil || approval.Receipt.Status != "succeeded" {
		return nil, optimizationConflict("The approved optimization has no confirmed completed apply.")
	}
	if e := req.TransitionEvidence; e != nil && (e.Revision != approval.Receipt.Revision ||
		e.PreviousRevision != approval.Receipt.PreviousRevision || e.CandidateApplicationDeploymentID != approval.Receipt.Application.DeploymentID) {
		return nil, optimizationConflict("The transition evidence is for a different applied revision.")
	}
	body := struct {
		ProposalID string `json:"proposalId"`
		model.AIAppOptimizationCompareRequest
	}{access.id, req}
	raw, err := h.callAIAppApprovalTool(ctx, access.engine, aiAppOptimizationCompareTool, "read", body)
	if errors.Is(err, errAIAppToolRejected) {
		return nil, optimizationConflict("The new measurement does not match the applied configuration, transition or observation window.")
	}
	if err != nil {
		return nil, intentChangePartnerFailure(err)
	}
	var comparison struct {
		ProposalID         string                                     `json:"proposalId"`
		Status             string                                     `json:"status"`
		Observation        optimizationObservation                    `json:"observation"`
		DowntimeSeconds    *float64                                   `json:"downtimeSeconds"`
		TransitionEvidence *model.AIAppOptimizationTransitionEvidence `json:"transitionEvidence"`
	}
	if json.Unmarshal(raw, &comparison) != nil || comparison.ProposalID != access.id ||
		(comparison.Status != "KEEP" && comparison.Status != "ROLLBACK_REVIEW") ||
		comparison.Observation.ObservationID != req.Load.ObservationID ||
		!comparison.Observation.Window.Start.Equal(req.Load.Window.Start) ||
		!comparison.Observation.Window.End.Equal(req.Load.Window.End) ||
		!comparison.Observation.matches(approval.Receipt) ||
		comparison.DowntimeSeconds == nil || *comparison.DowntimeSeconds != downtime ||
		!reflect.DeepEqual(comparison.TransitionEvidence, req.TransitionEvidence) {
		return nil, intentChangePartnerFailure(errors.New("AI-App returned a comparison for different evidence"))
	}
	record := optimizationAssessmentRecord{
		AIAppOptimizationAssessment: model.AIAppOptimizationAssessment{
			ProposalID: access.id, ObservationID: req.Load.ObservationID, RecordedBy: access.principal.Name,
			RecordedAt: time.Now().UTC(), Comparison: raw,
		},
		RequestDigest: digest,
	}
	if comparison.Status == "ROLLBACK_REVIEW" {
		previous, err := loadJSON[aiAppApplyApproval](h.applyApprovalPath(access.flow, approval.Request.PreviousRevision))
		if err != nil || previous.Request.Revision != approval.Request.PreviousRevision ||
			!validApplyRequest(previous.Request, access.engine.Namespace) ||
			!reflect.DeepEqual(previous.Request.Application, approval.Request.Application) {
			return nil, optimizationConflict("The pre-optimization configuration is unavailable for rollback review.")
		}
		rollback := previous.Request
		rollback.Revision, rollback.PreviousRevision = approval.Request.Revision+1, approval.Request.Revision
		if !validApplyRequest(rollback, access.engine.Namespace) {
			return nil, optimizationConflict("The pre-optimization configuration cannot be restored in a next revision.")
		}
		record.RollbackDraft = &rollback
	}
	if err := saveJournalRecord(path, record, true); err != nil {
		if errors.Is(err, os.ErrExist) {
			saved, failure := readOptimizationAssessment(path, access.id, req.Load.ObservationID, digest)
			if saved != nil || failure != nil {
				return saved, failure
			}
		}
		return nil, intentChangeStorage(err)
	}
	return &record.AIAppOptimizationAssessment, nil
}

func readOptimizationAssessment(path, id, observationID, digest string) (*model.AIAppOptimizationAssessment, *aiAppIntegrationFailure) {
	record, err := loadJSON[optimizationAssessmentRecord](path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil || record.ProposalID != id || record.ObservationID != observationID || record.RecordedAt.IsZero() ||
		!json.Valid(record.Comparison) {
		return nil, intentChangeStorage(err)
	}
	if record.RequestDigest != digest {
		return nil, optimizationConflict("This observation already has a different recorded load or downtime report.")
	}
	return &record.AIAppOptimizationAssessment, nil
}
