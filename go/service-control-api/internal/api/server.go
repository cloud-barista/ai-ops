package api

import (
	"errors"
	"net/http"

	playgroundvalidator "github.com/go-playground/validator/v10"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	"github.com/rs/zerolog/log"

	"kyunghee-aiops/service-control-api/internal/webui"
)

const (
	pathHealthz        = "/healthz"
	pathOpenAPI        = "/openapi.yaml"
	pathAgents         = "/api/v1/agents"
	pathOpsLLMSelect   = "/api/v1/ops-llm/select"
	pathVMSuitability  = "/api/v1/apps/vm-suitability"
	pathDeploymentPlan = "/api/v1/apps/deployment-plan"
	pathAutomationPlan = "/api/v1/automation/action-proposals"
	pathAutomationFeed = "/api/v1/automation/feedback"
	pathAutonomy       = "/api/v1/autonomy"
	pathControlRuns    = "/api/v1/control-runs"
	pathPlannerDeploy  = "/api/v1/planner/deployments"
	pathServiceOpsRun  = "/api/v1/service-operations/run"
)

type restHandler struct {
	config  ServerConfig
	service Service
}

type requestValidator struct {
	validator *playgroundvalidator.Validate
}

func (validator requestValidator) Validate(value any) error {
	return validator.validator.Struct(value)
}

func NewServer(config ServerConfig) *echo.Echo {
	handler := restHandler{
		config:  config,
		service: NewService(config),
	}

	server := echo.New()
	server.HideBanner = true
	server.HidePort = true
	server.Validator = requestValidator{validator: playgroundvalidator.New()}
	server.Use(middleware.Recover())
	server.Use(middleware.RequestLoggerWithConfig(middleware.RequestLoggerConfig{
		LogMethod:  true,
		LogURI:     true,
		LogStatus:  true,
		LogLatency: true,
		LogError:   true,
		LogValuesFunc: func(context echo.Context, values middleware.RequestLoggerValues) error {
			event := log.Info()
			if values.Error != nil || values.Status >= http.StatusInternalServerError {
				event = log.Error().Err(values.Error)
			}
			event.
				Str("method", values.Method).
				Str("uri", values.URI).
				Int("status", values.Status).
				Dur("latency", values.Latency).
				Msg("service-control-api request")
			return nil
		},
	}))
	webui.Register(server)

	server.GET(pathHealthz, handler.RestGetHealthz)
	server.GET(pathOpenAPI, handler.RestGetOpenAPI)
	server.GET(pathAgents, handler.RestGetAgents)
	server.POST(pathAgents, handler.RestPostAgent)
	server.GET(pathAgents+"/:name", handler.RestGetAgent)
	server.DELETE(pathAgents+"/:name", handler.requireAutonomyAdmin(handler.RestDeleteAgent))
	server.POST(pathAgents+"/:name/actions/:action/validate", handler.RestPostAgentActionValidate)
	server.POST(pathAgents+"/:name/invocations/plan", handler.RestPostAgentInvocationPlan)
	server.POST(pathOpsLLMSelect, handler.RestPostOpsLLMSelect)
	server.POST(pathVMSuitability, handler.RestPostVMSuitability)
	server.POST(pathDeploymentPlan, handler.RestPostDeploymentPlan)
	server.POST(pathAutomationPlan, handler.RestPostLLMAutomationAction)
	server.POST(pathAutomationFeed, handler.RestPostAutomationFeedback)
	server.GET(pathAutomationFeed, handler.RestGetAutomationFeedback)
	server.DELETE(pathAutomationFeed+"/:correlation_id", handler.requireAutonomyAdmin(handler.RestDeleteAutomationFeedback))
	server.DELETE(pathAutomationFeed, handler.requireAutonomyAdmin(handler.RestDeleteAllAutomationFeedback))
	server.POST(pathControlRuns, handler.RestPostControlRun)
	server.POST(pathControlRuns+"/:run_id/submit", handler.RestPostControlRunSubmit)
	server.GET(pathControlRuns, handler.RestGetControlRuns)
	server.GET(pathControlRuns+"/:run_id", handler.RestGetControlRun)
	server.DELETE(pathControlRuns+"/:run_id", handler.requireAutonomyAdmin(handler.RestDeleteControlRun))
	server.DELETE(pathControlRuns, handler.requireAutonomyAdmin(handler.RestDeleteControlRuns))
	server.GET(pathAutonomy+"/status", handler.RestGetAutonomyStatus)
	server.PUT(pathAutonomy+"/config", handler.requireAutonomyAdmin(handler.RestPutAutonomyConfig))
	server.POST(pathAutonomy+"/start", handler.requireAutonomyAdmin(handler.RestPostAutonomyStart))
	server.POST(pathAutonomy+"/stop", handler.requireAutonomyAdmin(handler.RestPostAutonomyStop))
	server.POST(pathAutonomy+"/emergency-stop", handler.requireAutonomyAdmin(handler.RestPostAutonomyEmergencyStop))
	server.POST(pathAutonomy+"/cycles", handler.requireAutonomyAdmin(handler.RestPostAutonomyCycle))
	server.GET(pathAutonomy+"/events", handler.RestGetAutonomyEvents)
	server.DELETE(pathAutonomy+"/events/:sequence", handler.requireAutonomyAdmin(handler.RestDeleteAutonomyEvent))
	server.DELETE(pathAutonomy+"/events", handler.requireAutonomyAdmin(handler.RestDeleteAutonomyEvents))
	server.POST(pathPlannerDeploy, handler.RestPostAppDeployPlanner)
	server.POST(pathServiceOpsRun, handler.RestPostServiceOperationsRun)

	return server
}

