// Package profileapi exposes the narrow LLM_Op HTTP boundary.
package profileapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	"github.com/rs/zerolog/log"

	"kyunghee-aiops/llmop-api/internal/applicationprofile"
	"kyunghee-aiops/llmop-api/internal/llmclient"
	"kyunghee-aiops/llmop-api/internal/llmselection"
)

const (
	pathHealthz             = "/healthz"
	pathOpenAPI             = "/openapi.yaml"
	pathApplicationProfiles = "/api/v1/application-profiles"
	pathOpsLLMSelect        = "/api/v1/ops-llm/select"
)

type profileGenerator interface {
	Generate(
		ctx context.Context,
		request applicationprofile.GenerateRequest,
	) (applicationprofile.GenerateResult, error)
}

type handler struct {
	config    Config
	generator profileGenerator
	selector  llmselection.Selector
}

type ErrorResponse struct {
	Code    string `json:"code" validate:"required"`
	Message string `json:"message" validate:"required"`
}

func NewServer(config Config) *echo.Echo {
	generator := applicationprofile.NewGenerator(
		config.LLMCandidatesPath,
		config.CandidateID,
		llmclient.NewClient(nil),
	)
	if strings.TrimSpace(config.OpsLLMBenchmarkPath) != "" {
		generator.Selector = llmselection.NewSelector(config.OpsLLMBenchmarkPath)
	}
	if config.MaxLLMAttempts > 0 {
		generator.MaxLLMAttempts = config.MaxLLMAttempts
	}
	return newServer(config, generator)
}

func newServer(config Config, generator profileGenerator) *echo.Echo {
	handler := handler{
		config:    config,
		generator: generator,
		selector:  llmselection.NewSelector(config.OpsLLMBenchmarkPath),
	}
	server := echo.New()
	server.HideBanner = true
	server.HidePort = true
	server.Use(middleware.Recover())
	server.Use(middleware.RequestLoggerWithConfig(middleware.RequestLoggerConfig{
		LogMethod:  true,
		LogURI:     true,
		LogStatus:  true,
		LogLatency: true,
		LogError:   true,
		LogValuesFunc: func(_ echo.Context, values middleware.RequestLoggerValues) error {
			event := log.Info()
			if values.Error != nil || values.Status >= http.StatusInternalServerError {
				event = log.Error().Err(values.Error)
			}
			event.
				Str("method", values.Method).
				Str("uri", values.URI).
				Int("status", values.Status).
				Dur("latency", values.Latency).
				Msg("llmop-api request")
			return nil
		},
	}))

	server.GET(pathHealthz, handler.getHealthz)
	server.GET(pathOpenAPI, handler.getOpenAPI)
	server.POST(pathApplicationProfiles, handler.postApplicationProfile)
	server.POST(pathOpsLLMSelect, handler.postOpsLLMSelect)
	return server
}

// postOpsLLMSelect godoc
// @ID SelectOpsLLM
// @Summary Rank and select an Ops LLM candidate
// @Description Rank configured Ops LLM candidates with a weighted policy. benchmark_status states whether the source metrics came from an executed benchmark; a not_executed result is a policy baseline, not a measured model-quality claim. This endpoint does not invoke the selected model or perform deployment work.
// @Tags Ops LLM Selection
// @Accept json
// @Produce json
// @Param request body llmselection.SelectRequest true "Ops LLM selection policy"
// @Success 200 {object} llmselection.SelectionResponse
// @Failure 400 {object} ErrorResponse
// @Failure 413 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /api/v1/ops-llm/select [post]
func (handler handler) postOpsLLMSelect(context echo.Context) error {
	request := llmselection.SelectRequest{}
	if err := decodeRequest(context, handler.config.MaxRequestBytes, &request); err != nil {
		var maxBytesError *http.MaxBytesError
		if errors.As(err, &maxBytesError) {
			return context.JSON(http.StatusRequestEntityTooLarge, ErrorResponse{
				Code:    "REQUEST_TOO_LARGE",
				Message: "Request body exceeds the configured limit.",
			})
		}
		return context.JSON(http.StatusBadRequest, ErrorResponse{
			Code:    "INVALID_REQUEST",
			Message: "Request body must be one valid JSON object with only supported fields.",
		})
	}
	result, err := handler.selector.Select(context.Request().Context(), request.Policy)
	if err == nil {
		return context.JSON(http.StatusOK, result)
	}
	log.Error().Err(err).Msg("Ops LLM selection failed")
	if errors.Is(err, llmselection.ErrUnknownPolicy) {
		return context.JSON(http.StatusBadRequest, ErrorResponse{
			Code:    "UNKNOWN_POLICY",
			Message: "The requested Ops LLM selection policy is not configured.",
		})
	}
	return context.JSON(http.StatusInternalServerError, ErrorResponse{
		Code:    "SELECTION_UNAVAILABLE",
		Message: "Ops LLM ranking could not be loaded.",
	})
}

