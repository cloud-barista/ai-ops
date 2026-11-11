package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strings"
	"time"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/model"
)

const (
	aiAppApprovalMCPPath = "/aiapp/engine/automation/mcp"
	aiAppApprovalServer  = "ai-mcmp-app-approved-deployment"
	aiAppStageTool       = "stage_approved_deployment"
	aiAppApplyTool       = "apply_approved_revision"
	aiAppApplyReadTool   = "get_applied_revision"
)

type aiAppStageRecord struct {
	ApprovedAt time.Time                  `json:"approved_at"`
	Request    model.AIAppStageRequest    `json:"request"`
	PlannedApp *model.AIAppNodeDeployment `json:"planned_app,omitempty"`
	Receipt    *model.AIAppStageReceipt   `json:"receipt,omitempty"`
}

// RestPostAIAppStage godoc
// @ID StageApprovedAIAppDeployment
// @Summary Approve and stage an exact initial AI-App inference request
// @Description Bind an operator-approved request to the existing guarded initial Revision. Stage the single-node request through the dedicated AI-App MCP tool in dry-run.
// @Tags AI-App Integration
// @Security AutonomyAdminToken
// @Accept json
// @Produce json
// @Param correlation_id path string true "Approved flow ID"
// @Param request body model.AIAppStageRequest true "Exact initial inference request from prepare"
// @Success 200 {object} model.AIAppStageReceipt
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/stage [post]
func (h restHandler) RestPostAIAppStage(c echo.Context) error {
	if failure := h.authorizeAIAppApproval(c); failure != nil {
		return failure.respond(c)
	}
	id := c.Param("correlation_id")
	var request model.AIAppStageRequest
	if err := decodeFocusedRequest(c, &request); err != nil || !aiAppNodeID.MatchString(id) ||
		request.Revision < 1 || request.Deployment.Inference == nil {
		return focusedError(c, http.StatusBadRequest, "INVALID_REQUEST", "Supply a flow ID, revision and prepared inference request.", nil)
	}
	inference := request.Deployment.Inference
	if !aiAppCatalogID.MatchString(inference.ModelID) || !aiAppCatalogID.MatchString(inference.ArtifactID) {
		return focusedError(c, http.StatusBadRequest, "INVALID_AIAPP_CATALOG_IDS", "Supply valid AI-App model and artifact IDs.", nil)
	}
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil {
		return aiAppEngineUnavailable(err).respond(c)
	}
	ctx, cancel := context.WithTimeout(c.Request().Context(), h.config.AgentExecutionTimeout)
	defer cancel()

	var receipt *model.AIAppStageReceipt
	// shortcut: the existing flow lock keeps the approval stable during this
	// bounded dry-run call, live apply uses a separate revision reservation.
	err = h.service.agentControl.WithApprovedInitialFlow(id, func(flow agentcontrol.Flow) error {
		maxNumSeqs, err := parseAIAppMaxNumSeqs(inference)
		if err != nil {
			return err
		}
		prepared, err := prepareAIAppInitialFlow(flow, model.AIAppPreparationRequest{
			ModelID: inference.ModelID, ArtifactID: inference.ArtifactID, PlacementReview: request.Deployment.PlacementReview,
			MaxNumSeqs: maxNumSeqs,
		})
		if err != nil {
			return err
		}
		if request.Revision != prepared.Revision || !reflect.DeepEqual(request.Deployment, prepared.Deployments[0]) ||
			request.Deployment.Targets[0].NsID != engine.Namespace {
			return errors.New("request differs from the prepared revision or authorized namespace")
		}
		receipt, err = h.stageAIAppRevision(ctx, engine, id, request, prepared.Deployments[1])
		return err
	})
	if err != nil {
		var failure *aiAppIntegrationFailure
		if errors.As(err, &failure) {
			return failure.respond(c)
		}
		return focusedError(c, http.StatusConflict, "AIAPP_APPROVAL_CONFLICT", "The request must match the approved initial revision and authorized namespace.", err)
	}
	return c.JSON(http.StatusOK, receipt)
}

func (cfg ServerConfig) aiAppApprovalCredentialsConfigured() bool {
	admin := strings.TrimSpace(cfg.AutonomyAdminToken)
	token := cfg.AIAppEngineApprovalToken
	return cfg.aiAppEngineRegistryConfigured() && token != "" && token != admin &&
		token != cfg.AIAppToken && token != cfg.AIAppEngineRegistryToken && token != cfg.OpsLLMToken
}

func (h restHandler) authorizeAIAppApproval(c echo.Context) *aiAppIntegrationFailure {
	admin := strings.TrimSpace(h.config.AutonomyAdminToken)
	if !h.config.aiAppApprovalCredentialsConfigured() || admin == "" || admin == h.config.AIAppToken || admin == h.config.OpsLLMToken {
		return &aiAppIntegrationFailure{status: http.StatusServiceUnavailable, code: "AIAPP_STAGE_UNAVAILABLE", message: "Configure separate operator and AI-App approval credentials."}
	}
	if subtle.ConstantTimeCompare([]byte(c.Request().Header.Get(echo.HeaderAuthorization)), []byte("Bearer "+admin)) != 1 {
		return &aiAppIntegrationFailure{status: http.StatusUnauthorized, code: "UNAUTHORIZED", message: "Valid operator authorization is required."}
	}
	return nil
}

