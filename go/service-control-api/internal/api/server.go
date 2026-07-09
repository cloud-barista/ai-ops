package api

import (
	"net/http"

	playgroundvalidator "github.com/go-playground/validator/v10"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	"github.com/rs/zerolog/log"
)

const (
	pathHealthz        = "/healthz"
	pathOpenAPI        = "/openapi.yaml"
	pathAgents         = "/api/v1/agents"
	pathOpsLLMSelect   = "/api/v1/ops-llm/select"
	pathAppPlacement   = "/api/v1/apps/placement"
	pathDeploymentPlan = "/api/v1/apps/deployment-plan"
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

	server.GET(pathHealthz, handler.RestGetHealthz)
	server.GET(pathOpenAPI, handler.RestGetOpenAPI)
	server.GET(pathAgents, handler.RestGetAgents)
	server.POST(pathOpsLLMSelect, handler.RestPostOpsLLMSelect)
	server.POST(pathAppPlacement, handler.RestPostAppPlacement)
	server.POST(pathDeploymentPlan, handler.RestPostDeploymentPlan)
	server.POST(pathServiceOpsRun, handler.RestPostServiceOperationsRun)

	return server
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

// RestPostAppPlacement godoc
// @ID PostAppPlacement
// @Summary Recommend CPU/GPU VM placement
// @Description Recommend a CPU/GPU VM target for an AI application workload.
// @Tags AI Application Deployment
// @Accept json
// @Produce json
// @Param request body WorkloadRequest true "Inference workload request"
// @Success 200 {object} PlacementResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/apps/placement [post]
func (handler restHandler) RestPostAppPlacement(context echo.Context) error {
	var request WorkloadRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.RecommendPlacement(context.Request().Context(), request.Workload)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Placement request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostDeploymentPlan godoc
// @ID PostDeploymentPlan
// @Summary Build an AI application deployment plan
// @Description Build a Kubernetes-oriented deployment and control plan for the selected workload.
// @Tags AI Application Deployment
// @Accept json
// @Produce json
// @Param request body WorkloadRequest true "Inference workload request"
// @Success 200 {object} DeploymentPlanResponse
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/apps/deployment-plan [post]
func (handler restHandler) RestPostDeploymentPlan(context echo.Context) error {
	var request WorkloadRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.BuildDeploymentPlan(context.Request().Context(), request.Workload)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Deployment plan request could not be processed", err)
	}
	return context.JSON(http.StatusOK, result)
}

// RestPostServiceOperationsRun godoc
// @ID PostServiceOperationsRun
// @Summary Run service-control operation planning
// @Description Execute the Go service-control flow across LLM selection, agent checks, CPU/GPU placement, and deployment-control planning.
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
