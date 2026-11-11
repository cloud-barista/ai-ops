package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"time"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"

	"kyunghee-aiops/service-control-api/internal/model"
)

const (
	aiAppOptimizationPath        = pathAgentControl + "/flows/:correlation_id/aiapp/optimizations/:proposal_id"
	aiAppOptimizationReadTool    = "get_inference_optimization"
	aiAppOptimizationCompareTool = "compare_inference_optimization"
)

var aiAppEvidenceDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

type optimizationAccess struct {
	principal *frameworkPrincipal
	engine    *registeredAIAppEngine
	flow      string
	id        string
}

type optimizationApprovalRecord struct {
	model.AIAppOptimizationApproval
	EngineDigest string          `json:"engineDigest"`
	Proposal     json.RawMessage `json:"proposal"`
}

type optimizationObservation struct {
	ObservationID string `json:"observationId"`
	CorrelationID string `json:"correlationId"`
	Revision      int    `json:"revision"`
	Placement     struct {
		Namespace    string `json:"nsId"`
		InfraID      string `json:"infraId"`
		NodeID       string `json:"nodeId"`
		DeploymentID string `json:"deploymentId"`
	} `json:"placement"`
	InferenceRunID          string                        `json:"inferenceRunId"`
	ApplicationDeploymentID string                        `json:"applicationDeploymentId"`
	ApplicationRunID        string                        `json:"applicationRunId"`
	ApplicationName         string                        `json:"applicationName"`
	ApplicationVersion      int                           `json:"applicationVersion"`
	RuntimeID               string                        `json:"runtimeId"`
	ModelID                 string                        `json:"modelId"`
	ArtifactID              string                        `json:"artifactId"`
	MaxNumSeqs              int                           `json:"maxNumSeqs"`
	Window                  model.AIAppOptimizationWindow `json:"window"`
}

func (o optimizationObservation) matches(receipt *model.AIAppApplyReceipt) bool {
	return receipt.Status == "succeeded" && o.CorrelationID == receipt.CorrelationID && o.Revision == receipt.Revision &&
		o.Placement.Namespace == receipt.Namespace &&
		o.Placement.Namespace+"/"+o.Placement.InfraID+"/"+o.Placement.NodeID == receipt.Target &&
		o.Placement.DeploymentID == receipt.Inference.DeploymentID && o.InferenceRunID == receipt.Inference.EngineRunID &&
		o.ApplicationDeploymentID == receipt.Application.DeploymentID && o.ApplicationRunID == receipt.Application.EngineRunID
}

func registerAIAppOptimizationRoutes(server *echo.Echo, h restHandler) {
	server.GET(aiAppOptimizationPath, h.RestReviewAIAppOptimization)
	server.POST(aiAppOptimizationPath+"/approve", h.RestApproveAIAppOptimization)
	server.POST(aiAppOptimizationPath+"/compare", h.RestCompareAIAppOptimization)
}

// RestReviewAIAppOptimization godoc
// @ID ReviewAIAppOptimization
// @Summary Read a published vLLM optimization and its exact next revision
// @Tags AI-App Integration
// @Security FrameworkPrincipalBearer
// @Produce json
// @Param correlation_id path string true "Approved flow ID"
// @Param proposal_id path string true "Published proposal SHA-256"
// @Success 200 {object} model.AIAppOptimizationReview
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/optimizations/{proposal_id} [get]
func (h restHandler) RestReviewAIAppOptimization(c echo.Context) error {
	access, failure := h.authorizeOptimization(c, "optimize_inference.read")
	if failure != nil {
		return failure.respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	if saved, failure := h.readOptimizationApproval(access); failure != nil {
		return failure.respond(c)
	} else if saved != nil {
		h.refreshOptimizationApproval(ctx, access, saved)
		return c.JSON(http.StatusOK, model.AIAppOptimizationReview{
			ProposalID: access.id, Proposal: saved.Proposal, Reason: "already_approved",
			Next: &saved.Request, Approval: &saved.AIAppOptimizationApproval,
		})
	}
	review, failure := h.reviewOptimization(ctx, access)
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, review)
}

// RestApproveAIAppOptimization godoc
// @ID ApproveAIAppOptimization
// @Summary Approve a reviewed model proposal and queue only its vLLM setting change
// @Description The original approver can repeat the digest to resume an exact revision after AI-App confirms that revision is pending.
// @Tags AI-App Integration
// @Security FrameworkPrincipalBearer
// @Accept json
// @Produce json
// @Param correlation_id path string true "Approved flow ID"
// @Param proposal_id path string true "Published proposal SHA-256"
// @Param request body model.AIAppOptimizationApprovalRequest true "Exact reviewed proposal digest"
// @Success 202 {object} model.AIAppOptimizationApproval
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/optimizations/{proposal_id}/approve [post]
func (h restHandler) RestApproveAIAppOptimization(c echo.Context) error {
	access, failure := h.authorizeOptimization(c, "optimize_inference.approve")
	if failure != nil {
		return failure.respond(c)
	}
	var req model.AIAppOptimizationApprovalRequest
	if err := decodeFocusedRequest(c, &req); err != nil || req.Digest != access.id {
		return focusedError(c, http.StatusBadRequest, "INVALID_OPTIMIZATION_APPROVAL", "Confirm the exact reviewed proposal digest.", nil)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	saved, failure := h.approveOptimization(ctx, access)
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusAccepted, saved)
}