func (h restHandler) stageAIAppRevision(ctx context.Context, engine *registeredAIAppEngine, id string, request model.AIAppStageRequest, plannedApp model.AIAppNodeDeployment) (*model.AIAppStageReceipt, error) {
	path := h.config.path("runs", "aiapp-stage", id+".json")
	record, err := loadJSON[aiAppStageRecord](path)
	switch {
	case errors.Is(err, os.ErrNotExist):
		record = aiAppStageRecord{ApprovedAt: time.Now().UTC(), Request: request, PlannedApp: &plannedApp}
		err = saveJournalRecord(path, record, true)
	case err == nil && (!reflect.DeepEqual(record.Request, request) ||
		(record.PlannedApp != nil && !reflect.DeepEqual(*record.PlannedApp, plannedApp))):
		return nil, &aiAppIntegrationFailure{
			status: http.StatusConflict, code: "AIAPP_APPROVAL_CONFLICT", message: "This flow already has a different approved request.",
		}
	case err == nil && record.PlannedApp == nil:
		record.PlannedApp = &plannedApp
		err = saveJournalRecord(path, record, false)
	}
	if err != nil {
		return nil, &aiAppIntegrationFailure{
			status: http.StatusInternalServerError, code: "AIAPP_APPROVAL_SAVE_FAILED", message: "The approved request could not be recorded.", cause: err,
		}
	}
	if record.Receipt != nil && record.Receipt.Status != "confirmation_pending" {
		return record.Receipt, nil
	}
	receipt, err := h.callAIAppStageTool(ctx, engine, id, request)
	if err != nil {
		return nil, &aiAppIntegrationFailure{
			status: http.StatusBadGateway, code: "AIAPP_STAGE_UNCONFIRMED", message: "AI-App staging could not be confirmed. Repeat the same approved request to check its receipt.", cause: err,
		}
	}
	record.Receipt = receipt
	if err := saveJournalRecord(path, record, false); err != nil {
		return nil, &aiAppIntegrationFailure{
			status: http.StatusInternalServerError, code: "AIAPP_RECEIPT_SAVE_FAILED", message: "The AI-App receipt could not be recorded.", cause: err,
		}
	}
	return receipt, nil
}

func (h restHandler) callAIAppStageTool(ctx context.Context, engine *registeredAIAppEngine, id string, request model.AIAppStageRequest) (*model.AIAppStageReceipt, error) {
	result, err := h.callAIAppApprovalTool(ctx, engine, aiAppStageTool, "write", map[string]any{
		"correlationId": id, "revision": request.Revision, "deployment": request.Deployment,
	})
	if err != nil {
		return nil, err
	}
	target := request.Deployment.Targets[0]
	var receipt model.AIAppStageReceipt
	if err := json.Unmarshal(result, &receipt); err != nil ||
		receipt.CorrelationID != id || receipt.Revision != request.Revision || receipt.Namespace != engine.Namespace ||
		receipt.Target != target.NsID+"/"+target.InfraID+"/"+target.NodeID || receipt.UpdatedAt.IsZero() {
		return nil, errors.New("AI-App staging receipt did not match the approved request")
	}
	switch receipt.Status {
	case "failed", "confirmation_pending":
		return &receipt, nil
	case "blocked":
		if receipt.EngineRunID != "" && aiAppDeploymentID.MatchString(receipt.DeploymentID) {
			return &receipt, nil
		}
	}
	return nil, errors.New("AI-App did not return a valid dry-run status")
}

func (h restHandler) callAIAppApprovalTool(ctx context.Context, engine *registeredAIAppEngine, tool, grade string, input any) (json.RawMessage, error) {
	ctx = aiAppEngineContext{ctx}
	session, err := h.connectAIAppMCP(ctx, engine, aiAppApprovalMCPPath, h.config.AIAppEngineApprovalToken)
	if err != nil {
		return nil, err
	}
	defer session.Close()
	initialized := session.InitializeResult()
	if initialized.ServerInfo == nil || initialized.ServerInfo.Name != aiAppApprovalServer ||
		(tool == aiAppStageTool && initialized.Meta["ai-mcmp/dryRun"] != true) {
		return nil, errors.New("AI-App approval server did not advertise the expected identity or dry-run")
	}
	for definition, err := range session.Tools(ctx, nil) {
		if err != nil {
			return nil, err
		}
		if definition.Name == tool && definition.Meta["ai-mcmp/grade"] == grade &&
			(grade != "read" || (definition.Annotations != nil && definition.Annotations.ReadOnlyHint)) {
			return aiAppMCPTextResult(ctx, session, tool, input)
		}
	}
	return nil, errors.New("AI-App approval tool was not advertised")
}
