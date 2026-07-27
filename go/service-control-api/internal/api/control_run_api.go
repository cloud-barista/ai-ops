package api

import (
	"net/http"
	"strings"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/controlrun"
)

// RestPostControlRun godoc
// @ID CreateControlRun
// @Summary Generate a guarded DeploymentManifest
// @Description Create a backend ControlRun, validate the request and Agent Registry authorization, invoke the configured Qwen Planner, and return a Go-validated DeploymentManifest without requiring AppDeploy.
// @Tags AI Application Automation
// @Accept json
// @Produce json
// @Param request body CreateControlRunRequest true "Manifest planning request"
// @Success 201 {object} controlrun.Run
// @Failure 400 {object} controlrun.Run
// @Failure 403 {object} controlrun.Run
// @Failure 422 {object} controlrun.Run
// @Router /api/v1/control-runs [post]
func (handler restHandler) RestPostControlRun(context echo.Context) error {
	var request CreateControlRunRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	run, err := handler.service.CreateControlRun(context.Request().Context(), request)
	if err != nil {
		return context.JSON(controlRunHTTPStatus(run.Status), run)
	}
	return context.JSON(http.StatusCreated, run)
}

// RestGetControlRuns godoc
// @ID ListControlRuns
// @Summary List geon ControlRuns
// @Tags AI Application Automation
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/control-runs [get]
func (handler restHandler) RestGetControlRuns(context echo.Context) error {
	runs := handler.service.ListControlRuns()
	return context.JSON(http.StatusOK, map[string]any{
		"count": len(runs),
		"items": runs,
		"runs":  runs,
	})
}

// RestGetControlRun godoc
// @ID GetControlRun
// @Summary Get one geon ControlRun
// @Tags AI Application Automation
// @Produce json
// @Param run_id path string true "ControlRun ID"
// @Success 200 {object} controlrun.Run
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/control-runs/{run_id} [get]
func (handler restHandler) RestGetControlRun(context echo.Context) error {
	run, ok := handler.service.GetControlRun(context.Param("run_id"))
	if !ok {
		return context.JSON(http.StatusNotFound, ErrorResponse{Valid: false, Message: "ControlRun was not found"})
	}
	return context.JSON(http.StatusOK, run)
}

// RestDeleteControlRun godoc
// @ID DeleteControlRun
// @Summary Delete one geon-owned ControlRun record
// @Description Delete only the in-memory geon ControlRun. This does not delete AppDeploy or cloud resources.
// @Tags AI Application Automation
// @Produce json
// @Param run_id path string true "ControlRun ID"
// @Success 200 {object} controlrun.Run
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/control-runs/{run_id} [delete]
func (handler restHandler) RestDeleteControlRun(context echo.Context) error {
	run, ok := handler.service.DeleteControlRun(context.Param("run_id"))
	if !ok {
		return context.JSON(http.StatusNotFound, ErrorResponse{Valid: false, Message: "ControlRun was not found"})
	}
	return context.JSON(http.StatusOK, run)
}

// RestDeleteControlRuns godoc
// @ID DeleteControlRuns
// @Summary Delete all geon-owned ControlRun records
// @Description Clear only the in-memory geon ControlRun store. This does not delete AppDeploy or cloud resources.
// @Tags AI Application Automation
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/control-runs [delete]
func (handler restHandler) RestDeleteControlRuns(context echo.Context) error {
	deleted := handler.service.ClearControlRuns()
	return context.JSON(http.StatusOK, map[string]any{
		"deleted_count": deleted,
		"items":         []controlrun.Run{},
	})
}

func controlRunHTTPStatus(status controlrun.Status) int {
	switch status {
	case controlrun.StatusRequestRejected:
		return http.StatusBadRequest
	case controlrun.StatusAgentRejected:
		return http.StatusForbidden
	case controlrun.StatusManifestRejected:
		return http.StatusUnprocessableEntity
	default:
		return http.StatusInternalServerError
	}
}

func normalizeControlRunID(runID string) string {
	return strings.TrimSpace(runID)
}
