package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"time"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"

	"kyunghee-aiops/service-control-api/internal/model"
)

const aiAppIntentChangesPath = "/api/v1/integrations/ai-app/intent-changes"

type AIAppIntentChangeRequest struct {
	CorrelationID  string                   `json:"correlation_id,omitempty"`
	Apply          *model.AIAppApplyRequest `json:"apply,omitempty"`
	DeploymentType string                   `json:"deployment_type,omitempty" enums:"app,inference"`
}

type AIAppIntentChangePlan struct {
	RunID            string                   `json:"run_id"`
	Namespace        string                   `json:"namespace"`
	Agent            string                   `json:"agent"`
	EngineVersion    string                   `json:"engine_version"`
	Action           string                   `json:"action"`
	Tool             string                   `json:"tool"`
	ControlAction    string                   `json:"control_action,omitempty"`
	DeploymentID     string                   `json:"deployment_id,omitempty"`
	Target           model.AIAppNodeTarget    `json:"target"`
	DeploymentStatus string                   `json:"deployment_status,omitempty"`
	Request          AIAppIntentChangeRequest `json:"request"`
	PreparedBy       string                   `json:"prepared_by"`
	PreparedAt       time.Time                `json:"prepared_at"`
	PolicyDigest     string                   `json:"policy_digest"`
	EngineDigest     string                   `json:"engine_digest"`
	Digest           string                   `json:"digest"`
}

type AIAppIntentApprovalRequest struct {
	Digest             string `json:"digest"`
	ConfirmTermination bool   `json:"confirm_termination,omitempty"`
}

type AIAppIntentChangeApproval struct {
	Digest           string                   `json:"digest"`
	ApprovedBy       string                   `json:"approved_by"`
	ApprovedAt       time.Time                `json:"approved_at"`
	Status           string                   `json:"status"`
	Apply            *model.AIAppApplyReceipt `json:"apply,omitempty"`
	EngineRunID      string                   `json:"engine_run_id,omitempty"`
	DeploymentID     string                   `json:"deployment_id,omitempty"`
	DeploymentStatus string                   `json:"deployment_status,omitempty"`
}

type AIAppIntentChange struct {
	Plan     AIAppIntentChangePlan      `json:"plan"`
	Approval *AIAppIntentChangeApproval `json:"approval,omitempty"`
}

func registerAIAppIntentChangeRoutes(server *echo.Echo, h restHandler) {
	server.POST(aiAppIntentChangesPath+"/:run_id/prepare", h.RestPrepareAIAppIntentChange)
	server.POST(aiAppIntentChangesPath+"/:run_id/approve", h.RestApproveAIAppIntentChange)
	server.GET(aiAppIntentChangesPath+"/:run_id", h.RestGetAIAppIntentChange)
}

// RestPrepareAIAppIntentChange godoc
// @ID PrepareAIAppIntentChange
// @Summary Bind a saved natural-language proposal to an exact AI-App change
// @Description Check the principal grant, registered engine, target, action and guarded deployment revision. Save a plan for operator review.
// @Tags AI-App Integration
// @Security FrameworkPrincipalBearer
// @Accept json
// @Produce json
// @Param run_id path string true "AI-Ops intent run ID"
// @Param request body AIAppIntentChangeRequest true "Exact revision or deployment type"
// @Success 200 {object} AIAppIntentChangePlan
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/integrations/ai-app/intent-changes/{run_id}/prepare [post]
func (h restHandler) RestPrepareAIAppIntentChange(c echo.Context) error {
	principal, failure := h.frameworkPrincipal(c)
	if failure != nil {
		return failure.respond(c)
	}
	var req AIAppIntentChangeRequest
	if err := decodeFocusedRequest(c, &req); err != nil {
		return intentChangeInvalid("Supply one valid change request.").respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	plan, failure := h.prepareAIAppIntentChange(ctx, principal, c.Param("run_id"), req)
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, plan)
}

// RestApproveAIAppIntentChange godoc
// @ID ApproveAIAppIntentChange
// @Summary Approve the reviewed AI-App change and dispatch its exact MCP input
// @Description The original approver can repeat the digest to resume an exact revision after AI-App confirms that revision is pending.
// @Tags AI-App Integration
// @Security FrameworkPrincipalBearer
// @Accept json
// @Produce json
// @Param run_id path string true "AI-Ops intent run ID"
// @Param request body AIAppIntentApprovalRequest true "Reviewed plan digest and explicit VM termination confirmation"
// @Success 202 {object} AIAppIntentChangeApproval
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/integrations/ai-app/intent-changes/{run_id}/approve [post]
func (h restHandler) RestApproveAIAppIntentChange(c echo.Context) error {
	principal, failure := h.frameworkPrincipal(c)
	if failure != nil {
		return failure.respond(c)
	}
	var req AIAppIntentApprovalRequest
	if err := decodeFocusedRequest(c, &req); err != nil {
		return intentChangeInvalid("Supply the reviewed plan digest.").respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	approval, failure := h.approveAIAppIntentChange(ctx, principal, c.Param("run_id"), req)
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusAccepted, approval)
}

