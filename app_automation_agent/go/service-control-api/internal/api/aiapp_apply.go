package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/model"
)

var aiAppEnvName = regexp.MustCompile(`^[A-Z_][A-Z0-9_]{0,127}$`)

type aiAppApplyApproval struct {
	ApprovedAt time.Time               `json:"approved_at"`
	Request    model.AIAppApplyRequest `json:"request"`
}

// RestPostAIAppApply godoc
// @ID ApplyApprovedAIAppRevision
// @Summary Approve and queue a live AI-App revision
// @Description Submit a pinned single-node inference and application through the registered AI-App MCP server. Revision 1 requires the exact staged inference and planned application. Later revisions use the same placement.
// @Tags AI-App Integration
// @Security AutonomyAdminToken
// @Accept json
// @Produce json
// @Param correlation_id path string true "Approved flow ID"
// @Param request body model.AIAppApplyRequest true "Approved revision and desired deployments"
// @Success 202 {object} model.AIAppApplyReceipt
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/apply [post]
func (h restHandler) RestPostAIAppApply(c echo.Context) error {
	if failure := h.authorizeAIAppApproval(c); failure != nil {
		return failure.respond(c)
	}
	id := c.Param("correlation_id")
	var req model.AIAppApplyRequest
	if err := decodeFocusedRequest(c, &req); err != nil || !aiAppNodeID.MatchString(id) || !validApplyRequest(req, h.config.AIAppNamespace) {
		return focusedError(c, http.StatusBadRequest, "INVALID_REQUEST", "Supply the exact revision and one scoped inference/application pair.", nil)
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil {
		return aiAppEngineUnavailable(err).respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	receipt, failure := h.queueAIAppApply(ctx, engine, id, req)
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusAccepted, receipt)
}

func (h restHandler) queueAIAppApply(ctx context.Context, engine *registeredAIAppEngine, id string, req model.AIAppApplyRequest) (*model.AIAppApplyReceipt, *aiAppIntegrationFailure) {
	if failure := h.recordApplyApproval(ctx, engine, id, req); failure != nil {
		return nil, failure
	}
	body := struct {
		CorrelationID string `json:"correlationId"`
		model.AIAppApplyRequest
	}{id, req}
	receipt, err := h.applyReceipt(ctx, engine, id, req, aiAppApplyTool, "write", body)
	if err != nil {
		return nil, aiAppApplyCallFailure(err)
	}
	return receipt, nil
}

// RestGetAIAppApply godoc
// @ID GetAppliedAIAppRevision
// @Summary Read the engine and deployment IDs of an approved revision
// @Tags AI-App Integration
// @Security AutonomyAdminToken
// @Produce json
// @Param correlation_id path string true "Approved flow ID"
// @Param revision path int true "Revision"
// @Success 200 {object} model.AIAppApplyReceipt
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/apply/{revision} [get]
func (h restHandler) RestGetAIAppApply(c echo.Context) error {
	if failure := h.authorizeAIAppApproval(c); failure != nil {
		return failure.respond(c)
	}
	id := c.Param("correlation_id")
	revision, err := strconv.Atoi(c.Param("revision"))
	if err != nil || revision < 1 || !aiAppNodeID.MatchString(id) {
		return focusedError(c, http.StatusBadRequest, "INVALID_REQUEST", "Supply an approved flow and revision.", nil)
	}
	approved, err := loadJSON[aiAppApplyApproval](h.applyApprovalPath(id, revision))
	if err != nil || approved.Request.Revision != revision || !validApplyRequest(approved.Request, h.config.AIAppNamespace) {
		return focusedError(c, http.StatusNotFound, "AIAPP_APPLY_NOT_FOUND", "The approved revision was not found.", nil)
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil {
		return aiAppEngineUnavailable(err).respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()
	receipt, err := h.readAppliedRevision(ctx, engine, id, approved.Request)
	if err != nil {
		return aiAppApplyCallFailure(err).respond(c)
	}
	return c.JSON(http.StatusOK, receipt)
}

func validApplyRequest(req model.AIAppApplyRequest, nsID string) bool {
	inf, app := req.Inference, req.Application
	if req.Revision < 1 || req.PreviousRevision != req.Revision-1 || inf.Type != "inference" || app.Type != "app" ||
		inf.Inference == nil || inf.App != nil || app.App == nil || app.Inference != nil || app.App.Version < 1 ||
		!validAIAppPlacementReview(inf.PlacementReview, nsID) || !reflect.DeepEqual(inf.PlacementReview, app.PlacementReview) ||
		len(inf.Targets) != 1 || len(app.Targets) != 1 || inf.Targets[0] != app.Targets[0] ||
		inf.Targets[0].NsID != nsID || inf.Targets[0].Kind != "node" ||
		!aiAppNodeID.MatchString(inf.Targets[0].InfraID) || !aiAppNodeID.MatchString(inf.Targets[0].NodeID) ||
		!aiAppCatalogID.MatchString(inf.Inference.ModelID) || !aiAppCatalogID.MatchString(inf.Inference.ArtifactID) ||
		!aiAppCatalogID.MatchString(inf.Inference.RuntimeID) || !aiAppName.MatchString(app.App.Name) {
		return false
	}
	b := app.App.InferenceBinding
	if b == nil || !aiAppEnvName.MatchString(b.BaseURLEnv) || b.BaseURLEnv == b.APIKeyEnv ||
		(b.APIKeyEnv != "" && !aiAppEnvName.MatchString(b.APIKeyEnv)) || (b.Native && inf.Inference.RuntimeID != "ollama") {
		return false
	}
	for name, ref := range app.App.EnvFrom {
		if !aiAppEnvName.MatchString(name) || !aiAppEnvName.MatchString(ref) || !strings.HasPrefix(ref, "AIAPP_DEPLOY_SECRET_") ||
			name == b.BaseURLEnv || name == b.APIKeyEnv {
			return false
		}
	}
	_, err := parseAIAppMaxNumSeqs(inf.Inference)
	return err == nil
}

func (h restHandler) applyApprovalPath(id string, revision int) string {
	return h.config.path("runs", "aiapp-apply", id, strconv.Itoa(revision)+".json")
}

func (h restHandler) recordApplyApproval(ctx context.Context, engine *registeredAIAppEngine, id string, req model.AIAppApplyRequest) *aiAppIntegrationFailure {
	path := h.applyApprovalPath(id, req.Revision)
	record, err := loadJSON[aiAppApplyApproval](path)
	if err == nil {
		oldBody, oldErr := json.Marshal(record.Request)
		newBody, newErr := json.Marshal(req)
		if oldErr != nil || newErr != nil || !bytes.Equal(oldBody, newBody) {
			return &aiAppIntegrationFailure{status: http.StatusConflict, code: "AIAPP_APPROVAL_CONFLICT", message: "This revision already has a different approved body."}
		}
		return nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return &aiAppIntegrationFailure{status: http.StatusInternalServerError, code: "AIAPP_APPROVAL_READ_FAILED", message: "The approved revision could not be read.", cause: err}
	}
	if req.Revision == 1 {
		err = h.checkInitialApply(id, req)
	} else {
		previous, readErr := loadJSON[aiAppApplyApproval](h.applyApprovalPath(id, req.PreviousRevision))
		if readErr != nil || previous.Request.Revision != req.PreviousRevision || !validApplyRequest(previous.Request, engine.Namespace) ||
			previous.Request.Inference.Targets[0] != req.Inference.Targets[0] ||
			!reflect.DeepEqual(previous.Request.Inference.PlacementReview, req.Inference.PlacementReview) ||
			previous.Request.Inference.Inference.RuntimeID != req.Inference.Inference.RuntimeID ||
			previous.Request.Application.App.Name != req.Application.App.Name {
			err = errors.New("the previous approved revision has a different placement")
		} else {
			prior, readErr := h.readAppliedRevision(ctx, engine, id, previous.Request)
			if readErr != nil || (prior.Status != "succeeded" && prior.Status != "failed") {
				err = errors.New("the previous revision has not settled")
			} else {
				err = nil
			}
		}
	}
	if err != nil {
		return &aiAppIntegrationFailure{status: http.StatusConflict, code: "AIAPP_APPROVAL_CONFLICT", message: "The initial stage or previous revision does not authorize this placement.", cause: err}
	}
	if err := saveJournalRecord(path, aiAppApplyApproval{ApprovedAt: time.Now().UTC(), Request: req}, true); err != nil {
		return &aiAppIntegrationFailure{status: http.StatusInternalServerError, code: "AIAPP_APPROVAL_SAVE_FAILED", message: "The approved revision could not be saved.", cause: err}
	}
	return nil
}

func (h restHandler) checkInitialApply(id string, req model.AIAppApplyRequest) error {
	stage, err := loadJSON[aiAppStageRecord](h.config.path("runs", "aiapp-stage", id+".json"))
	if err != nil || stage.Receipt == nil || stage.Receipt.Status != "blocked" || stage.Request.Revision != 1 ||
		!reflect.DeepEqual(stage.Request.Deployment, req.Inference) {
		return errors.New("the initial inference request must match the recorded dry-run")
	}
	want := stage.PlannedApp
	if want == nil {
		// Older dry-run receipts predate the saved app projection.
		if err := h.service.agentControl.WithApprovedInitialFlow(id, func(flow agentcontrol.Flow) error {
			prepared, err := prepareAIAppInitialFlow(flow, model.AIAppPreparationRequest{
				ModelID: req.Inference.Inference.ModelID, ArtifactID: req.Inference.Inference.ArtifactID,
				PlacementReview: req.Inference.PlacementReview,
			})
			if err == nil {
				want = &prepared.Deployments[1]
			}
			return err
		}); err != nil {
			return err
		}
	}
	app := req.Application
	app.App = &model.AIAppApplication{Name: app.App.Name, Version: app.App.Version}
	if !reflect.DeepEqual(*want, app) {
		return errors.New("the application version or target differs from the approved plan")
	}
	return nil
}

func (h restHandler) readAppliedRevision(ctx context.Context, engine *registeredAIAppEngine, id string, req model.AIAppApplyRequest) (*model.AIAppApplyReceipt, error) {
	return h.applyReceipt(ctx, engine, id, req, aiAppApplyReadTool, "read", map[string]any{"correlationId": id, "revision": req.Revision})
}

func (h restHandler) readSavedAppliedRevision(ctx context.Context, engine *registeredAIAppEngine, id string, req model.AIAppApplyRequest) (*model.AIAppApplyReceipt, error) {
	saved, err := loadJSON[aiAppApplyApproval](h.applyApprovalPath(id, req.Revision))
	if err != nil {
		return nil, err
	}
	if !reflect.DeepEqual(saved.Request, req) || !validApplyRequest(req, engine.Namespace) {
		return nil, errors.New("the saved apply request differs from the reviewed revision")
	}
	return h.readAppliedRevision(ctx, engine, id, req)
}

func (h restHandler) resumePendingAIAppApply(ctx context.Context, engine *registeredAIAppEngine, id string, req model.AIAppApplyRequest, receipt *model.AIAppApplyReceipt) *model.AIAppApplyReceipt {
	if receipt.Status != "confirmation_pending" {
		return receipt
	}
	resumed, failure := h.queueAIAppApply(ctx, engine, id, req)
	if failure != nil {
		log.Warn().Str("correlation_id", id).Int("revision", req.Revision).Str("code", failure.code).
			Msg("AI-App revision recovery could not be confirmed")
		return receipt
	}
	return resumed
}

func (h restHandler) applyReceipt(ctx context.Context, engine *registeredAIAppEngine, id string, req model.AIAppApplyRequest, tool, grade string, input any) (*model.AIAppApplyReceipt, error) {
	raw, err := h.callAIAppApprovalTool(ctx, engine, tool, grade, input)
	if err != nil {
		return nil, err
	}
	var r model.AIAppApplyReceipt
	target := req.Inference.Targets[0]
	if err := json.Unmarshal(raw, &r); err != nil || r.CorrelationID != id || r.Revision != req.Revision ||
		r.PreviousRevision != req.PreviousRevision || r.Namespace != engine.Namespace ||
		r.Target != target.NsID+"/"+target.InfraID+"/"+target.NodeID || r.UpdatedAt.IsZero() {
		return nil, errors.New("the apply receipt does not match the approval")
	}
	switch r.Status {
	case "succeeded":
		for _, step := range []model.AIAppApplyStep{r.Inference, r.Application} {
			if step.Status != "succeeded" || step.EngineRunID == "" || !aiAppDeploymentID.MatchString(step.DeploymentID) {
				return nil, errors.New("a completed apply receipt is missing a deployment")
			}
		}
	case "queued", "failed", "confirmation_pending":
	default:
		return nil, errors.New("AI-App returned an invalid apply status")
	}
	return &r, nil
}

func aiAppApplyCallFailure(err error) *aiAppIntegrationFailure {
	if errors.Is(err, errAIAppToolRejected) {
		return &aiAppIntegrationFailure{status: http.StatusConflict, code: "AIAPP_APPLY_REJECTED", message: "AI-App rejected the revision or its placement."}
	}
	return &aiAppIntegrationFailure{status: http.StatusBadGateway, code: "AIAPP_APPLY_UNCONFIRMED", message: "Check the saved revision or repeat the exact approval.", cause: err}
}
