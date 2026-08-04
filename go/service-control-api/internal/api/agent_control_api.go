package api

import (
	"errors"
	"fmt"
	"net/http"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

// RestPostApplicationAnalysisRequest godoc
// @ID PostApplicationAnalysisRequest
// @Summary Start the geon automation flow from a Common JSON analysis request
// @Description Accept application.analysis.request, preserve its correlation and trace identifiers, and execute requirement analysis, resource recommendation, Agent decision, and Go Guard exactly once per message_id.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body agentcontrol.ApplicationAnalysisRequestEnvelope true "Common JSON v1.0 application analysis request"
// @Success 201 {object} agentcontrol.AutomationRun
// @Success 200 {object} agentcontrol.AutomationRun "Idempotent replay"
// @Failure 400 {object} ErrorResponse
// @Failure 409 {object} ErrorResponse
// @Router /api/v1/agent-control/application-analysis-requests [post]
func (handler restHandler) RestPostApplicationAnalysisRequest(context echo.Context) error {
	var request agentcontrol.ApplicationAnalysisRequestEnvelope
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	run, replayed, err := handler.service.automationRunner.RunAnalysisRequest(
		context.Request().Context(),
		request,
	)
	if err != nil {
		if errors.Is(err, agentcontrol.ErrAnalysisRequestIdempotencyConflict) {
			return jsonError(
				context,
				http.StatusConflict,
				"message_id was already used for a different analysis request",
				err,
			)
		}
		return jsonError(
			context,
			http.StatusBadRequest,
			"Application analysis request could not be processed",
			err,
		)
	}
	if replayed {
		context.Response().Header().Set("Idempotent-Replayed", "true")
		return context.JSON(http.StatusOK, run)
	}
	return context.JSON(http.StatusCreated, run)
}

type AgentControlReasoningComparisonRequest struct {
	CandidateID string `json:"candidate_id" validate:"required"`
}

// RestPostAutomationRun godoc
// @ID PostAgentControlAutomationRun
// @Summary Run requirement analysis, resource recommendation, and Agent decision
// @Description Accept one natural-language request or structured App Spec, resolve the selected or default eligible decision Agent from Agent Registry, dispatch its DEPLOY/REJECT/RETRY decision, and validate the result with external Go Guards before producing a DesiredDeploymentSpec.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body agentcontrol.AutomationRunInput true "One-shot automation request"
// @Success 201 {object} agentcontrol.AutomationRun
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/automation-runs [post]
func (handler restHandler) RestPostAutomationRun(context echo.Context) error {
	var request agentcontrol.AutomationRunInput
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	run, err := handler.service.automationRunner.Run(context.Request().Context(), request)
	if err != nil {
		return jsonError(
			context,
			http.StatusBadRequest,
			"Automatic Agent Control flow could not be completed",
			err,
		)
	}
	return context.JSON(http.StatusCreated, run)
}

// RestGetAutomationRun godoc
// @ID GetAgentControlAutomationRun
// @Summary Get one automatic three-stage Agent run
// @Description Return requirement analysis, resource recommendation, Agent decision, Guard evidence, and Desired Deployment Spec for one run_id.
// @Tags AI Application Automation Agent
// @Produce json
// @Param run_id path string true "Automation run ID"
// @Success 200 {object} agentcontrol.AutomationRun
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/agent-control/automation-runs/{run_id} [get]
func (handler restHandler) RestGetAutomationRun(context echo.Context) error {
	runID := context.Param("run_id")
	run, ok := handler.service.automationRunner.Get(runID)
	if !ok {
		return jsonError(
			context,
			http.StatusNotFound,
			"Automation run was not found",
			fmt.Errorf("run_id %q was not found", runID),
		)
	}
	return context.JSON(http.StatusOK, run)
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

// RestDeleteAgentControlFlows godoc
// @ID DeleteAgentControlFlows
// @Summary Delete all generated Agent Control flows
// @Description Clear the in-memory Agent Control experiment records.
// @Tags AI Application Automation Agent
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/agent-control/flows [delete]
func (handler restHandler) RestDeleteAgentControlFlows(context echo.Context) error {
	deleted := handler.service.agentControl.ClearFlows()
	return context.JSON(http.StatusOK, map[string]any{
		"deleted": deleted,
		"flows":   []agentcontrol.Flow{},
	})
}

// RestDeleteAgentControlFlow godoc
// @ID DeleteAgentControlFlow
// @Summary Delete one generated Agent Control flow
// @Description Delete one in-memory Agent Control experiment record by correlation_id.
// @Tags AI Application Automation Agent
// @Produce json
// @Param correlation_id path string true "Common JSON correlation ID"
// @Success 200 {object} map[string]interface{}
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/agent-control/flows/{correlation_id} [delete]
func (handler restHandler) RestDeleteAgentControlFlow(context echo.Context) error {
	correlationID := context.Param("correlation_id")
	flow, ok := handler.service.agentControl.DeleteFlow(correlationID)
	if !ok {
		return jsonError(
			context,
			http.StatusNotFound,
			"Agent Control flow was not found",
			fmt.Errorf("correlation_id %q was not found", correlationID),
		)
	}
	return context.JSON(http.StatusOK, map[string]any{
		"deleted":        1,
		"correlation_id": flow.CorrelationID,
	})
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