// RestPostAppDeployPlanner godoc
// @ID PostAppDeployPlanner
// @Summary Plan and submit an AI application deployment
// @Description Validate the request with Go Request Guard, use an actual configured LLM to generate an AppDeploy DeploymentManifest, validate it with Go Manifest Guard, submit it to AppDeploy, and poll the deployment status. AppDeploy retains target selection and execution responsibility.
// @Tags AI Application Automation
// @Accept json
// @Produce json
// @Param request body AppDeployPlannerRequest true "Planner request"
// @Success 200 {object} map[string]interface{}
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/planner/deployments [post]
func (handler restHandler) RestPostAppDeployPlanner(context echo.Context) error {
	var request AppDeployPlannerRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.RunAppDeployPlanner(context.Request().Context(), request)
	if err != nil {
		if result.Status == "REQUEST_REJECTED" {
			return context.JSON(http.StatusBadRequest, result)
		}
		return jsonError(context, http.StatusBadRequest, "Deployment planner request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestGetHealthz godoc
// @ID GetHealthz
// @Summary Check service-control API health
// @Description Return a lightweight readiness response for the Go service-control API.
// @Tags Service Control
// @Produce json
// @Success 200 {object} map[string]string
// @Router /healthz [get]
func (handler restHandler) RestGetHealthz(context echo.Context) error {
	return context.JSON(http.StatusOK, map[string]string{
		"status":  "ok",
		"service": "service-control-api",
	})
}

// RestGetOpenAPI godoc
// @ID GetOpenAPI
// @Summary Get OpenAPI specification
// @Description Return the submission OpenAPI YAML for the service-control prototype.
// @Tags Service Control
// @Produce text/yaml
// @Success 200 {file} file
// @Failure 500 {object} ErrorResponse
// @Router /openapi.yaml [get]
func (handler restHandler) RestGetOpenAPI(context echo.Context) error {
	return context.File(handler.config.OpenAPIPath)
}

// RestGetAgents godoc
// @ID GetAgents
// @Summary List registered AI operation agents
// @Description Return the configured bounded-action agent registry.
// @Tags Agent Registry
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Failure 500 {object} ErrorResponse
// @Router /api/v1/agents [get]
func (handler restHandler) RestGetAgents(context echo.Context) error {
	result, err := handler.service.ListAgents(context.Request().Context())
	if err != nil {
		return jsonError(context, http.StatusInternalServerError, "Agent registry is unavailable", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostAgent godoc
// @ID PostAgent
// @Summary Register an external AI operation agent
// @Description Register an external agent endpoint, capabilities, and bounded actions in process memory.
// @Tags Agent Registry
// @Accept json
// @Produce json
// @Param request body ExternalAgentRegistrationRequest true "External agent registration"
// @Success 201 {object} AgentProfile
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agents [post]
func (handler restHandler) RestPostAgent(context echo.Context) error {
	var request ExternalAgentRegistrationRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.RegisterExternalAgent(context.Request().Context(), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Agent registration could not be processed", err)
	}
	return context.JSON(http.StatusCreated, result)
}

// RestGetAgent godoc
// @ID GetAgent
// @Summary Get a registered AI operation agent
// @Tags Agent Registry
// @Produce json
// @Param name path string true "Agent name"
// @Success 200 {object} AgentProfile
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/agents/{name} [get]
func (handler restHandler) RestGetAgent(context echo.Context) error {
	result, err := handler.service.ShowAgent(context.Request().Context(), context.Param("name"))
	if err != nil {
		return jsonError(context, http.StatusNotFound, "Agent was not found", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestDeleteAgent godoc
// @ID DeleteAgent
// @Summary Delete a runtime AI operation agent
// @Description Delete only an Agent registered in process memory. Configuration-backed Agents are protected.
// @Tags Agent Registry
// @Produce json
// @Param name path string true "Agent name"
// @Success 200 {object} map[string]interface{}
// @Failure 401 {object} ErrorResponse
// @Failure 403 {object} ErrorResponse
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/agents/{name} [delete]
func (handler restHandler) RestDeleteAgent(context echo.Context) error {
	agent, err := handler.service.DeleteExternalAgent(context.Request().Context(), context.Param("name"))
	if err != nil {
		switch {
		case errors.Is(err, errConfiguredAgentProtected):
			return jsonError(context, http.StatusForbidden, "Configuration Agent is protected", err)
		case errors.Is(err, errRuntimeAgentNotFound):
			return jsonError(context, http.StatusNotFound, "Runtime Agent was not found", err)
		default:
			return jsonError(context, http.StatusInternalServerError, "Runtime Agent deletion failed", err)
		}
	}
	return context.JSON(http.StatusOK, map[string]any{
		"deleted": true,
		"name":    agent.Name,
		"source":  agent.Source,
	})
}

// RestPostAgentActionValidate godoc
// @ID PostAgentActionValidate
// @Summary Validate an action against an agent boundary
// @Tags Agent Registry
// @Produce json
// @Param name path string true "Agent name"
// @Param action path string true "Action identifier"
// @Success 200 {object} map[string]interface{}
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/agents/{name}/actions/{action}/validate [post]
func (handler restHandler) RestPostAgentActionValidate(context echo.Context) error {
	name := context.Param("name")
	action := context.Param("action")
	valid, err := handler.service.ValidateAgentAction(context.Request().Context(), name, action)
	if err != nil {
		return jsonError(context, http.StatusNotFound, "Agent was not found", err)
	}
	return context.JSON(http.StatusOK, map[string]any{
		"agent":  name,
		"action": action,
		"valid":  valid,
	})
}

// RestPostAgentInvocationPlan godoc
// @ID PostAgentInvocationPlan
// @Summary Build a validated external-agent invocation plan
// @Description Validate the registered capability and bounded action, then return a non-executing invocation plan.
// @Tags Agent Registry
// @Accept json
// @Produce json
// @Param name path string true "Agent name"
// @Param request body AgentInvocationPlanRequest true "Invocation plan request"
// @Success 200 {object} AgentInvocationPlan
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agents/{name}/invocations/plan [post]
func (handler restHandler) RestPostAgentInvocationPlan(context echo.Context) error {
	var request AgentInvocationPlanRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.BuildAgentInvocationPlan(context.Request().Context(), context.Param("name"), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Agent invocation plan could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostOpsLLMSelect godoc
// @ID PostOpsLLMSelect
// @Summary Select an Ops LLM policy candidate
// @Description Rank Ops LLM candidates using the configured service-control benchmark policy.
// @Tags LLM Operation Management
// @Accept json
// @Produce json
// @Param request body OpsLLMSelectRequest true "Ops LLM selection request"
// @Success 200 {object} OpsLLMSelectionResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/ops-llm/select [post]
func (handler restHandler) RestPostOpsLLMSelect(context echo.Context) error {
	var request OpsLLMSelectRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.SelectOpsLLM(context.Request().Context(), request.Policy)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "LLM selection request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostVMSuitability godoc
// @ID PostVMSuitability
// @Summary Validate a provided CPU/GPU VM
// @Description Validate whether an infrastructure-supplied VM snapshot satisfies declared workload requirements without selecting or provisioning a VM.
// @Tags AI Application Deployment
// @Accept json
// @Produce json
// @Param request body VMCompatibilityRequest true "VM compatibility request"
// @Success 200 {object} VMCompatibilityResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/apps/vm-suitability [post]
func (handler restHandler) RestPostVMSuitability(context echo.Context) error {
	var request VMCompatibilityRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.ValidateVMSuitability(context.Request().Context(), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "VM suitability request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostDeploymentPlan godoc
// @ID PostDeploymentPlan
// @Summary Build an AI application deployment plan
// @Description Build a non-executing, capability-based handoff plan for any registered external execution agent.
// @Tags AI Application Deployment
// @Accept json
// @Produce json
// @Param request body VMCompatibilityRequest true "VM compatibility request"
// @Success 200 {object} DeploymentPlanResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/apps/deployment-plan [post]
func (handler restHandler) RestPostDeploymentPlan(context echo.Context) error {
	var request VMCompatibilityRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.BuildDeploymentPlan(context.Request().Context(), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Deployment plan request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostLLMAutomationAction godoc
// @ID PostLLMAutomationAction
// @Summary Generate and validate an LLM-based automation Action
// @Description Call an actual configured LLM candidate, validate its bounded Action against actual VM evidence and Agent Registry policy, and return a non-executing external-agent handoff.
// @Tags AI Application Automation
// @Accept json
// @Produce json
// @Param request body LLMAutomationActionRequest true "LLM automation Action request"
// @Success 200 {object} LLMAutomationActionResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/automation/action-proposals [post]
func (handler restHandler) RestPostLLMAutomationAction(context echo.Context) error {
	var request LLMAutomationActionRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.PlanLLMAutomationAction(context.Request().Context(), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "LLM automation Action request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostAutomationFeedback godoc
// @ID PostAutomationFeedback
// @Summary Record external automation execution feedback
// @Description Record normalized status and optional performance evidence for a previously approved correlation ID. Credentials and arbitrary secret fields are not accepted.
// @Tags AI Application Automation
// @Accept json
// @Produce json
// @Param request body AutomationFeedbackRequest true "Automation execution feedback"
// @Success 200 {object} AutomationFeedbackRecord
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/automation/feedback [post]
func (handler restHandler) RestPostAutomationFeedback(context echo.Context) error {
	var request AutomationFeedbackRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.RecordAutomationFeedback(context.Request().Context(), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Automation feedback could not be recorded", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestGetAutomationFeedback godoc
// @ID GetAutomationFeedback
// @Summary List automation execution feedback
// @Description Return normalized execution feedback records held by the current geon process, newest first.
// @Tags AI Application Automation
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Failure 500 {object} ErrorResponse
// @Router /api/v1/automation/feedback [get]
func (handler restHandler) RestGetAutomationFeedback(context echo.Context) error {
	records, err := handler.service.ListAutomationFeedback(context.Request().Context())
	if err != nil {
		return jsonError(context, http.StatusInternalServerError, "Automation feedback could not be listed", err)
	}
	return context.JSON(http.StatusOK, map[string]any{"count": len(records), "feedback": records})
}

// RestDeleteAutomationFeedback godoc
// @ID DeleteAutomationFeedback
// @Summary Delete one automation execution feedback record
// @Description Delete one geon-owned feedback record while preserving its approved correlation registration.
// @Tags AI Application Automation
// @Produce json
// @Param correlation_id path string true "Automation correlation ID"
// @Success 200 {object} map[string]interface{}
// @Failure 401 {object} ErrorResponse
// @Failure 403 {object} ErrorResponse
// @Failure 404 {object} ErrorResponse
// @Router /api/v1/automation/feedback/{correlation_id} [delete]
func (handler restHandler) RestDeleteAutomationFeedback(context echo.Context) error {
	record, err := handler.service.DeleteAutomationFeedback(context.Request().Context(), context.Param("correlation_id"))
	if errors.Is(err, errAutomationFeedbackNotFound) {
		return context.JSON(http.StatusNotFound, ErrorResponse{Valid: false, Message: "Automation feedback was not found"})
	}
	if err != nil {
		return jsonError(context, http.StatusInternalServerError, "Automation feedback could not be deleted", err)
	}
	return context.JSON(http.StatusOK, map[string]any{"deleted": true, "feedback": record})
}

// RestDeleteAllAutomationFeedback godoc
// @ID DeleteAllAutomationFeedback
// @Summary Clear automation execution feedback
// @Description Clear geon-owned feedback records while preserving approved correlation registrations.
// @Tags AI Application Automation
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Failure 401 {object} ErrorResponse
// @Failure 403 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /api/v1/automation/feedback [delete]
func (handler restHandler) RestDeleteAllAutomationFeedback(context echo.Context) error {
	deleted, err := handler.service.ClearAutomationFeedback(context.Request().Context())
	if err != nil {
		return jsonError(context, http.StatusInternalServerError, "Automation feedback could not be cleared", err)
	}
	return context.JSON(http.StatusOK, map[string]any{"deleted": true, "deleted_count": deleted, "feedback": []AutomationFeedbackRecord{}})
}

// RestPostServiceOperationsRun godoc
// @ID PostServiceOperationsRun
// @Summary Run service-control operation planning
// @Description Execute the Go service-control flow across LLM selection, agent checks, actual VM suitability validation, and generic deployment-control handoff planning.
// @Tags Service Control
// @Accept json
// @Produce json
// @Param request body ServiceOperationsRequest true "Service operation request"
// @Success 200 {object} ServiceOperationsResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/service-operations/run [post]
func (handler restHandler) RestPostServiceOperationsRun(context echo.Context) error {
	var request ServiceOperationsRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.RunServiceOperations(context.Request().Context(), request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Service operation request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

func bindAndValidate(context echo.Context, request any) (string, error) {
	if err := context.Bind(request); err != nil {
		return "Malformed request body: check JSON syntax", err
	}
	if err := context.Validate(request); err != nil {
		return "Required request field is missing", err
	}
	return "", nil
}

func jsonError(context echo.Context, status int, message string, err error) error {
	log.Error().
		Err(err).
		Str("method", context.Request().Method).
		Str("path", context.Path()).
		Int("status", status).
		Msg(message)
	return context.JSON(status, ErrorResponse{
		Valid:   false,
		Message: message,
	})
}