func (h restHandler) authorizeOptimization(c echo.Context, action string) (optimizationAccess, *aiAppIntegrationFailure) {
	principal, failure := h.frameworkPrincipal(c)
	if failure != nil {
		return optimizationAccess{}, failure
	}
	if !principal.allows(aiAppEngineName, "AI-App", h.config.AIAppNamespace, action) {
		return optimizationAccess{}, deniedFrameworkAction()
	}
	id, flow := c.Param("proposal_id"), c.Param("correlation_id")
	if !aiAppEvidenceDigest.MatchString(id) || !aiAppNodeID.MatchString(flow) {
		return optimizationAccess{}, &aiAppIntegrationFailure{
			status: http.StatusBadRequest, code: "INVALID_OPTIMIZATION_REFERENCE",
			message: "Supply an approved flow ID and a published proposal digest.",
		}
	}
	if !h.config.aiAppApprovalCredentialsConfigured() {
		return optimizationAccess{}, &aiAppIntegrationFailure{status: http.StatusServiceUnavailable,
			code: "AIAPP_OPTIMIZATION_UNAVAILABLE", message: "The AI-App approval credential is not configured."}
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil {
		return optimizationAccess{}, aiAppEngineUnavailable(err)
	}
	return optimizationAccess{principal: principal, engine: engine, flow: flow, id: id}, nil
}

func (h restHandler) reviewOptimization(ctx context.Context, access optimizationAccess) (*model.AIAppOptimizationReview, *aiAppIntegrationFailure) {
	raw, err := h.callAIAppApprovalTool(ctx, access.engine, aiAppOptimizationReadTool, "read",
		map[string]any{"proposalId": access.id, "checkCurrent": true})
	if errors.Is(err, errAIAppToolRejected) {
		return nil, &aiAppIntegrationFailure{status: http.StatusNotFound, code: "OPTIMIZATION_PROPOSAL_NOT_FOUND",
			message: "The published proposal is unavailable for this flow."}
	}
	if err != nil {
		return nil, intentChangePartnerFailure(err)
	}
	var review model.AIAppOptimizationReview
	var proposal struct {
		SchemaVersion string                  `json:"schemaVersion"`
		Observation   optimizationObservation `json:"observation"`
		Decision      struct {
			Action              string `json:"action"`
			DataKind            string `json:"dataKind"`
			HoldoutPassed       bool   `json:"holdoutPassed"`
			CurrentMaxNumSeqs   int    `json:"currentMaxNumSeqs"`
			CandidateMaxNumSeqs int    `json:"candidateMaxNumSeqs"`
		} `json:"decision"`
	}
	if json.Unmarshal(raw, &review) != nil || review.ProposalID != access.id || review.Next != nil || review.Approval != nil ||
		json.Unmarshal(review.Proposal, &proposal) != nil || proposal.SchemaVersion != "khu-inference-optimization/v1" {
		return nil, intentChangePartnerFailure(errors.New("AI-App returned an invalid optimization proposal"))
	}
	o, d := proposal.Observation, proposal.Decision
	if o.CorrelationID != access.flow || o.Placement.Namespace != access.engine.Namespace {
		return nil, deniedFrameworkAction()
	}
	if !review.Eligible {
		return &review, nil
	}
	if d.Action != "UPDATE" || d.DataKind != "measurement" || !d.HoldoutPassed || o.RuntimeID != "vllm" ||
		d.CurrentMaxNumSeqs < 1 || d.CurrentMaxNumSeqs != o.MaxNumSeqs || d.CandidateMaxNumSeqs < 1 ||
		d.CandidateMaxNumSeqs == d.CurrentMaxNumSeqs || !aiAppEvidenceDigest.MatchString(o.ObservationID) {
		return nil, intentChangePartnerFailure(errors.New("AI-App did not confirm a qualified measurement update"))
	}
	before, err := loadJSON[aiAppApplyApproval](h.applyApprovalPath(access.flow, o.Revision))
	if err != nil || before.Request.Revision != o.Revision || !validApplyRequest(before.Request, access.engine.Namespace) {
		return nil, optimizationConflict("The currently approved AI-App revision is unavailable.")
	}
	inf, app := before.Request.Inference.Inference, before.Request.Application.App
	if !inf.GPU || inf.RuntimeID != o.RuntimeID || inf.ModelID != o.ModelID || inf.ArtifactID != o.ArtifactID ||
		app.Name != o.ApplicationName || app.Version != o.ApplicationVersion || len(inf.Args) != 2 {
		return nil, optimizationConflict("The proposal no longer matches the approved runtime and application.")
	}
	current, err := strconv.Atoi(inf.Args[1])
	if err != nil || current != d.CurrentMaxNumSeqs {
		return nil, optimizationConflict("The current vLLM setting differs from the proposed baseline.")
	}
	prior, err := h.readAppliedRevision(ctx, access.engine, access.flow, before.Request)
	if err != nil || !o.matches(prior) {
		return nil, optimizationConflict("The proposal does not match the settled inference and application deployments.")
	}
	next := before.Request
	next.Revision, next.PreviousRevision = o.Revision+1, o.Revision
	inference := *inf
	inference.Args = []string{"--max-num-seqs", strconv.Itoa(d.CandidateMaxNumSeqs)}
	next.Inference.Inference = &inference
	if !validApplyRequest(next, access.engine.Namespace) {
		return nil, optimizationConflict("The proposal does not produce a valid next revision.")
	}
	review.Next = &next
	return &review, nil
}

func (h restHandler) approveOptimization(ctx context.Context, access optimizationAccess) (*model.AIAppOptimizationApproval, *aiAppIntegrationFailure) {
	if saved, failure := h.readOptimizationApproval(access); failure != nil {
		return nil, failure
	} else if saved != nil {
		if h.refreshOptimizationApproval(ctx, access, saved) && saved.ApprovedBy == access.principal.Name {
			saved.Receipt = h.resumePendingAIAppApply(ctx, access.engine, access.flow, saved.Request, saved.Receipt)
			saved.Status = saved.Receipt.Status
		}
		return &saved.AIAppOptimizationApproval, nil
	}
	review, failure := h.reviewOptimization(ctx, access)
	if failure != nil {
		return nil, failure
	}
	if !review.Eligible || review.Next == nil {
		return nil, optimizationConflict("This proposal has no eligible update for the current observation.")
	}
	record := optimizationApprovalRecord{
		AIAppOptimizationApproval: model.AIAppOptimizationApproval{
			ProposalID: access.id, CorrelationID: access.flow, ApprovedBy: access.principal.Name,
			ApprovedAt: time.Now().UTC(), Status: "confirmation_pending", Request: *review.Next,
		},
		Proposal: review.Proposal, EngineDigest: aiAppEngineDigest(access.engine),
	}
	path := h.optimizationPath(access, "approval.json")
	if err := saveJournalRecord(path, record, true); err != nil {
		if errors.Is(err, os.ErrExist) {
			saved, failure := h.readOptimizationApproval(access)
			if failure != nil {
				return nil, failure
			}
			if saved != nil {
				return &saved.AIAppOptimizationApproval, nil
			}
		}
		return nil, intentChangeStorage(err)
	}
	receipt, failure := h.queueAIAppApply(ctx, access.engine, access.flow, record.Request)
	if failure == nil {
		record.Receipt, record.Status = receipt, receipt.Status
	} else {
		log.Warn().Str("proposal_id", access.id).Str("code", failure.code).Msg("Optimization apply could not be confirmed")
		if failure.status == http.StatusConflict {
			record.Status = "rejected"
		}
	}
	if err := saveJournalRecord(path, record, false); err != nil {
		return nil, intentChangeStorage(err)
	}
	return &record.AIAppOptimizationApproval, nil
}

func (h restHandler) readOptimizationApproval(access optimizationAccess) (*optimizationApprovalRecord, *aiAppIntegrationFailure) {
	saved, err := loadJSON[optimizationApprovalRecord](h.optimizationPath(access, "approval.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil || saved.ProposalID != access.id || saved.CorrelationID != access.flow || saved.ApprovedAt.IsZero() ||
		!validApplyRequest(saved.Request, access.engine.Namespace) || !json.Valid(saved.Proposal) {
		return nil, intentChangeStorage(err)
	}
	return &saved, nil
}

func (h restHandler) refreshOptimizationApproval(ctx context.Context, access optimizationAccess, record *optimizationApprovalRecord) bool {
	if (record.Status != "queued" && record.Status != "confirmation_pending") || record.EngineDigest != aiAppEngineDigest(access.engine) {
		return false
	}
	receipt, err := h.readSavedAppliedRevision(ctx, access.engine, access.flow, record.Request)
	if err != nil {
		return false
	}
	record.Receipt, record.Status = receipt, receipt.Status
	return true
}

func (h restHandler) optimizationPath(access optimizationAccess, files ...string) string {
	parts := []string{"runs", "aiapp-optimizations", access.flow, access.id}
	return h.config.path(append(parts, files...)...)
}

func optimizationConflict(message string) *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusConflict, code: "AIAPP_OPTIMIZATION_CONFLICT", message: message}
}