// getHealthz godoc
// @ID GetLLMOpHealthz
// @Summary Check LLM_Op API health
// @Description Return process readiness and the bounded LLM_Op role.
// @Tags LLM_Op
// @Produce json
// @Success 200 {object} map[string]string
// @Router /healthz [get]
func (handler handler) getHealthz(context echo.Context) error {
	return context.JSON(http.StatusOK, map[string]string{
		"status":  "ok",
		"service": "llmop-api",
		"role":    "application_profile_generation_and_llm_selection",
	})
}

// getOpenAPI godoc
// @ID GetLLMOpOpenAPI
// @Summary Get the LLM_Op OpenAPI specification
// @Tags LLM_Op
// @Produce text/yaml
// @Success 200 {file} file
// @Failure 500 {object} ErrorResponse
// @Router /openapi.yaml [get]
func (handler handler) getOpenAPI(context echo.Context) error {
	return context.File(handler.config.OpenAPIPath)
}

// postApplicationProfile godoc
// @ID GenerateApplicationProfile
// @Summary Generate an ApplicationProfile
// @Description Validate natural-language or structured App Spec input and return a bounded ApplicationProfile. Natural-language analysis selects an LLM by policy, retries invalid output, and labels bounded local-rule fallback; structured input is converted without an LLM. This endpoint does not recommend infrastructure, select an Agent, create a manifest, submit a deployment, or process runtime feedback.
// @Tags Application Profiles
// @Accept json
// @Produce json
// @Param request body applicationprofile.GenerateRequest true "Natural-language application requirements"
// @Success 201 {object} applicationprofile.GenerateResult
// @Failure 400 {object} ErrorResponse
// @Failure 413 {object} ErrorResponse
// @Failure 502 {object} ErrorResponse
// @Failure 503 {object} ErrorResponse
// @Router /api/v1/application-profiles [post]
func (handler handler) postApplicationProfile(context echo.Context) error {
	request := applicationprofile.GenerateRequest{}
	if err := decodeRequest(context, handler.config.MaxRequestBytes, &request); err != nil {
		var maxBytesError *http.MaxBytesError
		if errors.As(err, &maxBytesError) {
			return context.JSON(http.StatusRequestEntityTooLarge, ErrorResponse{
				Code:    "REQUEST_TOO_LARGE",
				Message: "Request body exceeds the configured limit.",
			})
		}
		return context.JSON(http.StatusBadRequest, ErrorResponse{
			Code:    "INVALID_REQUEST",
			Message: "Request body must be one valid JSON object with only supported fields.",
		})
	}
	result, err := handler.generator.Generate(context.Request().Context(), request)
	if err == nil {
		return context.JSON(http.StatusCreated, result)
	}
	log.Error().Err(err).Msg("ApplicationProfile generation failed")
	switch {
	case errors.Is(err, applicationprofile.ErrInvalidRequest):
		return context.JSON(http.StatusBadRequest, ErrorResponse{
			Code:    "INVALID_REQUEST",
			Message: "Natural-language application requirements are invalid.",
		})
	case errors.Is(err, applicationprofile.ErrModelUnavailable):
		return context.JSON(http.StatusServiceUnavailable, ErrorResponse{
			Code:    "MODEL_UNAVAILABLE",
			Message: "The configured ApplicationProfile model is unavailable.",
		})
	case errors.Is(err, applicationprofile.ErrInvalidModelOutput):
		return context.JSON(http.StatusBadGateway, ErrorResponse{
			Code:    "INVALID_MODEL_OUTPUT",
			Message: "The model did not produce a valid ApplicationProfile.",
		})
	default:
		return context.JSON(http.StatusInternalServerError, ErrorResponse{
			Code:    "INTERNAL_ERROR",
			Message: "ApplicationProfile generation could not be completed.",
		})
	}
}

func decodeRequest(context echo.Context, maxBytes int64, destination any) error {
	request := context.Request()
	request.Body = http.MaxBytesReader(context.Response(), request.Body, maxBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return fmt.Errorf("multiple JSON values are not allowed")
		}
		return err
	}
	if strings.TrimSpace(request.Header.Get(echo.HeaderContentType)) != "" &&
		!strings.HasPrefix(request.Header.Get(echo.HeaderContentType), echo.MIMEApplicationJSON) {
		return fmt.Errorf("content type must be application/json")
	}
	return nil
}