// RestGetAIAppIntentChange godoc
// @ID GetAIAppIntentChange
// @Summary Read a reviewed AI-App plan and its approval receipt
// @Tags AI-App Integration
// @Security FrameworkPrincipalBearer
// @Produce json
// @Param run_id path string true "AI-Ops intent run ID"
// @Success 200 {object} AIAppIntentChange
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/integrations/ai-app/intent-changes/{run_id} [get]
func (h restHandler) RestGetAIAppIntentChange(c echo.Context) error {
	principal, failure := h.frameworkPrincipal(c)
	if failure != nil {
		return failure.respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	change, failure := h.getAIAppIntentChange(ctx, principal, c.Param("run_id"))
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, change)
}

func (h restHandler) prepareAIAppIntentChange(ctx context.Context, principal *frameworkPrincipal, id string, req AIAppIntentChangeRequest) (*AIAppIntentChangePlan, *aiAppIntegrationFailure) {
	run, failure := h.getAIAppIntentRun(id)
	if failure != nil {
		return nil, failure
	}
	if run.Source != aiAppIntentSource || run.Kind != "intent" || run.Status != "blocked" || len(run.Steps) != 1 ||
		run.Steps[0].Executed || run.Steps[0].Grade != "write" || run.OpsLLM == nil ||
		run.OpsLLM.RunID == "" || run.OpsLLM.Status != "completed" || run.OpsLLM.Proposal == nil {
		return nil, intentChangeConflict("The intent has no specific pending write proposal.")
	}
	proposal := run.OpsLLM.Proposal
	tool := proposal.Action
	if tool == "update_application" {
		tool = "deploy_application"
	}
	if step := run.Steps[0]; step.Tool != tool || step.Input["deploymentId"] != proposal.DeploymentID ||
		step.Input["action"] != proposal.ControlAction {
		return nil, intentChangeConflict("The saved tool proposal and its arguments disagree.")
	}
	plan := AIAppIntentChangePlan{
		RunID: id, Namespace: run.Namespace, Agent: aiAppEngineName, Action: proposal.Action,
		ControlAction: proposal.ControlAction, DeploymentID: proposal.DeploymentID, Request: req,
		PreparedAt: time.Now().UTC(), PreparedBy: principal.Name, PolicyDigest: h.service.frameworkAccess.digest,
	}
	if !principal.allows(plan.Agent, "AI-App", plan.Namespace, plan.permission()) {
		return nil, deniedFrameworkAction()
	}
	path := h.intentChangePath(id, "plan")
	saved, err := loadJSON[AIAppIntentChangePlan](path)
	if err == nil {
		if saved.validDigest(id) && reflect.DeepEqual(saved.Request, req) &&
			saved.Action == plan.Action && saved.DeploymentID == plan.DeploymentID && saved.ControlAction == plan.ControlAction {
			return &saved, nil
		}
		return nil, intentChangeConflict("This intent already has a different reviewed plan. Submit a new intent to change it.")
	}
	if !errors.Is(err, os.ErrNotExist) {
		return nil, intentChangeStorage(err)
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil {
		return nil, aiAppEngineUnavailable(err)
	}
	plan.EngineVersion = engine.Card.Version
	plan.EngineDigest = aiAppEngineDigest(engine)
	if failure := h.bindAIAppIntentChange(ctx, engine, &plan); failure != nil {
		return nil, failure
	}
	plan.Digest = frameworkDigest(plan)
	if plan.Digest == "" {
		return nil, intentChangeStorage(errors.New("could not encode the plan"))
	}
	if err := saveJournalRecord(path, plan, true); err != nil {
		if errors.Is(err, os.ErrExist) {
			return nil, intentChangeConflict("The intent already has a reviewed plan.")
		}
		return nil, intentChangeStorage(err)
	}
	return &plan, nil
}

func (h restHandler) getAIAppIntentChange(ctx context.Context, principal *frameworkPrincipal, id string) (AIAppIntentChange, *aiAppIntegrationFailure) {
	change, failure := h.loadAIAppIntentChange(principal, id)
	if failure != nil || change.Approval == nil {
		return change, failure
	}
	if change.Approval.Status == "queued" || change.Approval.Status == "confirmation_pending" {
		h.refreshAIAppIntentApproval(ctx, &change)
	}
	return change, nil
}

func (h restHandler) loadAIAppIntentChange(principal *frameworkPrincipal, id string) (AIAppIntentChange, *aiAppIntegrationFailure) {
	notFound := &aiAppIntegrationFailure{status: http.StatusNotFound, code: "INTENT_CHANGE_NOT_FOUND", message: "The reviewed intent plan was not found."}
	if !aiAppIntentRunID.MatchString(id) {
		return AIAppIntentChange{}, notFound
	}
	plan, err := loadJSON[AIAppIntentChangePlan](h.intentChangePath(id, "plan"))
	if errors.Is(err, os.ErrNotExist) {
		return AIAppIntentChange{}, notFound
	}
	if err != nil || !plan.validDigest(id) {
		return AIAppIntentChange{}, intentChangeStorage(err)
	}
	if !principal.allows(plan.Agent, "AI-App", plan.Namespace, plan.permission()) {
		return AIAppIntentChange{}, deniedFrameworkAction()
	}
	change := AIAppIntentChange{Plan: plan}
	approval, err := loadJSON[AIAppIntentChangeApproval](h.intentChangePath(id, "approval"))
	if !errors.Is(err, os.ErrNotExist) {
		if err != nil || approval.Digest != plan.Digest {
			return AIAppIntentChange{}, intentChangeStorage(err)
		}
		change.Approval = &approval
	}
	return change, nil
}

func (h restHandler) approveAIAppIntentChange(ctx context.Context, principal *frameworkPrincipal, id string, req AIAppIntentApprovalRequest) (*AIAppIntentChangeApproval, *aiAppIntegrationFailure) {
	change, failure := h.loadAIAppIntentChange(principal, id)
	if failure != nil {
		return nil, failure
	}
	plan := change.Plan
	if req.Digest != plan.Digest || req.ConfirmTermination != (plan.ControlAction == "terminate") {
		return nil, intentChangeConflict("Approval must confirm the reviewed digest and any explicit VM termination.")
	}
	if change.Approval != nil {
		return h.resumeAIAppIntentApply(ctx, principal, change), nil
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil {
		return nil, aiAppEngineUnavailable(err)
	}
	if engine.Namespace != plan.Namespace || plan.EngineDigest != aiAppEngineDigest(engine) || plan.PolicyDigest != h.service.frameworkAccess.digest {
		return nil, intentChangeConflict("The policy or engine registration changed. Prepare a new intent.")
	}
	rechecked := plan
	if failure := h.bindAIAppIntentChange(ctx, engine, &rechecked); failure != nil {
		return nil, failure
	}
	if !reflect.DeepEqual(rechecked, plan) {
		return nil, intentChangeConflict("The deployment or target changed after review. Prepare a new intent.")
	}
	path := h.intentChangePath(id, "approval")
	approval := AIAppIntentChangeApproval{
		Digest: plan.Digest, ApprovedBy: principal.Name, ApprovedAt: time.Now().UTC(),
		Status: "confirmation_pending", DeploymentID: plan.DeploymentID,
	}
	if err := saveJournalRecord(path, approval, true); err != nil {
		if errors.Is(err, os.ErrExist) {
			latest, failure := h.loadAIAppIntentChange(principal, id)
			return latest.Approval, failure
		}
		return nil, intentChangeStorage(err)
	}
	if failure := h.dispatchAIAppIntentChange(ctx, engine, plan, &approval); failure != nil {
		log.Warn().Str("run_id", plan.RunID).Str("code", failure.code).Msg("AI-App change could not be confirmed")
		return &approval, nil
	}
	if err := saveJournalRecord(path, approval, false); err != nil {
		return nil, intentChangeStorage(err)
	}
	return &approval, nil
}

func (h restHandler) resumeAIAppIntentApply(ctx context.Context, principal *frameworkPrincipal, change AIAppIntentChange) *AIAppIntentChangeApproval {
	plan, approval := change.Plan, change.Approval
	req := plan.Request.Apply
	if req == nil || approval.ApprovedBy != principal.Name || plan.PolicyDigest != h.service.frameworkAccess.digest ||
		(approval.Status != "queued" && approval.Status != "confirmation_pending") {
		return approval
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil || plan.EngineDigest != aiAppEngineDigest(engine) || !engine.advertisesWrite("deploy", "deploy_application") {
		return approval
	}
	receipt, err := h.readSavedAppliedRevision(ctx, engine, plan.Request.CorrelationID, *req)
	if err == nil {
		receipt = h.resumePendingAIAppApply(ctx, engine, plan.Request.CorrelationID, *req, receipt)
		approval.Apply, approval.Status = receipt, receipt.Status
	}
	return approval
}

func (h restHandler) intentChangePath(id, filename string) string {
	return h.config.path("runs", "aiapp-intent-changes", id, filename+".json")
}

func (p AIAppIntentChangePlan) permission() string {
	if p.Action == "control_application" {
		return p.Action + "." + p.ControlAction
	}
	return p.Action
}

func (p AIAppIntentChangePlan) validDigest(id string) bool {
	digest := p.Digest
	p.Digest = ""
	return p.RunID == id && p.Agent == aiAppEngineName && digest != "" && digest == frameworkDigest(p)
}

func frameworkDigest(value any) string {
	raw, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	hash := sha256.Sum256(raw)
	return hex.EncodeToString(hash[:])
}

func intentChangeInvalid(message string) *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusBadRequest, code: "INVALID_INTENT_CHANGE", message: message}
}

func intentChangeConflict(message string) *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusConflict, code: "INTENT_CHANGE_CONFLICT", message: message}
}

func intentChangeStorage(err error) *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusInternalServerError, code: "INTENT_CHANGE_STORE_UNAVAILABLE",
		message: "The intent change record could not be read or saved.", cause: err}
}
