package api

import (
	"fmt"
	"net/http"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

type AgentControlReasoningComparisonRequest struct {
	CandidateID string `json:"candidate_id" validate:"required"`
}

// RestPostApplicationContext godoc
// @ID PostAgentControlApplicationContext
// @Summary Receive an Application Context message
// @Description Validate and store a Common JSON v1.0 application.context.created message.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body agentcontrol.ApplicationContextEnvelope true "Application Context message"
// @Success 202 {object} agentcontrol.Flow
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/application-contexts [post]
func (handler restHandler) RestPostApplicationContext(context echo.Context) error {
	var request agentcontrol.ApplicationContextEnvelope
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	flow, err := handler.service.agentControl.ReceiveApplicationContext(
		context.Request().Context(),
		request,
	)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Application Context could not be accepted", err)
	}
	return context.JSON(http.StatusAccepted, flow)
}

// RestPostResourceRecommendation godoc
// @ID PostAgentControlResourceRecommendation
// @Summary Receive a Resource Recommendation message
// @Description Validate and store a Common JSON v1.0 resource.recommendation.created message.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body agentcontrol.ResourceRecommendationEnvelope true "Resource Recommendation message"
// @Success 202 {object} agentcontrol.Flow
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/resource-recommendations [post]
func (handler restHandler) RestPostResourceRecommendation(context echo.Context) error {
	var request agentcontrol.ResourceRecommendationEnvelope
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	flow, err := handler.service.agentControl.ReceiveResourceRecommendation(
		context.Request().Context(),
		request,
	)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Resource Recommendation could not be accepted", err)
	}
	return context.JSON(http.StatusAccepted, flow)
}

// RestPostDeploymentStatus godoc
// @ID PostAgentControlDeploymentStatus
// @Summary Receive a deployment status message
// @Description Validate and attach a Common JSON v1.0 deployment.status.changed message to an Agent Control flow.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body agentcontrol.DeploymentStatusEnvelope true "Deployment status message"
// @Success 202 {object} agentcontrol.Flow
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/deployment-status [post]
func (handler restHandler) RestPostDeploymentStatus(context echo.Context) error {
	var request agentcontrol.DeploymentStatusEnvelope
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	flow, err := handler.service.agentControl.ReceiveDeploymentStatus(
		context.Request().Context(),
		request,
	)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Deployment status could not be accepted", err)
	}
	return context.JSON(http.StatusAccepted, flow)
}

// RestPostOptimizationFeedback godoc
// @ID PostAgentControlOptimizationFeedback
// @Summary Receive optimization feedback
// @Description Validate and attach a Common JSON v1.0 optimization.feedback.created message to an Agent Control flow.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body agentcontrol.OptimizationFeedbackEnvelope true "Optimization feedback message"
// @Success 202 {object} agentcontrol.Flow
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/optimization-feedback [post]
func (handler restHandler) RestPostOptimizationFeedback(context echo.Context) error {
	var request agentcontrol.OptimizationFeedbackEnvelope
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	flow, err := handler.service.agentControl.ReceiveOptimizationFeedback(
		context.Request().Context(),
		request,
	)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Optimization feedback could not be accepted", err)
	}
	return context.JSON(http.StatusAccepted, flow)
}

// RestGetAgentControlFlows godoc
// @ID GetAgentControlFlows
// @Summary List joined Agent Control inputs
// @Description Return Application Context and Resource Recommendation correlation flows.
// @Tags AI Application Automation Agent
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows [get]
func (handler restHandler) RestGetAgentControlFlows(context echo.Context) error {
	flows := handler.service.agentControl.ListFlows()
	return context.JSON(http.StatusOK, map[string]any{
		"count": len(flows),
		"flows": flows,
	})
}

// RestGetAgentControlFlow godoc
// @ID GetAgentControlFlow
// @Summary Get a joined Agent Control input
// @Description Return one correlation flow by correlation_id.
// @Tags AI Application Automation Agent
// @Produce json
// @Param correlation_id path string true "Common JSON correlation ID"
// @Success 200 {object} agentcontrol.Flow
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/agent-control/flows/{correlation_id} [get]
func (handler restHandler) RestGetAgentControlFlow(context echo.Context) error {
	correlationID := context.Param("correlation_id")
	flow, ok := handler.service.agentControl.GetFlow(correlationID)
	if !ok {
		return jsonError(
			context,
			http.StatusNotFound,
			"Agent Control flow was not found",
			fmt.Errorf("correlation_id %q was not found", correlationID),
		)
	}
	return context.JSON(http.StatusOK, flow)
}

// RestPostAgentControlReasoningComparison godoc
// @ID PostAgentControlReasoningComparison
// @Summary Compare simple and validated Qwen reasoning
// @Description Compare the deterministic rule-based decision, the raw Qwen proposal, and the same proposal after Go Guard validation.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param correlation_id path string true "Common JSON correlation ID"
// @Param request body AgentControlReasoningComparisonRequest true "Reasoning candidate"
// @Success 200 {object} agentcontrol.ReasoningComparison
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/flows/{correlation_id}/reasoning-comparisons [post]
func (handler restHandler) RestPostAgentControlReasoningComparison(context echo.Context) error {
	var request AgentControlReasoningComparisonRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	comparison, err := handler.service.agentControl.CompareReasoning(
		context.Request().Context(),
		context.Param("correlation_id"),
		request.CandidateID,
	)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Reasoning comparison could not be completed", err)
	}
	return context.JSON(http.StatusOK, comparison)
}
